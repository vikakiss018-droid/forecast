"""
Скан 50 отфильтрованных пар: тренд 1h, импульс, rel_volume — как multi-symbol backtest (без kNN).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import ccxt
import pandas as pd

from .auto_trader import load_auto_trade_config, validate_setup
from .indicators import add_basic_indicators
from .features import add_basic_features
from .market_scanner import (
    _adjust_stage1_for_direction,
    _level_proximity,
    _stage1_snapshot,
)
from .run_symbol_ranking import load_filtered_symbols
from .signal_combiner import compute_volume_scores
from .single_symbol_backtest import _build_candidate
from .tf_backtest import BARS_BY_TF, _fetch_df
from .strategy_config import env_bool, env_float, env_int, env_str, yaml_section
from .trend_rules import DEFAULT_TREND_PARAMS, TrendPullbackParams, build_trend_plan


def df_closed_only(df: pd.DataFrame) -> pd.DataFrame:
    """Последняя незакрытая свеча отброшена — как в walk-forward бэктесте."""
    if len(df) < 3:
        return df
    return df.iloc[:-1].copy()


@dataclass
class TrendScanConfig:
    timeframe: str = "1h"
    bars: int = 0  # 0 → BARS_BY_TF[timeframe]
    top_n: int = 20
    stage1_min_score: float = 18.0
    min_probability_pct: float = 50.0
    trend_params: TrendPullbackParams | None = None
    use_filtered_symbols: bool = True
    symbols: tuple[str, ...] | None = None
    long_only: bool = True
    use_closed_bar_only: bool = True


def trend_params_from_yaml() -> TrendPullbackParams:
    y = yaml_section("trend_scan")
    return TrendPullbackParams(
        require_pullback=False,
        require_htf_align=False,
        min_rel_volume=env_float("TREND_MIN_REL_VOLUME", float(y.get("min_rel_volume", 1.2))),
        min_atr_pct=env_float("TREND_MIN_ATR_PCT", float(y.get("min_atr_pct", 0))),
        trend_lookback=env_int("TREND_LOOKBACK", int(y.get("lookback", 60))),
        min_trend_move_pct=env_float("TREND_MIN_MOVE_PCT", float(y.get("min_move_pct", 0.008))),
        block_asian_session=env_bool("TREND_BLOCK_ASIAN", bool(y.get("block_asian_session", False))),
    )


def trend_scan_config_from_env() -> TrendScanConfig:
    y = yaml_section("trend_scan")
    tf = env_str("FORECAST_TIMEFRAME", str(y.get("timeframe", "1h")))
    bars_default = int(y.get("bars", BARS_BY_TF.get(tf, 1000)))
    bars = env_int("FORECAST_BARS", bars_default, positive=True)
    top_n = env_int("FORECAST_TOP", int(y.get("top_n", 20)), positive=True)
    stage1 = env_float(
        "FORECAST_STAGE1_MIN_SCORE",
        float(y.get("stage1_min_score", 18)),
        positive=True,
    )
    min_prob = env_float("FORECAST_MIN_PROB_PCT", float(y.get("min_prob_pct", 50)), positive=True)
    use_filtered = env_bool("FORECAST_USE_FILTERED", bool(y.get("use_filtered", True)))
    symbols: tuple[str, ...] | None = None
    sym_env = os.environ.get("FORECAST_SYMBOLS", "").strip()
    if sym_env:
        symbols = tuple(s.strip() for s in sym_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or None
    long_only = env_bool("FORECAST_LONG_ONLY", bool(y.get("long_only", True)))
    use_closed = env_bool("FORECAST_USE_CLOSED_BAR", bool(y.get("use_closed_bar_only", True)))
    return TrendScanConfig(
        timeframe=tf,
        bars=bars,
        top_n=top_n,
        stage1_min_score=stage1,
        min_probability_pct=min_prob,
        trend_params=trend_params_from_yaml(),
        use_filtered_symbols=use_filtered,
        symbols=symbols,
        long_only=long_only,
        use_closed_bar_only=use_closed,
    )


def scan_trend_setups(
    symbols: tuple[str, ...],
    *,
    scan_cfg: TrendScanConfig | None = None,
    auto_cfg: Any | None = None,
) -> dict[str, Any]:
    """Возвращает report в формате, совместимом с auto_trader (top_setups)."""
    scan_cfg = scan_cfg or TrendScanConfig()
    params = scan_cfg.trend_params or trend_params_from_yaml()
    bars = scan_cfg.bars or BARS_BY_TF.get(scan_cfg.timeframe, 1000)
    auto_cfg = auto_cfg or load_auto_trade_config()
    auto_cfg.min_probability_pct = scan_cfg.min_probability_pct
    auto_cfg.allow_level_breakout = False
    auto_cfg.allow_triangle = False

    ex = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    candidates: list[dict[str, Any]] = []
    skipped: list[str] = []

    for symbol in symbols:
        df = _fetch_df(ex, symbol, scan_cfg.timeframe, bars)
        if df is None:
            skipped.append(symbol)
            continue

        work = df_closed_only(df) if scan_cfg.use_closed_bar_only else df
        if len(work) < 280:
            skipped.append(symbol)
            continue

        snap = _stage1_snapshot(work)
        plan = build_trend_plan(work, snap, params)
        if plan is None:
            continue
        if scan_cfg.long_only and str(plan.get("direction", "")).strip().lower() == "short":
            continue

        last = df.iloc[-1]
        close = float(last["close"])
        candle_bullish = close > float(last["open"])
        rel_vol = float(snap["context"]["rel_volume"])
        support = float(plan["trend_support"])
        resistance = float(plan["trend_resistance"])
        vol_up, vol_down = compute_volume_scores(df)

        stage1 = _adjust_stage1_for_direction(
            max(float(snap["stage1_score"]), 10.0),
            direction=str(plan["direction"]),
            rel_vol=rel_vol,
            candle_bullish=candle_bullish,
            close=close,
            support=support,
            resistance=resistance,
            vol_up=vol_up,
            vol_down=vol_down,
        )
        if stage1 < scan_cfg.stage1_min_score:
            continue

        cand = _build_candidate(symbol=symbol, snap=snap, plan=plan, stage1=stage1, df=work)
        cand["trend"] = plan.get("trend")
        cand["entry_style"] = plan.get("entry_style")
        cand["rel_volume"] = plan.get("rel_volume")
        cand["why_selected"] = (
            f"trend {plan.get('trend')} momentum 1h; rel_vol={rel_vol:.2f}; "
            f"TP {params.tp_target_pct * 100:.0f}%"
        )

        ok, reason = validate_setup(cand, auto_cfg)
        if not ok:
            print(f"[trend_scan] skip {symbol}: {reason}", flush=True)
            continue

        candidates.append(cand)

    candidates.sort(key=lambda c: -float(c.get("score") or 0))
    top = candidates[: max(1, scan_cfg.top_n)]

    return {
        "mode": "trend_momentum",
        "entry_style": "momentum",
        "timeframe": scan_cfg.timeframe,
        "symbols_universe": list(symbols),
        "symbols_scanned": len(symbols),
        "skipped_no_data": skipped,
        "candidates_found": len(candidates),
        "top_setups": top,
        "scan_duration_sec": round(time.perf_counter() - t0, 1),
        "trend_params": {
            "lookback": params.trend_lookback,
            "min_move_pct": params.min_trend_move_pct,
            "min_rel_volume": params.min_rel_volume,
            "tp_target_pct": params.tp_target_pct,
            "block_asian_session": params.block_asian_session,
        },
    }


def scan_trend_filtered_setups(
    scan_cfg: TrendScanConfig | None = None,
    *,
    auto_cfg: Any | None = None,
) -> dict[str, Any]:
    scan_cfg = scan_cfg or trend_scan_config_from_env()
    symbols = scan_cfg.symbols
    if not symbols:
        if scan_cfg.use_filtered_symbols:
            symbols = load_filtered_symbols()
        if not symbols:
            return {
                "status": "error",
                "error": "no_symbols: run symbol ranking or set FORECAST_SYMBOLS",
                "top_setups": [],
            }
    return scan_trend_setups(symbols, scan_cfg=scan_cfg, auto_cfg=auto_cfg)
