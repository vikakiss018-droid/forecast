"""Скан лучших среднесрочных входов (горизонт неделя–месяц).

Инструменты: ликвидные USDT-spot majors/large-cap (не мем-микрокапы, не
leveraged, не bStocks). Логика: тренд на 4h + выравнивание с дневным HTF,
цель 2R (на 4h стопе это ~6–10% — типичный ход на 1–4 недели).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import ccxt

from .auto_trader import load_auto_trade_config
from .paths import CONFIGS_DIR, PROCESSED_DATA_DIR, ensure_directories
from .stocks_scanner import is_bstock_base
from .tf_backtest import _STABLE_OR_FIAT_BASES
from .trend_rules import PARAMS_SWING
from .trend_scanner import TrendScanConfig, scan_combined_setups

_log = logging.getLogger(__name__)

SWING_CACHE_PATH = PROCESSED_DATA_DIR / "swing_scan_latest.json"
SWING_PROGRESS_PATH = PROCESSED_DATA_DIR / "swing_scan_progress.json"

# Всегда в универсуме, даже если 24h volume чуть ниже порога
SWING_CORE_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "DOGE/USDT",
    "DOT/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "ATOM/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "SUI/USDT",
    "TON/USDT",
    "TRX/USDT",
    "UNI/USDT",
    "AAVE/USDT",
    "ARB/USDT",
    "OP/USDT",
    "INJ/USDT",
    "FIL/USDT",
    "RENDER/USDT",
    "TAO/USDT",
    "BCH/USDT",
    "XLM/USDT",
    "HBAR/USDT",
    "ICP/USDT",
    "ONDO/USDT",
)

_SWING_STABLES = frozenset(
    {
        *_STABLE_OR_FIAT_BASES,
        "RLUSD",
        "USDP",
        "BFUSD",
        "TUSD",
        "FDUSD",
        "USDE",
        "XUSD",
        "USD1",
        "EURI",
        "AEUR",
        "USDT",
        "USDC",
        "DAI",
    }
)

_LEVERAGED_SUFFIXES = ("3L", "3S", "2L", "2S", "BULL", "BEAR")
_BASE_RE = re.compile(r"^[A-Z0-9]{2,15}$")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _is_leveraged_token(base: str) -> bool:
    b = (base or "").upper()
    if b.endswith(_LEVERAGED_SUFFIXES):
        return True
    # BTCUP / ETHDOWN — не путать с JUP (3 буквы)
    if len(b) >= 5 and (b.endswith("UP") or b.endswith("DOWN")):
        return True
    return False


def is_swing_base(base: str) -> bool:
    b = (base or "").upper()
    if not b or not _BASE_RE.match(b) or b in _SWING_STABLES:
        return False
    if _is_leveraged_token(b) or is_bstock_base(b):
        return False
    return True


def discover_swing_symbols(
    *,
    exchange: Any | None = None,
    limit: int | None = None,
    min_quote_volume: float | None = None,
) -> list[dict[str, Any]]:
    """Ликвидные spot USDT, пригодные для удержания неделю–месяц."""
    lim = limit if limit is not None else _env_int("SWING_TOP_N", 32)
    min_vol = (
        min_quote_volume
        if min_quote_volume is not None
        else _env_float("SWING_MIN_QUOTE_VOL", 20_000_000.0)
    )
    ex = exchange or ccxt.binance({"enableRateLimit": True})
    markets = ex.load_markets()
    allowed: set[str] = set()
    for sym, m in markets.items():
        if not (m.get("spot") and m.get("active") and m.get("quote") == "USDT"):
            continue
        if not is_swing_base(str(m.get("base") or "")):
            continue
        allowed.add(sym)
    if not allowed:
        return []

    # Все тикеры одним запросом — список symbols на Binance ломается
    # на не-ASCII базах и слишком длинном URL.
    tickers = ex.fetch_tickers()
    rows: list[dict[str, Any]] = []
    by_sym: dict[str, dict[str, Any]] = {}
    for sym in allowed:
        t = tickers.get(sym) or {}
        qv = float(t.get("quoteVolume") or 0.0)
        base = sym.split("/")[0]
        row = {
            "symbol": sym,
            "base": base,
            "last": t.get("last"),
            "quote_volume": round(qv, 2),
            "change_pct": t.get("percentage"),
            "core": sym in SWING_CORE_SYMBOLS,
        }
        by_sym[sym] = row
        if qv >= min_vol:
            rows.append(row)

    # Majors всегда в списке
    for core in SWING_CORE_SYMBOLS:
        if core in by_sym and all(r["symbol"] != core for r in rows):
            rows.append(by_sym[core])

    core_rows = [r for r in rows if r.get("core")]
    core_rows.sort(key=lambda r: -float(r.get("quote_volume") or 0))
    # Pump-токены с всплеском 24h vol не держим неделю–месяц
    if len(core_rows) >= 8:
        return core_rows[: max(1, lim)]
    rows.sort(key=lambda r: -float(r.get("quote_volume") or 0))
    return rows[: max(1, lim)]


def load_swing_scan() -> dict[str, Any] | None:
    if not SWING_CACHE_PATH.is_file():
        return None
    try:
        data = json.loads(SWING_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_swing_scan(payload: dict[str, Any]) -> None:
    ensure_directories()
    tmp = SWING_CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SWING_CACHE_PATH)


def save_swing_progress(payload: dict[str, Any]) -> None:
    ensure_directories()
    SWING_PROGRESS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_swing_progress() -> dict[str, Any]:
    if not SWING_PROGRESS_PATH.is_file():
        return {"status": "idle"}
    try:
        data = json.loads(SWING_PROGRESS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"status": "idle"}
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": "bad progress file"}


def _auto_trade_yaml() -> dict:
    import yaml

    path = CONFIGS_DIR / "config.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("auto_trade") or {}


def run_swing_scan(
    *,
    top: int | None = None,
    timeframe: str | None = None,
    stage1_min_score: float | None = None,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Скан лучших среднесрочных входов по ликвидным majors."""
    tf = (timeframe or os.environ.get("SWING_TIMEFRAME") or "1d").strip() or "1d"
    top_n = top if top is not None else _env_int("SWING_RESULT_TOP", 12)
    s1 = (
        stage1_min_score
        if stage1_min_score is not None
        else _env_float("SWING_STAGE1_MIN", 16.0)
    )

    universe = discover_swing_symbols()
    symbols = tuple(r["symbol"] for r in universe)
    if not symbols:
        return {
            "status": "error",
            "error": "Не найдены ликвидные USDT-пары для среднесрока",
            "universe": [],
            "top_setups": [],
        }

    from dataclasses import replace

    params = replace(PARAMS_SWING)
    if tf == "4h":
        params = replace(
            params,
            require_htf_align=True,
            htf_timeframe="1d",
            trend_lookback=60,
            min_trend_move_pct=0.025,
            min_atr_pct=0.004,
        )

    scan_cfg = TrendScanConfig(
        timeframe=tf,
        bars=0,
        top_n=max(1, top_n),
        stage1_min_score=s1,
        trend_params=params,
        use_filtered_symbols=False,
        symbols=symbols,
        long_only=False,
        use_closed_bar_only=True,
        allow_trend=True,
        allow_range=True,  # дневной range = недели у S/R
        btc_regime_filter=True,
        min_bars=160,
    )
    auto_cfg = load_auto_trade_config(_auto_trade_yaml())
    auto_cfg = replace(auto_cfg, min_risk_reward=min(float(auto_cfg.min_risk_reward), 1.5))
    report = scan_combined_setups(
        symbols,
        scan_cfg=scan_cfg,
        auto_cfg=auto_cfg,
        progress_cb=progress_cb,
    )

    vol_by_sym = {r["symbol"]: r for r in universe}
    for row in report.get("top_setups") or []:
        meta = vol_by_sym.get(str(row.get("symbol") or ""))
        if meta:
            row["quote_volume"] = meta.get("quote_volume")
            row["change_pct"] = meta.get("change_pct")
        why = str(row.get("why_selected") or "")
        row["horizon"] = "week-month"
        if "swing" not in why.lower():
            row["why_selected"] = f"среднесрок 1–4 нед; {why}"

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "universe": universe,
        "universe_count": len(universe),
        "scan_config": {
            "mode": "swing",
            "horizon": "week-month",
            "timeframe": tf,
            "htf_timeframe": params.htf_timeframe if params.require_htf_align else None,
            "stage1_min_score": s1,
            "top_n": top_n,
            "allow_trend": True,
            "allow_range": True,
            "btc_regime_filter": True,
            "rr_target": params.rr_target,
            "symbols_count": len(symbols),
        },
        "report": report,
    }
    save_swing_scan(payload)
    return payload


