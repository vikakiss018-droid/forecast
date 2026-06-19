"""Scalp mode: env-driven config (event-driven OB + aggTrade signals)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class ScalpConfig:
    enabled: bool = False
    dry_run: bool = True
    obi_long_min: float = 0.15
    obi_short_max: float = -0.15
    obi_persist_sec: float = 3.0
    max_spread_bps: float = 15.0
    stale_sec: float = 5.0
    cooldown_sec: int = 45
    max_trades_per_hour: int = 12
    max_trades_per_day: int = 80
    min_tp_pct: float = 0.35
    sl_pct: float = 0.25
    time_stop_sec: int = 90
    agg_flow_window_sec: float = 5.0
    agg_flow_min_ratio: float = 1.3
    require_agg_flow: bool = False
    tick_log: bool = False
    spot_allow_short: bool = False


def load_scalp_config() -> ScalpConfig:
    return ScalpConfig(
        enabled=_env_bool("SCALP_ENABLED", False),
        dry_run=_env_bool("SCALP_DRY_RUN", True),
        obi_long_min=_env_float("SCALP_OBI_LONG_MIN", 0.15),
        obi_short_max=_env_float("SCALP_OBI_SHORT_MAX", -0.15),
        obi_persist_sec=_env_float("SCALP_OBI_PERSIST_SEC", 3.0),
        max_spread_bps=_env_float("SCALP_MAX_SPREAD_BPS", 15.0),
        stale_sec=_env_float("SCALP_STALE_SEC", 5.0),
        cooldown_sec=_env_int("SCALP_COOLDOWN_SEC", 45),
        max_trades_per_hour=_env_int("SCALP_MAX_TRADES_PER_HOUR", 12),
        max_trades_per_day=_env_int("SCALP_MAX_TRADES_PER_DAY", 80),
        min_tp_pct=_env_float("SCALP_MIN_TP_PCT", 0.35),
        sl_pct=_env_float("SCALP_SL_PCT", 0.25),
        time_stop_sec=_env_int("SCALP_TIME_STOP_SEC", 90),
        agg_flow_window_sec=_env_float("SCALP_AGG_FLOW_WINDOW_SEC", 5.0),
        agg_flow_min_ratio=_env_float("SCALP_AGG_FLOW_MIN_RATIO", 1.3),
        require_agg_flow=_env_bool("SCALP_REQUIRE_AGG_FLOW", False),
        tick_log=_env_bool("SCALP_TICK_LOG", False),
        spot_allow_short=_env_bool("SCALP_SPOT_ALLOW_SHORT", False),
    )
