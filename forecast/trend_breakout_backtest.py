"""
Бэктест целевой long-стратегии (README): EMA50>EMA200 и цена > EMA200;
пробой max(high) за 20 прошлых баров; объём > EMA(volume,20); RSI14 < cap;
стоп 1.5×ATR14; выход по +2R или стопу (при обоих касаниях — стоп раньше).

По умолчанию: ресемпл OHLCV из 1h → 4h. Риск на сделку: доля депозита (0.5–1%).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .data_loader import load_ohlcv_from_csv
from .indicators import add_basic_indicators


def resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample("4h", label="right", closed="right").agg(agg)
    return out.dropna(how="any")


def run_spec_trend_breakout_long(
    df: pd.DataFrame,
    *,
    breakout_lookback: int = 20,
    rsi_overbought: float = 70.0,
    atr_stop_mult: float = 1.5,
    take_profit_R: float = 2.0,
    risk_per_trade_frac: float = 0.0075,
    cost_bp_roundtrip: float = 4.0,
    col_ema_slow: str = "ema_200",
) -> dict[str, Any]:
    px = df["close"].to_numpy(dtype=float)
    hi = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    atr = df["atr_14"].to_numpy(dtype=float)
    rsi = df["rsi_14"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    vema = df["volume_ema_20"].to_numpy(dtype=float)
    ema50 = df["ema_50"].to_numpy(dtype=float)
    ema_slow = df[col_ema_slow].to_numpy(dtype=float)

    hh_prev = pd.Series(hi).shift(1).rolling(breakout_lookback).max().to_numpy()
    fee_frac = float(cost_bp_roundtrip) / 10000.0

    equity = 1.0
    equity_snapshots: list[float] = [equity]
    pnls: list[float] = []
    outcomes: list[str] = []

    n = len(df)
    valid_mask = np.isfinite(hh_prev) & np.isfinite(ema_slow) & np.isfinite(ema50) & np.isfinite(atr)
    first_ok = np.flatnonzero(valid_mask)
    warmup = int(first_ok[0]) + 3 if first_ok.size else n
    warmup = max(warmup, breakout_lookback + 3)
    warmup = min(warmup, max(0, n - 2))
    j = warmup

    while j < n - 1:
        i = j
        if not np.all(np.isfinite([px[i], atr[i], rsi[i], ema50[i], ema_slow[i], hh_prev[i]])):
            j += 1
            continue

        trend_ok = px[i] > ema_slow[i] and ema50[i] > ema_slow[i]
        breakout = px[i] > hh_prev[i]
        vol_ok = np.isfinite(vema[i]) and vema[i] > 0 and vol[i] > vema[i]
        rsi_ok = rsi[i] < rsi_overbought

        if not (trend_ok and breakout and vol_ok and rsi_ok):
            j += 1
            continue

        entry = float(px[i])
        atrv = max(float(atr[i]), 1e-12)
        risk_per_unit = atr_stop_mult * atrv
        stop_p = entry - risk_per_unit
        tp_p = entry + take_profit_R * risk_per_unit

        if stop_p <= 0 or not np.isfinite(stop_p) or not np.isfinite(tp_p):
            j += 1
            continue

        qty = equity * risk_per_trade_frac / risk_per_unit

        exit_price: float | None = None
        outcome = "eod"
        exit_idx = n - 1

        for k in range(i + 1, n):
            if lo[k] <= stop_p:
                exit_price = float(stop_p)
                outcome = "stop"
                exit_idx = k
                break
            if hi[k] >= tp_p:
                exit_price = float(tp_p)
                outcome = "tp"
                exit_idx = k
                break
        if exit_price is None:
            exit_idx = n - 1
            exit_price = float(px[exit_idx])
            outcome = "eod"

        gross = qty * (exit_price - entry)
        fees = fee_frac * qty * (entry + exit_price)
        equity = equity + gross - fees
        equity_snapshots.append(equity)

        r_mult = (exit_price - entry) / risk_per_unit
        pnls.append(float(r_mult))
        outcomes.append(outcome)

        j = exit_idx + 1

    eq_arr = np.array(equity_snapshots, dtype=float)
    dd = 0.0
    if eq_arr.size > 1:
        mx = np.maximum.accumulate(eq_arr)
        dd = float(np.min((eq_arr - mx) / np.maximum(mx, 1e-12)))

    pnls_a = np.array(pnls, dtype=float)
    wins = pnls_a[pnls_a > 0]
    losses = pnls_a[pnls_a <= 0]

    pf_R = float(np.nan)
    if losses.size:
        s_l = float(losses.sum())
        if s_l < 0:
            pf_R = float(wins.sum() / abs(s_l)) if wins.size else 0.0

    return {
        "strategy": "spec_trend_breakout_long",
        "n_rows": int(n),
        "n_trades": int(len(outcomes)),
        "win_rate": float(np.mean(pnls_a > 0)) if outcomes else 0.0,
        "outcome_counts": {k: outcomes.count(k) for k in sorted(set(outcomes), key=str)},
        "median_R": float(np.median(pnls_a)) if outcomes else float("nan"),
        "avg_R": float(np.mean(pnls_a)) if outcomes else float("nan"),
        "profit_factor_on_R": pf_R,
        "final_equity": float(equity),
        "total_return_pct": float((equity - 1.0) * 100.0),
        "max_drawdown_frac": dd,
        "params": {
            "breakout_lookback": breakout_lookback,
            "rsi_overbought_cap": rsi_overbought,
            "atr_stop_mult": atr_stop_mult,
            "take_profit_R": take_profit_R,
            "risk_per_trade_frac": risk_per_trade_frac,
            "cost_bp_roundtrip": cost_bp_roundtrip,
            "ema_slow_col": col_ema_slow,
        },
    }


def _default_csv(ns: argparse.Namespace) -> Path:
    if ns.csv:
        return Path(ns.csv)
    root = Path(__file__).resolve().parents[1]
    sym = str(ns.symbol).replace("/", "")
    return root / "data" / "raw" / f"{sym}_{ns.timeframe_raw}.csv"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=None)
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe_raw", default="1h")
    p.add_argument("--no-resample-4h", action="store_true")
    p.add_argument("--risk-pct", type=float, default=0.0075)
    p.add_argument("--rsi-max", type=float, default=70.0)
    p.add_argument("--cost-bp-rt", type=float, default=4.0)
    ns = p.parse_args(argv)

    path = _default_csv(ns)
    if not path.is_file():
        print(json.dumps({"error": "csv_missing", "path": str(path)}, indent=2))
        return 1

    df = load_ohlcv_from_csv(str(path)).sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        print(json.dumps({"error": "datetime_index_required"}, indent=2))
        return 1
    if not ns.no_resample_4h:
        df = resample_to_4h(df)
    df = add_basic_indicators(df)
    if df.empty or "ema_200" not in df.columns:
        print(json.dumps({"error": "empty_or_no_ema200"}, indent=2))
        return 1

    rep = run_spec_trend_breakout_long(
        df,
        risk_per_trade_frac=ns.risk_pct,
        rsi_overbought=ns.rsi_max,
        cost_bp_roundtrip=ns.cost_bp_rt,
    )
    rep["csv"] = str(path)
    rep["bars_after_resample"] = int(len(df))
    rep["profitable_in_sample"] = bool(
        rep["n_trades"] > 0 and rep["final_equity"] > 1.0 and (rep["profit_factor_on_R"] >= 1 or rep["avg_R"] > 0)
    )
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
