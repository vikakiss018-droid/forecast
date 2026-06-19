"""Paper scalp trades: cooldowns, rate limits, JSONL logs."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import PROCESSED_DATA_DIR, ensure_directories
from .scalp_config import ScalpConfig
from .scalp_signals import ScalpSignal

SIGNALS_LOG = PROCESSED_DATA_DIR / "scalp_signals.jsonl"
TICKS_DIR = PROCESSED_DATA_DIR / "scalp_ticks"

_RECENT_SIGNALS: deque[dict[str, Any]] = deque(maxlen=80)
_SYMBOL_LAST_EMIT: dict[str, float] = {}
_HOURLY_EMITS: deque[float] = deque()
_DAILY_EMITS: deque[float] = deque()
_STATS = {"emitted": 0, "skipped_cooldown": 0, "skipped_rate": 0}


def _trim_emits(now: float) -> None:
    hour_ago = now - 3600.0
    day_ago = now - 86400.0
    while _HOURLY_EMITS and _HOURLY_EMITS[0] < hour_ago:
        _HOURLY_EMITS.popleft()
    while _DAILY_EMITS and _DAILY_EMITS[0] < day_ago:
        _DAILY_EMITS.popleft()


def can_emit_signal(symbol: str, cfg: ScalpConfig, *, now: float | None = None) -> tuple[bool, str]:
    now = now or time.time()
    _trim_emits(now)

    last = _SYMBOL_LAST_EMIT.get(symbol)
    if last is not None and now - last < cfg.cooldown_sec:
        _STATS["skipped_cooldown"] += 1
        return False, f"COOLDOWN:{cfg.cooldown_sec - (now - last):.0f}s"

    if len(_HOURLY_EMITS) >= cfg.max_trades_per_hour:
        _STATS["skipped_rate"] += 1
        return False, "HOURLY_CAP"

    if len(_DAILY_EMITS) >= cfg.max_trades_per_day:
        _STATS["skipped_rate"] += 1
        return False, "DAILY_CAP"

    return True, "OK"


@dataclass
class PaperTradePlan:
    tp_price: float
    sl_price: float
    tp_pct: float
    sl_pct: float
    time_stop_sec: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "time_stop_sec": self.time_stop_sec,
        }


def build_paper_plan(signal: ScalpSignal, cfg: ScalpConfig) -> PaperTradePlan:
    entry = signal.entry
    tp_pct = cfg.min_tp_pct / 100.0
    sl_pct = cfg.sl_pct / 100.0
    if signal.side == "long":
        return PaperTradePlan(
            tp_price=entry * (1.0 + tp_pct),
            sl_price=entry * (1.0 - sl_pct),
            tp_pct=cfg.min_tp_pct,
            sl_pct=cfg.sl_pct,
            time_stop_sec=cfg.time_stop_sec,
        )
    return PaperTradePlan(
        tp_price=entry * (1.0 - tp_pct),
        sl_price=entry * (1.0 + sl_pct),
        tp_pct=cfg.min_tp_pct,
        sl_pct=cfg.sl_pct,
        time_stop_sec=cfg.time_stop_sec,
    )


def emit_paper_signal(
    signal: ScalpSignal,
    cfg: ScalpConfig,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Log paper signal if rate limits allow. Returns record or None."""
    now = now or time.time()
    ok, reason = can_emit_signal(signal.symbol, cfg, now=now)
    if not ok:
        print(f"[scalp] skip emit {signal.symbol} ({reason})", flush=True)
        return None

    plan = build_paper_plan(signal, cfg)
    record: dict[str, Any] = {
        "ts": now,
        "ts_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "type": "paper_signal",
        "dry_run": cfg.dry_run,
        "signal": signal.to_dict(),
        "plan": plan.to_dict(),
    }

    ensure_directories()
    SIGNALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SIGNALS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    _RECENT_SIGNALS.appendleft(record)
    _SYMBOL_LAST_EMIT[signal.symbol] = now
    _HOURLY_EMITS.append(now)
    _DAILY_EMITS.append(now)
    _STATS["emitted"] += 1

    print(
        f"[scalp] PAPER {signal.side.upper()} {signal.symbol} "
        f"entry={signal.entry:.6f} obi={signal.obi_smooth:.3f} "
        f"persist={signal.persist_sec:.1f}s TP={plan.tp_pct}% SL={plan.sl_pct}%",
        flush=True,
    )
    return record


def append_tick_log(symbol: str, payload: dict[str, Any]) -> None:
    ensure_directories()
    TICKS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = TICKS_DIR / f"{day}_{symbol.replace('/', '_')}.jsonl"
    row = {"ts": time.time(), **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def get_scalp_trader_status(cfg: ScalpConfig) -> dict[str, Any]:
    now = time.time()
    _trim_emits(now)
    return {
        "dry_run": cfg.dry_run,
        "stats": dict(_STATS),
        "hourly_count": len(_HOURLY_EMITS),
        "daily_count": len(_DAILY_EMITS),
        "recent_signals": list(_RECENT_SIGNALS)[:20],
        "log_path": str(SIGNALS_LOG),
    }
