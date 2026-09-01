"""Walk-forward: лимитный вход на откате vs рыночный вход, на одних сигналах.

Сигнал — 15m trend (как baseline). Вместо входа по close ставим лимитку
ниже (long) / выше (short) на LIM_OFFSET_ATR*ATR со сроком жизни LIM_TTL_BARS.
Вход maker, выход taker; у рыночного варианта taker обе стороны.
R считается без вшитой комиссии (_trade_r не используется).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import ccxt
import numpy as np
import pandas as pd

from .auto_trader import load_auto_trade_config, validate_setup
from .market_scanner import _adjust_stage1_for_direction, _stage1_snapshot
from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .signal_combiner import compute_volume_scores
from .single_symbol_backtest import COOLDOWN_BARS, _build_candidate, _summarize
from .strategy_config import yaml_section
from .tf_backtest import (
    DEFAULT_SYMBOLS,
    MAX_HOLD_BARS_BY_TF,
    STEP_BY_TF,
    _fetch_df_date_window,
)
from .trend_rules import build_trend_plan, trend_params_for_timeframe
from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env


def _raw_r(side: str, entry: float, exit_px: float, stop: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if side == "long":
        return (exit_px - entry) / risk
    return (entry - exit_px) / risk


def _exit_from(
    df: pd.DataFrame,
    start_i: int,
    *,
    side: str,
    stop: float,
    tp: float,
    max_bars: int,
    check_start_bar_stop: bool,
) -> tuple[float, str, int]:
    """Выход stop/tp/time начиная с бара start_i (стоп на баре входа — консервативно)."""
    if check_start_bar_stop and start_i < len(df):
        row = df.iloc[start_i]
        if side == "long" and float(row["low"]) <= stop:
            return stop, "stop", start_i
        if side == "short" and float(row["high"]) >= stop:
            return stop, "stop", start_i
    end = min(start_i + 1 + max_bars, len(df))
    for j in range(start_i + 1, end):
        row = df.iloc[j]
        hi, lo = float(row["high"]), float(row["low"])
        if side == "long":
            if lo <= stop:
                return stop, "stop", j
            if hi >= tp:
                return tp, "tp", j
        else:
            if hi >= stop:
                return stop, "stop", j
            if lo <= tp:
                return tp, "tp", j
    exit_px = float(df.iloc[end - 1]["close"])
    return exit_px, "time", end - 1


def _cost_r(entry: float, stop: float, *, entry_fee_pct: float, exit_fee_pct: float) -> float:
    risk_frac = abs(entry - stop) / max(entry, 1e-12)
    return ((entry_fee_pct + exit_fee_pct) / 100.0) / max(risk_frac, 1e-9)


def _signals_for_symbol(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    auto_cfg,
    stage1_min: float,
    trend_params,
) -> list[dict[str, Any]]:
    """Все сигналы тренда (бар, план) без симуляции входа."""
    step = STEP_BY_TF.get(timeframe, 2)
    max_hold = MAX_HOLD_BARS_BY_TF.get(timeframe, 48)
    out: list[dict[str, Any]] = []
    next_i = 72
    end = len(df) - max_hold - 1
    while next_i < end:
        sub = df.iloc[: next_i + 1]
        snap = _stage1_snapshot(sub)
        plan = build_trend_plan(sub, snap, trend_params)
        if plan is None:
            next_i += step
            continue
        last = sub.iloc[-1]
        close = float(last["close"])
        vol_up, vol_down = compute_volume_scores(sub)
        stage1 = _adjust_stage1_for_direction(
            float(snap["stage1_score"]),
            direction=str(plan["direction"]),
            rel_vol=float(snap["context"]["rel_volume"]),
            candle_bullish=close > float(last["open"]),
            close=close,
            support=float(plan["trend_support"]),
            resistance=float(plan["trend_resistance"]),
            vol_up=vol_up,
            vol_down=vol_down,
        )
        if stage1 < stage1_min:
            next_i += step
            continue
        cand = _build_candidate(symbol=symbol, snap=snap, plan=plan, stage1=stage1, df=sub)
        ok, _ = validate_setup(cand, auto_cfg)
        if not ok:
            next_i += step
            continue
        out.append({"i": next_i, "plan": plan, "atr": float(last["atr_14"]), "close": close})
        next_i += step
    return out


def _simulate_market(
    df: pd.DataFrame,
    signals: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    tp_pct: float,
    taker_pct: float,
    slip_pct: float,
) -> list[dict[str, Any]]:
    max_hold = MAX_HOLD_BARS_BY_TF.get(timeframe, 48)
    trades: list[dict[str, Any]] = []
    busy_until = -1
    for sig in signals:
        i = sig["i"]
        if i <= busy_until:
            continue
        plan = sig["plan"]
        side = str(plan["direction"]).lower()
        entry = float(sig["close"])
        stop = float(plan["stop"])
        tp = float(plan["target_2"])
        exit_px, reason, exit_i = _exit_from(
            df, i, side=side, stop=stop, tp=tp, max_bars=max_hold, check_start_bar_stop=False
        )
        r = _raw_r(side, entry, exit_px, stop)
        cost = _cost_r(entry, stop, entry_fee_pct=taker_pct + slip_pct, exit_fee_pct=taker_pct + slip_pct)
        trades.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": str(df.index[i]),
                "entry": entry,
                "stop": stop,
                "tp": tp,
                "exit_reason": reason,
                "r_multiple": round(r - cost, 3),
                "r_gross": round(r, 3),
                "cost_r": round(cost, 3),
                "win": (r - cost) > 0,
            }
        )
        busy_until = exit_i + COOLDOWN_BARS
    return trades


def _simulate_limit(
    df: pd.DataFrame,
    signals: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    tp_pct: float,
    offset_atr: float,
    ttl_bars: int,
    maker_pct: float,
    taker_pct: float,
    slip_pct: float,
) -> tuple[list[dict[str, Any]], int]:
    max_hold = MAX_HOLD_BARS_BY_TF.get(timeframe, 48)
    trades: list[dict[str, Any]] = []
    n_signals = 0
    busy_until = -1
    for sig in signals:
        i = sig["i"]
        if i <= busy_until:
            continue
        n_signals += 1
        plan = sig["plan"]
        side = str(plan["direction"]).lower()
        atr = float(sig["atr"])
        close = float(sig["close"])
        stop = float(plan["stop"])
        limit = close - offset_atr * atr if side == "long" else close + offset_atr * atr
        # лимитка не должна быть за стопом
        if side == "long" and limit <= stop:
            busy_until = i  # сигнал пропущен
            continue
        if side == "short" and limit >= stop:
            busy_until = i
            continue

        fill_i = -1
        fill_px = 0.0
        for j in range(i + 1, min(i + 1 + ttl_bars, len(df))):
            row = df.iloc[j]
            o = float(row["open"])
            if side == "long":
                if float(row["low"]) <= limit:
                    fill_px = min(limit, o)
                    fill_i = j
                    break
            else:
                if float(row["high"]) >= limit:
                    fill_px = max(limit, o)
                    fill_i = j
                    break
        if fill_i < 0:
            busy_until = i + ttl_bars  # ордер истёк
            continue

        tp = fill_px * (1.0 + tp_pct) if side == "long" else fill_px * (1.0 - tp_pct)
        exit_px, reason, exit_i = _exit_from(
            df,
            fill_i,
            side=side,
            stop=stop,
            tp=tp,
            max_bars=max_hold,
            check_start_bar_stop=True,
        )
        r = _raw_r(side, fill_px, exit_px, stop)
        cost = _cost_r(fill_px, stop, entry_fee_pct=maker_pct, exit_fee_pct=taker_pct + slip_pct)
        trades.append(
            {
                "symbol": symbol,
                "side": side,
                "entry_time": str(df.index[fill_i]),
                "signal_time": str(df.index[i]),
                "entry": fill_px,
                "stop": stop,
                "tp": tp,
                "exit_reason": reason,
                "r_multiple": round(r - cost, 3),
                "r_gross": round(r, 3),
                "cost_r": round(cost, 3),
                "win": (r - cost) > 0,
            }
        )
        busy_until = exit_i + COOLDOWN_BARS
    return trades, n_signals


def _windows(end: datetime, n: int, days: int) -> list[tuple[str, str, str]]:
    out = []
    for i in range(n):
        w_end = end - timedelta(days=i * days)
        w_start = w_end - timedelta(days=days)
        out.append((f"W{i + 1}", w_start.strftime("%Y-%m-%d"), w_end.strftime("%Y-%m-%d")))
    return out


def _bucket(trades, windows, *, deposit, risk):
    rows = []
    for label, start_s, end_s in windows:
        start = pd.Timestamp(start_s, tz="UTC")
        end = pd.Timestamp(end_s, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
        subset = [t for t in trades if start <= pd.Timestamp(t["entry_time"]) <= end]
        stats = _summarize(subset, deposit_usdt=deposit, risk_pct=risk, leverage=1)
        rows.append(
            {
                "window": label,
                "start": start_s,
                "end": end_s,
                "trades": stats["trades"],
                "win_rate_pct": stats["win_rate_pct"],
                "total_r": stats["total_r"],
                "profit_factor": stats.get("profit_factor"),
                "pnl_usdt": stats["estimated_profit_usdt"],
                "pf_gt_1": bool(stats.get("profit_factor") is not None and float(stats["profit_factor"]) > 1.0),
            }
        )
    return rows


def main() -> int:
    load_project_env(force=False)
    tf = os.environ.get("LIM_TF", "15m").strip() or "15m"
    n_win = int(os.environ.get("LIM_WINDOWS", "6"))
    win_days = int(os.environ.get("LIM_WINDOW_DAYS", "30"))
    deposit = float(os.environ.get("LIM_DEPOSIT", "1000"))
    risk = float(os.environ.get("LIM_RISK_PCT", "0.5"))
    offset_atr = float(os.environ.get("LIM_OFFSET_ATR", "0.5"))
    ttl_bars = int(os.environ.get("LIM_TTL_BARS", "16"))
    maker_pct = float(os.environ.get("LIM_MAKER_PCT", "0.0"))
    taker_pct = float(os.environ.get("LIM_TAKER_PCT", "0.1"))
    slip_pct = float(os.environ.get("LIM_SLIPPAGE_PCT", "0.05"))

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=n_win * win_days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    windows = _windows(end, n_win, win_days)

    symbols_env = os.environ.get("LIM_SYMBOLS", "").strip()
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    else:
        symbols = load_filtered_symbols() or DEFAULT_SYMBOLS

    scan_cfg = trend_scan_config_from_env()
    params = trend_params_for_timeframe(tf, base=trend_params_from_yaml())
    tp_pct = float(params.tp_target_pct)

    at_yaml = yaml_section("auto_trade")
    auto_cfg = load_auto_trade_config(at_yaml)
    auto_cfg.min_probability_pct = float(at_yaml.get("min_probability_pct", 0))
    auto_cfg.min_score = float(at_yaml.get("min_score", 12))
    auto_cfg.min_risk_reward = float(at_yaml.get("min_risk_reward", 1.2))
    auto_cfg.allow_level_breakout = bool(at_yaml.get("allow_level_breakout", False))
    auto_cfg.allow_triangle = bool(at_yaml.get("allow_triangle", False))

    print(
        f"[lim] {len(symbols)} sym {tf}, {n_win}×{win_days}d {start_s}..{end_s}; "
        f"limit=-{offset_atr}*ATR ttl={ttl_bars} bars; maker={maker_pct}% taker={taker_pct}%+slip {slip_pct}%",
        flush=True,
    )

    exchange = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    win_start = pd.Timestamp(start_s, tz="UTC")
    win_end = pd.Timestamp(end_s, tz="UTC")

    trades_mkt: list[dict[str, Any]] = []
    trades_lim: list[dict[str, Any]] = []
    total_signals = 0
    total_fills = 0
    for symbol in symbols:
        df = _fetch_df_date_window(exchange, symbol, tf, start=win_start, end=win_end)
        if df is None:
            print(f"[lim] skip {symbol}: no data", flush=True)
            continue
        signals = _signals_for_symbol(
            df,
            symbol=symbol,
            timeframe=tf,
            auto_cfg=auto_cfg,
            stage1_min=scan_cfg.stage1_min_score,
            trend_params=params,
        )
        mkt = _simulate_market(
            df, signals, symbol=symbol, timeframe=tf, tp_pct=tp_pct,
            taker_pct=taker_pct, slip_pct=slip_pct,
        )
        lim, n_sig = _simulate_limit(
            df, signals, symbol=symbol, timeframe=tf, tp_pct=tp_pct,
            offset_atr=offset_atr, ttl_bars=ttl_bars,
            maker_pct=maker_pct, taker_pct=taker_pct, slip_pct=slip_pct,
        )
        trades_mkt.extend(mkt)
        trades_lim.extend(lim)
        total_signals += n_sig
        total_fills += len(lim)
        print(
            f"[lim] {symbol}: signals={len(signals)} market={len(mkt)} limit_fills={len(lim)}",
            flush=True,
        )

    fill_rate = round(100.0 * total_fills / max(total_signals, 1), 1)
    sum_mkt = _summarize(trades_mkt, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    sum_lim = _summarize(trades_lim, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    rows_mkt = _bucket(trades_mkt, windows, deposit=deposit, risk=risk)
    rows_lim = _bucket(trades_lim, windows, deposit=deposit, risk=risk)

    def _pblock(title, rows, summ):
        print(f"\n[lim] === {title} ===", flush=True)
        for r in rows:
            print(
                f"  {r['window']} {r['start']}..{r['end']}: n={r['trades']} win%={r['win_rate_pct']} "
                f"R={r['total_r']} PF={r['profit_factor']} PnL=${r['pnl_usdt']}",
                flush=True,
            )
        pf_ok = sum(1 for r in rows if r["pf_gt_1"])
        print(
            f"  TOTAL n={summ['trades']} R={summ['total_r']} PF={summ.get('profit_factor')} "
            f"PnL=${summ['estimated_profit_usdt']} | PF>1 окон: {pf_ok}/{len(rows)}",
            flush=True,
        )

    _pblock("MARKET taker both sides (net)", rows_mkt, sum_mkt)
    _pblock(f"LIMIT maker entry (net), fill {fill_rate}% сигналов", rows_lim, sum_lim)

    print(
        f"\n[lim] VERDICT: market R={sum_mkt['total_r']} PF={sum_mkt.get('profit_factor')} n={sum_mkt['trades']} | "
        f"limit R={sum_lim['total_r']} PF={sum_lim.get('profit_factor')} n={sum_lim['trades']} (fill {fill_rate}%)",
        flush=True,
    )

    out = Path(__file__).resolve().parent / "data/processed" / f"limit_entry_{n_win}x{win_days}d_{tf}.json"
    payload = {
        "status": "done",
        "timeframe": tf,
        "period": {"start": start_s, "end": end_s},
        "params": {
            "offset_atr": offset_atr,
            "ttl_bars": ttl_bars,
            "maker_pct": maker_pct,
            "taker_pct": taker_pct,
            "slippage_pct": slip_pct,
            "tp_pct": tp_pct,
        },
        "fill_rate_pct": fill_rate,
        "duration_sec": round(time.perf_counter() - t0, 1),
        "market": {"summary": sum_mkt, "by_window": rows_mkt},
        "limit": {"summary": sum_lim, "by_window": rows_lim},
        "trades_market": trades_mkt,
        "trades_limit": trades_lim,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[lim] saved: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