def run_swing_scan_background(
    *,
    top: int | None = None,
    timeframe: str | None = None,
    stage1_min_score: float | None = None,
) -> None:
    started = datetime.now(timezone.utc).isoformat()
    save_swing_progress(
        {
            "status": "running",
            "kind": "swing_scan",
            "started_at": started,
            "progress": {"current": 0, "total": 0, "symbol": None},
        }
    )

    def on_progress(p: dict[str, Any]) -> None:
        save_swing_progress(
            {
                "status": "running",
                "kind": "swing_scan",
                "started_at": started,
                "progress": p,
            }
        )

    try:
        payload = run_swing_scan(
            top=top,
            timeframe=timeframe,
            stage1_min_score=stage1_min_score,
            progress_cb=on_progress,
        )
        if payload.get("status") == "error":
            save_swing_progress(
                {
                    "status": "error",
                    "kind": "swing_scan",
                    "error": payload.get("error"),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return
        rep = payload.get("report") or {}
        save_swing_progress(
            {
                "status": "done",
                "kind": "swing_scan",
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "candidates_found": rep.get("candidates_found"),
                "progress": {
                    "current": payload.get("universe_count") or 0,
                    "total": payload.get("universe_count") or 0,
                    "symbol": None,
                },
            }
        )
    except Exception as e:
        _log.exception("swing scan failed")
        save_swing_progress(
            {
                "status": "error",
                "kind": "swing_scan",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
