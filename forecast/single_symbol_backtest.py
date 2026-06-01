"""
Walk-forward backtest: historical OHLCV + trend following (no kNN).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
import numpy as np
import pandas as pd

from .auto_trader import AutoTradeConfig, load_auto_trade_config, validate_setup
from .trend_rules import (
    DEFAULT_TREND_PARAMS,
    TrendPullbackParams,
    build_trend_plan,
    htf_trend_aligned,
    trend_only_stats,
)
from .market_scanner import (
    _adjust_stage1_for_direction,
    _level_proximity,
    _stage1_snapshot,
)
from .paths import PROCESSED_DATA_DIR, ensure_directories
from .signal_combiner import compute_volume_scores
from .tf_backtest import (
    BARS_BY_TF,
    DEFAULT_SYMBOLS,
    MAX_HOLD_BARS_BY_TF,
    STEP_BY_TF,
    _fetch_df,
    _simulate_exit,
    _trade_r,
)

RESULT_PATH = PROCESSED_DATA_DIR / "single_symbol_backtest_latest.json"
MULTI_RESULT_PATH = PROCESSED_DATA_DIR / "multi_symbol_backtest_latest.json"

DEFAULT_SYMBOL = "SOL/USDT"
DEFAULT_TIMEFRAME = "1h"
DEFAULT_BARS = 1000
TARGET_TRADES = 100
COOLDOWN_BARS = 4
DEFAULT_LEVERAGE = 1
DEFAULT_MAX_NOTIONAL_USDT = 50.0


def trade_position_sizing(
    entry: float,
    stop: float,
    *,
    balance_usdt: float,
    risk_pct: float,
    leverage: int = DEFAULT_LEVERAGE,
    max_notional_usdt: float = DEFAULT_MAX_NOTIONAL_USDT,
) -> dict[str, float]:
    """Как auto_trader: риск % от баланса, notional с учётом плеча и капа."""
    risk_frac = abs(entry - stop) / max(entry, 1e-12)
    risk_usdt = balance_usdt * (risk_pct / 100.0)
    if risk_frac <= 0:
        return {
            "risk_frac": 0.0,
            "risk_usdt": risk_usdt,
            "notional_usdt": 0.0,
            "margin_usdt": 0.0,
            "leverage": float(leverage),
        }
    notional = risk_usdt / risk_frac
    max_by_margin = balance_usdt * 0.98 * max(1, leverage)
    notional = min(notional, max_notional_usdt, max_by_margin)
    margin = notional / max(1, leverage)
    return {
        "risk_frac": round(risk_frac, 6),
        "risk_usdt": round(risk_usdt, 4),
        "notional_usdt": round(notional, 2),
        "margin_usdt": round(margin, 2),
        "leverage": float(leverage),
    }


def enrich_trades_sizing(
    trades: list[dict[str, Any]],
    *,
    balance_usdt: float,
    risk_pct: float,
    leverage: int = DEFAULT_LEVERAGE,
    max_notional_usdt: float = DEFAULT_MAX_NOTIONAL_USDT,
) -> None:
    for t in trades:
        sz = trade_position_sizing(
            float(t["entry"]),
            float(t["stop"]),
            balance_usdt=balance_usdt,
            risk_pct=risk_pct,
            leverage=leverage,
            max_notional_usdt=max_notional_usdt,
        )
        r = float(t["r_multiple"])
        pnl = r * sz["risk_frac"] * sz["notional_usdt"]
        t.update(sz)
        t["pnl_usdt"] = round(pnl, 2)


@dataclass
class MultiSymbolBacktestConfig:
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    timeframe: str = DEFAULT_TIMEFRAME
    bars: int = 0  # 0 → BARS_BY_TF[timeframe]
    target_trades: int = TARGET_TRADES
    stage1_min_score: float = 18.0
    stage1_relax_score: float = 12.0
    trend_only: bool = True
    trend_params: TrendPullbackParams | None = None
    require_htf_align: bool = False
    htf_timeframe: str = "4h"
    htf_bars: int = 0
    leverage: int = DEFAULT_LEVERAGE
    max_notional_usdt: float = DEFAULT_MAX_NOTIONAL_USDT
    use_leverage_sizing: bool = False
    long_only: bool = False


@dataclass(frozen=True)
class TrendEntryCandidate:
    trade: dict[str, Any]
    entry_time: pd.Timestamp
    side: str
    stage1_score: float
    exit_i: int
    next_i_skip: int
    next_i_accept: int


@dataclass
class SingleSymbolBacktestConfig:
    symbol: str = DEFAULT_SYMBOL
    timeframe: str = DEFAULT_TIMEFRAME
    bars: int = DEFAULT_BARS
    target_trades: int = TARGET_TRADES
    stage1_min_score: float = 18.0
    trend_only: bool = True


def _peek_trend_entry(
    df: pd.DataFrame,
    next_i: int,
    *,
    symbol: str,
    timeframe: str,
    auto_cfg: AutoTradeConfig,
    stage1_min: float,
    step: int,
    max_hold: int,
    cooldown_bars: int,
    trend_only: bool,
    trend_params: TrendPullbackParams,
    df_htf: pd.DataFrame | None,
    long_only: bool = False,
) -> TrendEntryCandidate | None:
    sub = df.iloc[: next_i + 1]
    snap = _stage1_snapshot(sub)

    plan = build_trend_plan(sub, snap, trend_params) if trend_only else None
    if plan is None:
        return None
    if long_only and str(plan.get("direction", "")).strip().lower() == "short":
        return None

    htf_trend: str | None = None
    if trend_params.require_htf_align:
        if df_htf is None:
            return None
        aligned, htf_trend = htf_trend_aligned(df_htf, sub.index[-1], str(plan["trend"]), trend_params)
        if not aligned:
            return None

    support = float(plan["trend_support"])
    resistance = float(plan["trend_resistance"])
    st = trend_only_stats(snap, plan)
    last = sub.iloc[-1]
    close = float(last["close"])
    candle_bullish = close > float(last["open"])
    rel_vol = float(snap["context"]["rel_volume"])
    vol_up, vol_down = compute_volume_scores(sub)
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
    if stage1 < stage1_min:
        return None

    cand = _build_candidate(symbol=symbol, snap=snap, plan=plan, stage1=stage1, df=sub)
    ok, _reason = validate_setup(cand, auto_cfg)
    if not ok:
        return None

    side = str(plan["direction"]).lower()
    entry = close
    stop = float(plan["stop"])
    tp = float(plan["target_2"])
    exit_px, exit_reason, exit_i = _simulate_exit(
        df,
        next_i,
        side=side,
        entry=entry,
        stop=stop,
        tp=tp,
        max_bars=max_hold,
    )
    r_mult = _trade_r(side, entry, exit_px, stop)
    entry_time = sub.index[-1]
    trade = {
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "pattern": f"trend {plan.get('trend')}",
        "trend": plan.get("trend"),
        "htf_trend": htf_trend,
        "entry_style": plan.get("entry_style"),
        "range_position_pct": plan.get("range_position_pct"),
        "rel_volume": plan.get("rel_volume"),
        "atr_pct": plan.get("atr_pct"),
        "trend_support": support,
        "trend_resistance": resistance,
        "entry_time": str(entry_time),
        "exit_time": str(df.index[exit_i]),
        "entry": entry,
        "exit": exit_px,
        "stop": stop,
        "tp": tp,
        "exit_reason": exit_reason,
        "r_multiple": round(r_mult, 3),
        "win": r_mult > 0,
        "stage1_score": round(stage1, 1),
        "rr_plan": float(plan["risk_reward"]),
    }
    return TrendEntryCandidate(
        trade=trade,
        entry_time=entry_time,
        side=side,
        stage1_score=stage1,
        exit_i=exit_i,
        next_i_skip=next_i + step,
        next_i_accept=exit_i + cooldown_bars,
    )


def _build_candidate(
    *,
    symbol: str,
    snap: dict[str, Any],
    plan: dict[str, Any],
    stage1: float,
    df: pd.DataFrame,
) -> dict[str, Any]:
    last = df.iloc[-1]
    close = float(last["close"])
    support = float(plan.get("trend_support", snap["support_level"]))
    resistance = float(plan.get("trend_resistance", snap["resistance_level"]))
    vol_up, vol_down = compute_volume_scores(df)
    near_support, _ = _level_proximity(close, support, resistance)
    return {
        "symbol": symbol,
        "pattern": f"trend {plan.get('trend', '')}",
        "setup": plan,
        "score": float(stage1),
        "atr_pct": float(last["atr_14"]) / max(close, 1e-12),
        "vol_s_up": float(vol_up),
        "vol_s_down": float(vol_down),
        "short_near_support": str(plan["direction"]) == "Short" and near_support,
        "retest_breakout": False,
        "breakout_consolidation": False,
    }


def backtest_single_symbol(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    auto_cfg: AutoTradeConfig,
    stage1_min: float,
    target_trades: int = TARGET_TRADES,
    step: int | None = None,
    max_hold_bars: int | None = None,
    cooldown_bars: int = COOLDOWN_BARS,
    trend_only: bool = True,
    trend_params: TrendPullbackParams | None = None,
    df_htf: pd.DataFrame | None = None,
    long_only: bool = False,
) -> list[dict[str, Any]]:
    params = trend_params or DEFAULT_TREND_PARAMS
    step = step if step is not None else STEP_BY_TF.get(timeframe, 2)
    max_hold = max_hold_bars if max_hold_bars is not None else MAX_HOLD_BARS_BY_TF.get(timeframe, 48)
    trades: list[dict[str, Any]] = []
    next_i = 72
    end = len(df) - max_hold - 1

    while next_i < end and len(trades) < target_trades:
        cand = _peek_trend_entry(
            df,
            next_i,
            symbol=symbol,
            timeframe=timeframe,
            auto_cfg=auto_cfg,
            stage1_min=stage1_min,
            step=step,
            max_hold=max_hold,
            cooldown_bars=cooldown_bars,
            trend_only=trend_only,
            trend_params=params,
            df_htf=df_htf,
            long_only=long_only,
        )
        if cand is None:
            next_i += step
            continue
        trades.append(cand.trade)
        next_i = cand.next_i_accept

    return trades


def _aggregate_by_symbol(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_sym.setdefault(str(t["symbol"]), []).append(t)
    out: dict[str, Any] = {}
    for sym, rows in by_sym.items():
        rs = [float(r["r_multiple"]) for r in rows]
        wins = sum(1 for r in rs if r > 0)
        n = len(rs)
        out[sym] = {
            "trades": n,
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / n, 1) if n else 0.0,
            "total_r": round(float(np.sum(rs)), 2) if rs else 0.0,
        }
    return out


def _effective_trend_params(cfg: MultiSymbolBacktestConfig) -> TrendPullbackParams:
    base = cfg.trend_params or DEFAULT_TREND_PARAMS
    if cfg.require_htf_align == base.require_htf_align and cfg.htf_timeframe == base.htf_timeframe:
        return base
    return TrendPullbackParams(
        trend_lookback=base.trend_lookback,
        min_trend_move_pct=base.min_trend_move_pct,
        rr_target=base.rr_target,
        tp_target_pct=base.tp_target_pct,
        require_pullback=base.require_pullback,
        pullback_lookback=base.pullback_lookback,
        long_pos_min=base.long_pos_min,
        long_pos_max=base.long_pos_max,
        short_pos_min=base.short_pos_min,
        short_pos_max=base.short_pos_max,
        min_pullback_from_swing_pct=base.min_pullback_from_swing_pct,
        require_htf_align=cfg.require_htf_align,
        htf_timeframe=cfg.htf_timeframe,
        min_rel_volume=base.min_rel_volume,
        min_atr_pct=base.min_atr_pct,
        block_asian_session=base.block_asian_session,
        asian_session_hours_utc=base.asian_session_hours_utc,
    )


def run_multi_symbol_backtest(
    *,
    cfg: MultiSymbolBacktestConfig | None = None,
    deposit_usdt: float = 1000.0,
    risk_pct: float | None = None,
) -> dict[str, Any]:
    """10 alts, one TF: trend following on historical OHLCV."""
    cfg = cfg or MultiSymbolBacktestConfig()
    cfg.bars = cfg.bars or BARS_BY_TF.get(cfg.timeframe, DEFAULT_BARS)
    htf_bars = cfg.htf_bars or BARS_BY_TF.get(cfg.htf_timeframe, 800)
    trend_params = _effective_trend_params(cfg)
    auto_cfg = load_auto_trade_config()
    auto_cfg.min_probability_pct = 50.0
    if risk_pct is None:
        risk_pct = float(auto_cfg.risk_pct_of_balance)
    if risk_pct <= 0:
        risk_pct = 0.5

    exchange = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    all_trades: list[dict[str, Any]] = []

    for symbol in cfg.symbols:
        if len(all_trades) >= cfg.target_trades:
            break
        df = _fetch_df(exchange, symbol, cfg.timeframe, cfg.bars)
        if df is None:
            print(f"[multi_bt] skip {symbol}: no data", flush=True)
            continue
        df_htf = None
        if trend_params.require_htf_align and cfg.timeframe == "1h":
            df_htf = _fetch_df(exchange, symbol, cfg.htf_timeframe, htf_bars)
            if df_htf is None:
                print(f"[multi_bt] skip {symbol}: no {cfg.htf_timeframe} data", flush=True)
                continue
        need = cfg.target_trades - len(all_trades)
        sym_trades = backtest_single_symbol(
            df,
            symbol=symbol,
            timeframe=cfg.timeframe,
            auto_cfg=auto_cfg,
            stage1_min=cfg.stage1_min_score,
            target_trades=need,
            trend_only=cfg.trend_only,
            trend_params=trend_params,
            df_htf=df_htf,
            long_only=cfg.long_only,
        )
        all_trades.extend(sym_trades)
        print(f"[multi_bt] {symbol}: +{len(sym_trades)} (total {len(all_trades)})", flush=True)
        if len(all_trades) >= cfg.target_trades:
            all_trades = all_trades[: cfg.target_trades]
            break

    if len(all_trades) < cfg.target_trades:
        for symbol in cfg.symbols:
            if len(all_trades) >= cfg.target_trades:
                break
            df = _fetch_df(exchange, symbol, cfg.timeframe, cfg.bars)
            if df is None:
                continue
            df_htf = None
            if trend_params.require_htf_align and cfg.timeframe == "1h":
                df_htf = _fetch_df(exchange, symbol, cfg.htf_timeframe, htf_bars)
                if df_htf is None:
                    continue
            need = cfg.target_trades - len(all_trades)
            extra = backtest_single_symbol(
                df,
                symbol=symbol,
                timeframe=cfg.timeframe,
                auto_cfg=auto_cfg,
                stage1_min=cfg.stage1_relax_score,
                target_trades=need,
                trend_only=cfg.trend_only,
                trend_params=trend_params,
                df_htf=df_htf,
                long_only=cfg.long_only,
            )
            seen = {(t["symbol"], t["entry_time"]) for t in all_trades}
            for t in extra:
                key = (t["symbol"], t["entry_time"])
                if key not in seen:
                    all_trades.append(t)
                    seen.add(key)
                if len(all_trades) >= cfg.target_trades:
                    all_trades = all_trades[: cfg.target_trades]
                    break

    if cfg.use_leverage_sizing and cfg.leverage > 1:
        enrich_trades_sizing(
            all_trades,
            balance_usdt=deposit_usdt,
            risk_pct=risk_pct,
            leverage=cfg.leverage,
            max_notional_usdt=cfg.max_notional_usdt,
        )
    stats = _summarize(
        all_trades,
        deposit_usdt=deposit_usdt,
        risk_pct=risk_pct,
        leverage=cfg.leverage if cfg.use_leverage_sizing else 1,
    )
    stats["target_reached"] = len(all_trades) >= cfg.target_trades
    by_symbol = _aggregate_by_symbol(all_trades)

    payload = {
        "status": "done",
        "mode": "trend_momentum",
        "entry_style": "momentum",
        "long_only": cfg.long_only,
        "rule": (
            f"1h тренд 60/0.8%; импульс; rel_volume>={trend_params.min_rel_volume}; "
            f"TP={trend_params.tp_target_pct * 100:.0f}% от входа; "
            "без swing / отката / 4h / блока уровня; "
            + (
                f"без входов {trend_params.asian_session_hours_utc[0]:02d}-"
                f"{trend_params.asian_session_hours_utc[1]:02d} UTC"
                if trend_params.block_asian_session
                else "часы UTC без фильтра"
            )
            + ("; long only" if cfg.long_only else "")
        ),
        "htf_timeframe": cfg.htf_timeframe if trend_params.require_htf_align else None,
        "min_rel_volume": trend_params.min_rel_volume,
        "min_atr_pct": trend_params.min_atr_pct,
        "leverage": cfg.leverage if cfg.use_leverage_sizing else 1,
        "use_leverage_sizing": cfg.use_leverage_sizing,
        "max_notional_usdt": cfg.max_notional_usdt if cfg.use_leverage_sizing else None,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round(time.perf_counter() - t0, 1),
        "symbols": list(cfg.symbols),
        "timeframe": cfg.timeframe,
        "bars_per_symbol": cfg.bars,
        "target_trades": cfg.target_trades,
        "summary": stats,
        "by_symbol": by_symbol,
        "note": (
            f"{len(cfg.symbols)} монет, TF {cfg.timeframe}, импульсный вход (DEFAULT_TREND_PARAMS), без kNN. "
            "Прибыль USDT = total_R × (депозит × risk%)."
        ),
        "trades": all_trades[-30:],
        "trades_sample_note": "последние 30 сделок в JSON",
    }
    ensure_directories()
    MULTI_RESULT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_multi_symbol_result() -> dict[str, Any]:
    if not MULTI_RESULT_PATH.is_file():
        return {"status": "idle"}
    try:
        return json.loads(MULTI_RESULT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error"}


def _summarize(
    trades: list[dict[str, Any]],
    *,
    deposit_usdt: float,
    risk_pct: float,
    leverage: int = DEFAULT_LEVERAGE,
) -> dict[str, Any]:
    rs = [float(t["r_multiple"]) for t in trades]
    wins = sum(1 for r in rs if r > 0)
    n = len(rs)
    risk_usdt = deposit_usdt * (risk_pct / 100.0)
    total_r = float(np.sum(rs)) if rs else 0.0
    if trades and "pnl_usdt" in trades[0]:
        pnls = [float(t["pnl_usdt"]) for t in trades]
        notionals = [float(t.get("notional_usdt") or 0) for t in trades]
        margins = [float(t.get("margin_usdt") or 0) for t in trades]
        profit_usdt = float(np.sum(pnls))
        avg_notional = float(np.mean(notionals)) if notionals else 0.0
        avg_margin = float(np.mean(margins)) if margins else 0.0
    else:
        profit_usdt = total_r * risk_usdt
        avg_notional = 0.0
        avg_margin = 0.0
    return {
        "trades": n,
        "wins": wins,
        "win_rate_pct": round(100.0 * wins / n, 1) if n else 0.0,
        "avg_r": round(float(np.mean(rs)), 3) if rs else 0.0,
        "total_r": round(total_r, 2),
        "profit_factor": _pf(rs),
        "deposit_usdt": deposit_usdt,
        "risk_pct": risk_pct,
        "risk_usdt_per_trade": round(risk_usdt, 2),
        "leverage": leverage,
        "avg_notional_usdt": round(avg_notional, 2),
        "avg_margin_usdt": round(avg_margin, 2),
        "estimated_profit_usdt": round(profit_usdt, 2),
        "estimated_balance_usdt": round(deposit_usdt + profit_usdt, 2),
        "estimated_return_pct": round(100.0 * profit_usdt / deposit_usdt, 2) if deposit_usdt else 0.0,
    }


def _pf(rs: list[float]) -> float | None:
    gains = sum(r for r in rs if r > 0)
    losses = abs(sum(r for r in rs if r < 0))
    if losses <= 0:
        return None if gains <= 0 else 99.0
    return round(gains / losses, 2)


def run_single_symbol_backtest(
    *,
    cfg: SingleSymbolBacktestConfig | None = None,
    deposit_usdt: float = 1000.0,
    risk_pct: float | None = None,
) -> dict[str, Any]:
    cfg = cfg or SingleSymbolBacktestConfig()
    auto_cfg = load_auto_trade_config()
    auto_cfg.min_probability_pct = 50.0
    if risk_pct is None:
        risk_pct = float(auto_cfg.risk_pct_of_balance)
    if risk_pct <= 0:
        risk_pct = 0.5

    exchange = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    df = _fetch_df(exchange, cfg.symbol, cfg.timeframe, cfg.bars)
    if df is None:
        return {"status": "error", "error": f"no data for {cfg.symbol}"}

    trades = backtest_single_symbol(
        df,
        symbol=cfg.symbol,
        timeframe=cfg.timeframe,
        auto_cfg=auto_cfg,
        stage1_min=cfg.stage1_min_score,
        target_trades=cfg.target_trades,
        trend_only=cfg.trend_only,
    )

    stats = _summarize(trades, deposit_usdt=deposit_usdt, risk_pct=risk_pct)
    stats["target_reached"] = len(trades) >= cfg.target_trades

    payload = {
        "status": "done",
        "mode": "trend_momentum",
        "entry_style": "momentum",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round(time.perf_counter() - t0, 1),
        "symbol": cfg.symbol,
        "timeframe": cfg.timeframe,
        "bars": len(df),
        "target_trades": cfg.target_trades,
        "summary": stats,
        "trades": trades[-20:],
    }
    ensure_directories()
    RESULT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_single_symbol_result() -> dict[str, Any]:
    if not RESULT_PATH.is_file():
        return {"status": "idle"}
    try:
        return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error"}
