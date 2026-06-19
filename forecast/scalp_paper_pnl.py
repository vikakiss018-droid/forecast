"""Virtual scalp positions: TP / SL / time-stop and net P&L after fees."""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .paths import PROCESSED_DATA_DIR, ensure_directories
from .scalp_config import ScalpConfig
from .scalp_signals import ScalpSignal

CLOSED_LOG = PROCESSED_DATA_DIR / "scalp_paper_closed.jsonl"

Side = Literal["long", "short"]
CloseReason = Literal["TP", "SL", "TIME_STOP"]


@dataclass
class PaperPosition:
    id: str
    symbol: str
    side: Side
    entry: float
    tp_price: float
    sl_price: float
    opened_at: float
    time_stop_sec: int
    notional_usdt: float
    fee_pct: float

    def to_dict(self, *, mid: float | None = None) -> dict[str, Any]:
        now = time.time()
        upnl_pct: float | None = None
        upnl_usdt: float | None = None
        if mid is not None and self.entry > 0:
            gross = _gross_pct(self.side, self.entry, mid)
            upnl_pct = gross - self.fee_pct
            upnl_usdt = self.notional_usdt * upnl_pct / 100.0
        age = now - self.opened_at
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "opened_at": self.opened_at,
            "opened_iso": datetime.fromtimestamp(self.opened_at, tz=timezone.utc).isoformat(),
            "age_sec": round(age, 1),
            "time_left_sec": round(max(0.0, self.time_stop_sec - age), 1),
            "notional_usdt": self.notional_usdt,
            "mid": mid,
            "upnl_pct": round(upnl_pct, 4) if upnl_pct is not None else None,
            "upnl_usdt": round(upnl_usdt, 4) if upnl_usdt is not None else None,
        }


_OPEN: dict[str, PaperPosition] = {}
_CLOSED: deque[dict[str, Any]] = deque(maxlen=200)


def _gross_pct(side: Side, entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    if side == "long":
        return (price - entry) / entry * 100.0
    return (entry - price) / entry * 100.0


def _net_pct(gross_pct: float, fee_pct: float) -> float:
    return gross_pct - fee_pct


def _check_exit(pos: PaperPosition, mid: float, now: float) -> CloseReason | None:
    if pos.side == "long":
        if mid >= pos.tp_price:
            return "TP"
        if mid <= pos.sl_price:
            return "SL"
    else:
        if mid <= pos.tp_price:
            return "TP"
        if mid >= pos.sl_price:
            return "SL"
    if now - pos.opened_at >= pos.time_stop_sec:
        return "TIME_STOP"
    return None


def open_paper_position(
    signal: ScalpSignal,
    *,
    tp_price: float,
    sl_price: float,
    time_stop_sec: int,
    cfg: ScalpConfig,
    now: float | None = None,
) -> PaperPosition | None:
    """Open one virtual position per symbol (skip if already open)."""
    now = now or time.time()
    if signal.symbol in _OPEN:
        return None

    pos = PaperPosition(
        id=uuid.uuid4().hex[:12],
        symbol=signal.symbol,
        side=signal.side,
        entry=signal.entry,
        tp_price=tp_price,
        sl_price=sl_price,
        opened_at=now,
        time_stop_sec=time_stop_sec,
        notional_usdt=cfg.paper_notional_usdt,
        fee_pct=cfg.round_trip_fee_pct,
    )
    _OPEN[signal.symbol] = pos
    return pos


def _close_position(
    pos: PaperPosition,
    *,
    exit_price: float,
    reason: CloseReason,
    now: float,
) -> dict[str, Any]:
    gross = _gross_pct(pos.side, pos.entry, exit_price)
    net = _net_pct(gross, pos.fee_pct)
    pnl_usdt = pos.notional_usdt * net / 100.0
    hold_sec = now - pos.opened_at

    record: dict[str, Any] = {
        "ts": now,
        "ts_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "type": "paper_close",
        "id": pos.id,
        "symbol": pos.symbol,
        "side": pos.side,
        "entry": pos.entry,
        "exit": exit_price,
        "reason": reason,
        "hold_sec": round(hold_sec, 1),
        "gross_pct": round(gross, 4),
        "fee_pct": pos.fee_pct,
        "net_pct": round(net, 4),
        "notional_usdt": pos.notional_usdt,
        "pnl_usdt": round(pnl_usdt, 4),
        "win": net > 0,
    }

    ensure_directories()
    with CLOSED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    _CLOSED.appendleft(record)
    del _OPEN[pos.symbol]

    sign = "+" if pnl_usdt >= 0 else ""
    print(
        f"[scalp] CLOSE {reason} {pos.side.upper()} {pos.symbol} "
        f"entry={pos.entry:.6f} exit={exit_price:.6f} "
        f"net={sign}{net:.3f}% ({sign}{pnl_usdt:.2f} USDT)",
        flush=True,
    )
    return record


def update_paper_positions(
    symbol: str,
    mid: float,
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Check TP/SL/time-stop for open position on this symbol."""
    now = now or time.time()
    pos = _OPEN.get(symbol)
    if pos is None or mid <= 0:
        return []

    reason = _check_exit(pos, mid, now)
    if reason is None:
        return []

    return [_close_position(pos, exit_price=mid, reason=reason, now=now)]


def get_paper_pnl_status(cfg: ScalpConfig) -> dict[str, Any]:
    from .orderbook_layer import get_orderbook_metrics

    now = time.time()
    day_ago = now - 86400.0

    closed = list(_CLOSED)
    closed_24h = [c for c in closed if float(c.get("ts", 0)) >= day_ago]

    def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "total_pnl_usdt": 0.0,
                "avg_net_pct": 0.0,
                "tp": 0,
                "sl": 0,
                "time_stop": 0,
            }
        wins = sum(1 for r in rows if r.get("win"))
        total_pnl = sum(float(r.get("pnl_usdt") or 0) for r in rows)
        avg_net = sum(float(r.get("net_pct") or 0) for r in rows) / len(rows)
        return {
            "trades": len(rows),
            "wins": wins,
            "losses": len(rows) - wins,
            "win_rate_pct": round(100.0 * wins / len(rows), 1),
            "total_pnl_usdt": round(total_pnl, 2),
            "avg_net_pct": round(avg_net, 3),
            "tp": sum(1 for r in rows if r.get("reason") == "TP"),
            "sl": sum(1 for r in rows if r.get("reason") == "SL"),
            "time_stop": sum(1 for r in rows if r.get("reason") == "TIME_STOP"),
        }

    open_rows: list[dict[str, Any]] = []
    for sym, pos in _OPEN.items():
        m = get_orderbook_metrics(sym)
        mid = m.mid if m is not None and not m.stale and m.mid > 0 else None
        open_rows.append(pos.to_dict(mid=mid))

    return {
        "notional_usdt": cfg.paper_notional_usdt,
        "fee_pct": cfg.round_trip_fee_pct,
        "open_count": len(_OPEN),
        "open_positions": open_rows,
        "summary_24h": _agg(closed_24h),
        "summary_all": _agg(closed),
        "recent_closed": closed[:15],
        "closed_log": str(CLOSED_LOG),
    }
