"""
Скан 50 отфильтрованных пар: тренд + флет на 1h (как combined backtest, без kNN).

На каждой паре: сначала тренд (up/down), иначе отскок от S/R во флете (range).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import ccxt
import pandas as pd

from .auto_trader import load_auto_trade_config, validate_setup, apply_scan_auto_filters
from .market_scanner import _adjust_stage1_for_direction, _stage1_snapshot
from .run_symbol_ranking import load_filtered_symbols
from .signal_combiner import compute_volume_scores
from .single_symbol_backtest import _build_candidate
from .strategy_config import env_bool, env_float, env_int, env_str, yaml_section
from .tf_backtest import BARS_BY_TF, _fetch_df, fetch_top_usdt_symbols
from .trend_rules import (
    DEFAULT_TREND_PARAMS,
    MIN_ATR_PCT,
    MIN_REL_VOLUME_RANGE,
    TrendPullbackParams,
    build_range_plan,
    build_trend_plan,
    htf_trend_aligned,
    trend_params_for_timeframe,
)

Regime = Literal["trend", "range"]
SCAN_MODE = "trend_plus_range"

# Режим BTC: когда BTC падает, лонги по альтам проигрывают независимо от сетапа
BTC_REGIME_SYMBOL = "BTC/USDT"
BTC_REGIME_TIMEFRAME = "4h"
BTC_REGIME_BARS = 400
BTC_REGIME_RET_24H_THR = 0.02  # |24h return| > 2% — выраженное направление
HTF_ALIGN_BARS = 400


def df_closed_only(df: pd.DataFrame) -> pd.DataFrame:
    """Последняя незакрытая свеча отброшена — как в walk-forward бэктесте."""
    if len(df) < 3:
        return df
    return df.iloc[:-1].copy()


@dataclass
class TrendScanConfig:
    """Конфиг live-скана (секция trend_scan в config.yaml)."""

    timeframe: str = "1h"
    bars: int = 0
    top_n: int = 20
    stage1_min_score: float = 18.0
    min_probability_pct: float = 50.0
    trend_params: TrendPullbackParams | None = None
    use_filtered_symbols: bool = True
    universe_top_n: int = 400  # если use_filtered=false — топ-N по объёму 24h
    symbols: tuple[str, ...] | None = None
    long_only: bool = False
    use_closed_bar_only: bool = True
    allow_trend: bool = True
    allow_range: bool = True
    btc_regime_filter: bool = True


def _resolve_scan_symbols(scan_cfg: TrendScanConfig) -> tuple[str, ...]:
    """Явный список → filtered → топ по объёму (universe_top_n)."""
    if scan_cfg.symbols:
        return tuple(scan_cfg.symbols)
    if scan_cfg.use_filtered_symbols:
        filtered = load_filtered_symbols()
        if filtered:
            return filtered
    n = max(1, int(scan_cfg.universe_top_n or 400))
    ex = ccxt.binance({"enableRateLimit": True})
    return fetch_top_usdt_symbols(ex, limit=n)


def trend_params_from_yaml() -> TrendPullbackParams:
    y = yaml_section("trend_scan")
    tf = env_str("FORECAST_TIMEFRAME", str(y.get("timeframe", "1h")))
    base = trend_params_for_timeframe(tf, base=DEFAULT_TREND_PARAMS)
    return TrendPullbackParams(
        require_pullback=env_bool("TREND_REQUIRE_PULLBACK", bool(y.get("require_pullback", base.require_pullback))),
        require_htf_align=env_bool(
            "TREND_REQUIRE_HTF_ALIGN", bool(y.get("require_htf_align", base.require_htf_align))
        ),
        block_opposite_level=env_bool(
            "TREND_BLOCK_OPPOSITE_LEVEL",
            bool(y.get("block_opposite_level", base.block_opposite_level)),
        ),
        require_rejection_candle=env_bool(
            "TREND_REQUIRE_REJECTION",
            bool(y.get("require_rejection_candle", base.require_rejection_candle)),
        ),
        min_rel_volume=env_float(
            "TREND_MIN_REL_VOLUME",
            float(y.get("min_rel_volume", base.min_rel_volume)),
            positive=True,
        ),
        min_rel_volume_range=env_float(
            "TREND_MIN_REL_VOLUME_RANGE",
            float(y.get("min_rel_volume_range", base.min_rel_volume_range)),
            positive=True,
        ),
        min_atr_pct=env_float("TREND_MIN_ATR_PCT", float(y.get("min_atr_pct", base.min_atr_pct))),
        trend_lookback=env_int(
            "TREND_LOOKBACK", int(y.get("lookback", base.trend_lookback)), positive=True
        ),
        min_trend_move_pct=env_float(
            "TREND_MIN_MOVE_PCT",
            float(y.get("min_move_pct", base.min_trend_move_pct)),
            positive=True,
        ),
        tp_target_pct=env_float(
            "TREND_TP_TARGET_PCT",
            float(y.get("tp_target_pct", base.tp_target_pct)),
        ),
        rr_target=env_float("TREND_RR_TARGET", float(y.get("rr_target", base.rr_target)), positive=True),
        block_asian_session=env_bool(
            "TREND_BLOCK_ASIAN", bool(y.get("block_asian_session", base.block_asian_session))
        ),
        htf_timeframe=str(y.get("htf_timeframe", base.htf_timeframe)),
        min_level_touches=env_int(
            "MIN_LEVEL_TOUCHES",
            int(y.get("min_level_touches", base.min_level_touches)),
        ),
        require_max_touches_side=env_bool(
            "REQUIRE_MAX_TOUCHES_SIDE",
            bool(y.get("require_max_touches_side", base.require_max_touches_side)),
        ),
        require_with_trend_level=env_bool(
            "REQUIRE_WITH_TREND_LEVEL",
            bool(y.get("require_with_trend_level", base.require_with_trend_level)),
        ),
        with_trend_level_zone_frac=env_float(
            "WITH_TREND_LEVEL_ZONE_FRAC",
            float(y.get("with_trend_level_zone_frac", base.with_trend_level_zone_frac)),
            positive=True,
        ),
    )


def trend_scan_config_from_env() -> TrendScanConfig:
    y = yaml_section("trend_scan")
    tf = env_str("FORECAST_TIMEFRAME", str(y.get("timeframe", "1h")))
    bars_default = int(y.get("bars", BARS_BY_TF.get(tf, 1000)))
    bars = env_int("FORECAST_BARS", bars_default, positive=True)
    top_n = env_int("FORECAST_TOP", int(y.get("top_n", 20)), positive=True)
    stage1 = env_float(
        "FORECAST_STAGE1_MIN_SCORE",
        float(y.get("stage1_min_score", 12)),
        positive=True,
    )
    min_prob = env_float("FORECAST_MIN_PROB_PCT", float(y.get("min_prob_pct", 0)))
    use_filtered = env_bool("FORECAST_USE_FILTERED", bool(y.get("use_filtered", True)))
    universe_top_n = env_int(
        "FORECAST_SCAN_TOP_N",
        int(y.get("universe_top_n", y.get("scan_top_n", 400))),
        positive=True,
    )
    symbols: tuple[str, ...] | None = None
    sym_env = os.environ.get("FORECAST_SYMBOLS", "").strip()
    if sym_env:
        symbols = tuple(s.strip() for s in sym_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or None
    long_only = env_bool("FORECAST_LONG_ONLY", bool(y.get("long_only", False)))
    use_closed = env_bool("FORECAST_USE_CLOSED_BAR", bool(y.get("use_closed_bar_only", True)))
    allow_trend = env_bool("FORECAST_ALLOW_TREND", bool(y.get("allow_trend", True)))
    allow_range = env_bool("FORECAST_ALLOW_RANGE", bool(y.get("allow_range", True)))
    btc_regime = env_bool("FORECAST_BTC_REGIME_FILTER", bool(y.get("btc_regime_filter", True)))
    return TrendScanConfig(
        timeframe=tf,
        bars=bars,
        top_n=top_n,
        stage1_min_score=stage1,
        min_probability_pct=min_prob,
        trend_params=trend_params_from_yaml(),
        use_filtered_symbols=use_filtered,
        universe_top_n=universe_top_n,
        symbols=symbols,
        long_only=long_only,
        use_closed_bar_only=use_closed,
        allow_trend=allow_trend,
        allow_range=allow_range,
        btc_regime_filter=btc_regime,
    )


def _btc_regime_from_df(df: pd.DataFrame) -> str:
    """Режим BTC по уже загруженному 4h DF (последний бар)."""
    if df is None or len(df) < 210:
        return "neutral"
    last = df.iloc[-1]
    close = float(last["close"])
    ema200 = float(last["ema_200"])
    bars_24h = 6  # 6 баров по 4h
    ret_24h = 0.0
    if len(df) > bars_24h:
        prev = float(df["close"].iloc[-bars_24h - 1])
        if prev > 0:
            ret_24h = close / prev - 1.0
    bear = close < ema200 or ret_24h < -BTC_REGIME_RET_24H_THR
    bull = close > ema200 or ret_24h > BTC_REGIME_RET_24H_THR
    if bear and bull:
        return "neutral"
    if bear:
        return "bear"
    if bull:
        return "bull"
    return "neutral"


def btc_regime_at(df_btc: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """BTC regime на момент as_of без lookahead (только закрытые бары ≤ as_of)."""
    if df_btc is None or df_btc.empty:
        return "neutral"
    sub = df_btc[df_btc.index <= as_of]
    return _btc_regime_from_df(sub)


def _btc_regime(ex: ccxt.Exchange) -> str:
    """
    Режим BTC на 4h: 'bear' — блок лонгов по альтам, 'bull' — блок шортов.
    bear: close < EMA200 или 24h return < -2%; bull — зеркально.
    Конфликт сигналов (например, выше EMA200, но резко падает) => 'neutral'.
    """
    df = _fetch_df(ex, BTC_REGIME_SYMBOL, BTC_REGIME_TIMEFRAME, BTC_REGIME_BARS)
    return _btc_regime_from_df(df)


def _resolve_plan(
    work: pd.DataFrame,
    snap: dict[str, Any],
    params: TrendPullbackParams,
    *,
    allow_trend: bool,
    allow_range: bool,
) -> tuple[dict[str, Any], Regime] | None:
    if allow_trend:
        plan = build_trend_plan(work, snap, params)
        if plan is not None:
            return plan, "trend"
    if allow_range:
        plan = build_range_plan(work, snap, params)
        if plan is not None:
            return plan, "range"
    return None


def _why_selected(plan: dict[str, Any], regime: Regime, rel_vol: float, params: TrendPullbackParams) -> str:
    if regime == "range":
        return (
            f"range bounce 1h; pos={plan.get('range_position_pct')}% "
            f"rel_vol={rel_vol:.2f}; RR={plan.get('risk_reward', 0):.2f}"
        )
    tp_txt = (
        f"TP {params.tp_target_pct * 100:.0f}%"
        if params.tp_target_pct > 0
        else f"TP {params.rr_target:.1f}R"
    )
    return f"trend {plan.get('trend')} {plan.get('entry_style')} 1h; rel_vol={rel_vol:.2f}; {tp_txt}"


def scan_combined_setups(
    symbols: tuple[str, ...],
    *,
    scan_cfg: TrendScanConfig | None = None,
    auto_cfg: Any | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Отчёт для auto_trader и панели (top_setups)."""
    scan_cfg = scan_cfg or TrendScanConfig()
    params = scan_cfg.trend_params or trend_params_from_yaml()
    bars = scan_cfg.bars or BARS_BY_TF.get(scan_cfg.timeframe, 1000)
    auto_cfg = auto_cfg or load_auto_trade_config()
    apply_scan_auto_filters(auto_cfg, scan_cfg)
    auto_cfg.allow_level_breakout = False
    auto_cfg.allow_triangle = False

    ex = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    candidates: list[dict[str, Any]] = []
    skipped: list[str] = []
    regime_counts: dict[str, int] = {"trend": 0, "range": 0}
    symbols_list = list(symbols)
    total = len(symbols_list)

    btc_regime = _btc_regime(ex) if scan_cfg.btc_regime_filter else "neutral"
    if btc_regime != "neutral":
        blocked_side = "Long" if btc_regime == "bear" else "Short"
        print(f"[combined_scan] BTC regime={btc_regime}: skip all {blocked_side}", flush=True)

    for i, symbol in enumerate(symbols_list, 1):
        if progress_cb:
            progress_cb({"current": i, "total": total, "symbol": symbol})
        df = _fetch_df(ex, symbol, scan_cfg.timeframe, bars)
        if df is None:
            skipped.append(symbol)
            continue

        work = df_closed_only(df) if scan_cfg.use_closed_bar_only else df
        if len(work) < 280:
            skipped.append(symbol)
            continue

        snap = _stage1_snapshot(work)
        resolved = _resolve_plan(
            work,
            snap,
            params,
            allow_trend=scan_cfg.allow_trend,
            allow_range=scan_cfg.allow_range,
        )
        if resolved is None:
            continue

        plan, regime = resolved
        direction = str(plan.get("direction", "")).strip()
        if scan_cfg.long_only and direction.lower() == "short":
            continue
        if btc_regime == "bear" and direction == "Long":
            continue
        if btc_regime == "bull" and direction == "Short":
            continue

        if regime == "trend" and params.require_htf_align:
            df_htf = _fetch_df(ex, symbol, params.htf_timeframe, HTF_ALIGN_BARS)
            if df_htf is None:
                continue
            aligned, htf_reason = htf_trend_aligned(
                df_htf, work.index[-1], str(plan.get("trend", "")), params
            )
            if not aligned:
                print(f"[combined_scan] skip {symbol} (htf): {htf_reason}", flush=True)
                continue

        last = work.iloc[-1]
        close = float(last["close"])
        candle_bullish = close > float(last["open"])
        rel_vol = float(snap["context"]["rel_volume"])
        support = float(plan["trend_support"])
        resistance = float(plan["trend_resistance"])
        vol_up, vol_down = compute_volume_scores(work)

        # Без floor: пара без уровней/паттернов не должна проходить порог за счёт одного объёма
        stage1 = _adjust_stage1_for_direction(
            float(snap["stage1_score"]),
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
        cand["regime"] = regime
        cand["trend"] = plan.get("trend")
        cand["entry_style"] = plan.get("entry_style")
        cand["rel_volume"] = plan.get("rel_volume")
        cand["pattern"] = "range bounce" if regime == "range" else f"trend {plan.get('trend', '')}"
        cand["why_selected"] = _why_selected(plan, regime, rel_vol, params)

        ok, reason = validate_setup(cand, auto_cfg)
        if not ok:
            print(f"[combined_scan] skip {symbol} ({regime}): {reason}", flush=True)
            continue

        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        candidates.append(cand)

    candidates.sort(key=lambda c: (-float(c.get("score") or 0), str(c.get("regime"))))
    top = candidates[: max(1, scan_cfg.top_n)]

    return {
        "mode": SCAN_MODE,
        "entry_style": "trend_momentum_and_range_bounce",
        "timeframe": scan_cfg.timeframe,
        "symbols_universe": list(symbols),
        "symbols_scanned": len(symbols),
        "skipped_no_data": skipped,
        "candidates_found": len(candidates),
        "candidates_by_regime": regime_counts,
        "top_setups": top,
        "scan_duration_sec": round(time.perf_counter() - t0, 1),
        "long_only": scan_cfg.long_only,
        "allow_trend": scan_cfg.allow_trend,
        "allow_range": scan_cfg.allow_range,
        "btc_regime": btc_regime,
        "trend_params": {
            "lookback": params.trend_lookback,
            "min_move_pct": params.min_trend_move_pct,
            "min_rel_volume": params.min_rel_volume,
            "min_rel_volume_range": params.min_rel_volume_range,
            "min_atr_pct": params.min_atr_pct,
            "tp_target_pct": params.tp_target_pct,
            "rr_target": params.rr_target,
            "require_pullback": params.require_pullback,
            "require_htf_align": params.require_htf_align,
            "block_opposite_level": params.block_opposite_level,
            "block_asian_session": params.block_asian_session,
        },
        "rule": (
            "тренд (up/down) или флет (range) у S/R; "
            f"stage1>={scan_cfg.stage1_min_score}; RR>={auto_cfg.min_risk_reward}"
        ),
    }


def scan_combined_filtered_setups(
    scan_cfg: TrendScanConfig | None = None,
    *,
    auto_cfg: Any | None = None,
) -> dict[str, Any]:
    scan_cfg = scan_cfg or trend_scan_config_from_env()
    try:
        symbols = _resolve_scan_symbols(scan_cfg)
    except Exception as e:
        return {
            "status": "error",
            "error": f"no_symbols: {e}",
            "top_setups": [],
        }
    if not symbols:
        return {
            "status": "error",
            "error": "no_symbols: set FORECAST_USE_FILTERED=0 and FORECAST_SCAN_TOP_N=400, "
            "or run ranking / set FORECAST_SYMBOLS",
            "top_setups": [],
        }
    scan_cfg.symbols = symbols
    return scan_combined_setups(symbols, scan_cfg=scan_cfg, auto_cfg=auto_cfg)


# Совместимость со старыми импортами
scan_trend_setups = scan_combined_setups
scan_trend_filtered_setups = scan_combined_filtered_setups
