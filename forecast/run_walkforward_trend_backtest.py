"""Walk-forward 6×30d: 15m trend baseline vs BTC-regime + HTF align."""

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

from .auto_trader import load_auto_trade_config
from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import (
    UNLIMITED_TARGET_TRADES,
    _summarize,
    backtest_single_symbol,
)
from .strategy_config import yaml_section
from .tf_backtest import DEFAULT_SYMBOLS, _fetch_df_date_window
from .trend_rules import trend_params_for_timeframe
from .trend_scanner import (
    BTC_REGIME_SYMBOL,
    BTC_REGIME_TIMEFRAME,
    trend_params_from_yaml,
    trend_scan_config_from_env,
)


def _windows(end: datetime, n: int = 6, days: int = 30) -> list[tuple[str, str, str]]:
    """Newest-first labels: W1 = last 30d, W6 = farthest."""
    out: list[tuple[str, str, str]] = []
    for i in range(n):
        w_end = end - timedelta(days=i * days)
        w_start = w_end - timedelta(days=days)
        label = f"W{i + 1}"
        out.append((label, w_start.strftime("%Y-%m-%d"), w_end.strftime("%Y-%m-%d")))
    return out


def _parse_entry(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts).tz_convert("UTC") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts, tz="UTC")


def apply_costs(
    trades: list[dict[str, Any]],
    *,
    fee_pct_per_side: float,
    slippage_pct_per_side: float,
) -> list[dict[str, Any]]:
    """Копия сделок с r_multiple за вычетом round-trip издержек.

    Издержки в R: (2*(fee+slip)/100) / (|entry-stop|/entry) — чем ближе стоп,
    тем дороже комиссия в единицах риска.
    """
    cost_frac = 2.0 * (fee_pct_per_side + slippage_pct_per_side) / 100.0
    out = []
    for t in trades:
        entry = float(t["entry"])
        stop = float(t["stop"])
        risk_frac = abs(entry - stop) / max(entry, 1e-12)
        cost_r = cost_frac / max(risk_frac, 1e-9)
        nt = dict(t)
        nt["r_multiple"] = round(float(t["r_multiple"]) - cost_r, 3)
        nt["cost_r"] = round(cost_r, 3)
        nt["win"] = nt["r_multiple"] > 0
        out.append(nt)
    return out


def _bucket_trades(
    trades: list[dict[str, Any]],
    windows: list[tuple[str, str, str]],
    *,
    deposit: float,
    risk: float,
) -> list[dict[str, Any]]:
    rows = []
    for label, start_s, end_s in windows:
        start = pd.Timestamp(start_s, tz="UTC")
        end = pd.Timestamp(end_s, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
        subset = []
        for t in trades:
            et = _parse_entry(str(t["entry_time"]))
            if start <= et <= end:
                subset.append(t)
        stats = _summarize(subset, deposit_usdt=deposit, risk_pct=risk, leverage=1)
        long_n = sum(1 for t in subset if str(t.get("side")) == "long")
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
                "long": long_n,
                "short": stats["trades"] - long_n,
                "pf_gt_1": bool(stats.get("profit_factor") is not None and float(stats["profit_factor"]) > 1.0),
            }
        )
    return rows


def _run_variant(
    *,
    label: str,
    symbols: tuple[str, ...],
    dfs: dict[str, pd.DataFrame],
    dfs_htf: dict[str, pd.DataFrame] | None,
    df_btc: pd.DataFrame | None,
    trend_params,
    stage1: float,
    auto_cfg,
    tf: str,
    btc_filter: bool,
) -> list[dict[str, Any]]:
    all_trades: list[dict[str, Any]] = []
    for symbol in symbols:
        df = dfs.get(symbol)
        if df is None:
            continue
        df_htf = (dfs_htf or {}).get(symbol) if trend_params.require_htf_align else None
        if trend_params.require_htf_align and df_htf is None:
            continue
        sym_trades = backtest_single_symbol(
            df,
            symbol=symbol,
            timeframe=tf,
            auto_cfg=auto_cfg,
            stage1_min=stage1,
            target_trades=UNLIMITED_TARGET_TRADES,
            trend_only=True,
            trend_params=trend_params,
            df_htf=df_htf,
            long_only=False,
            df_btc=df_btc if btc_filter else None,
            btc_regime_filter=btc_filter,
        )
        all_trades.extend(sym_trades)
        print(f"[wf] {label} {symbol}: +{len(sym_trades)} (total {len(all_trades)})", flush=True)
    return all_trades


def main() -> int:
    load_project_env(force=False)
    tf = os.environ.get("WF_TF", "15m").strip() or "15m"
    htf = os.environ.get("WF_HTF", "1h").strip() or "1h"
    n_win = int(os.environ.get("WF_WINDOWS", "6"))
    win_days = int(os.environ.get("WF_WINDOW_DAYS", "30"))
    deposit = float(os.environ.get("WF_DEPOSIT", "1000"))
    risk = float(os.environ.get("WF_RISK_PCT", "0.5"))

    end = datetime.now(timezone.utc)
    total_days = n_win * win_days
    start = end - timedelta(days=total_days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    windows = _windows(end, n=n_win, days=win_days)

    use_filtered = os.environ.get("WF_USE_FILTERED", "1").strip().lower() not in ("0", "false", "no")
    symbols_env = os.environ.get("WF_SYMBOLS", "").strip()
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or DEFAULT_SYMBOLS
    else:
        symbols = DEFAULT_SYMBOLS

    scan_cfg = trend_scan_config_from_env()
    base = trend_params_for_timeframe(tf, base=trend_params_from_yaml())
    params_a = replace(base, require_htf_align=False)
    params_b = replace(base, require_htf_align=True, htf_timeframe=htf)

    at_yaml = yaml_section("auto_trade")
    auto_cfg = load_auto_trade_config(at_yaml)
    auto_cfg.min_probability_pct = float(at_yaml.get("min_probability_pct", 0))
    auto_cfg.min_score = float(at_yaml.get("min_score", 12))
    auto_cfg.min_risk_reward = float(at_yaml.get("min_risk_reward", 1.2))
    auto_cfg.allow_level_breakout = bool(at_yaml.get("allow_level_breakout", False))
    auto_cfg.allow_triangle = bool(at_yaml.get("allow_triangle", False))

    print(
        f"[wf] load {len(symbols)} symbols {tf} (+{htf}/BTC4h for B), "
        f"{total_days}d {start_s}..{end_s}, {n_win}×{win_days}d",
        flush=True,
    )
    exchange = ccxt.binance({"enableRateLimit": True})
    t0 = time.perf_counter()
    win_start = pd.Timestamp(start_s, tz="UTC")
    win_end = pd.Timestamp(end_s, tz="UTC")

    dfs: dict[str, pd.DataFrame] = {}
    dfs_htf: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = _fetch_df_date_window(exchange, symbol, tf, start=win_start, end=win_end)
        if df is None:
            print(f"[wf] skip {symbol}: no {tf}", flush=True)
            continue
        dfs[symbol] = df
        df_h = _fetch_df_date_window(exchange, symbol, htf, start=win_start, end=win_end)
        if df_h is not None:
            dfs_htf[symbol] = df_h
        print(f"[wf] loaded {symbol}: {tf}={len(df)} htf={len(df_h) if df_h is not None else 0}", flush=True)

    df_btc = _fetch_df_date_window(
        exchange, BTC_REGIME_SYMBOL, BTC_REGIME_TIMEFRAME, start=win_start, end=win_end
    )
    print(f"[wf] BTC 4h bars={len(df_btc) if df_btc is not None else 0}", flush=True)

    trades_a = _run_variant(
        label="A_baseline",
        symbols=tuple(dfs.keys()),
        dfs=dfs,
        dfs_htf=None,
        df_btc=None,
        trend_params=params_a,
        stage1=scan_cfg.stage1_min_score,
        auto_cfg=auto_cfg,
        tf=tf,
        btc_filter=False,
    )
    trades_b = _run_variant(
        label="B_btc+htf",
        symbols=tuple(dfs.keys()),
        dfs=dfs,
        dfs_htf=dfs_htf,
        df_btc=df_btc,
        trend_params=params_b,
        stage1=scan_cfg.stage1_min_score,
        auto_cfg=auto_cfg,
        tf=tf,
        btc_filter=True,
    )

    fee = float(os.environ.get("WF_FEE_PCT", "0.1"))  # taker, за сторону
    slip = float(os.environ.get("WF_SLIPPAGE_PCT", "0.05"))  # за сторону
    trades_a_net = apply_costs(trades_a, fee_pct_per_side=fee, slippage_pct_per_side=slip)
    trades_b_net = apply_costs(trades_b, fee_pct_per_side=fee, slippage_pct_per_side=slip)

    rows_a = _bucket_trades(trades_a, windows, deposit=deposit, risk=risk)
    rows_b = _bucket_trades(trades_b, windows, deposit=deposit, risk=risk)
    rows_a_net = _bucket_trades(trades_a_net, windows, deposit=deposit, risk=risk)
    rows_b_net = _bucket_trades(trades_b_net, windows, deposit=deposit, risk=risk)
    sum_a = _summarize(trades_a, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    sum_b = _summarize(trades_b, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    sum_a_net = _summarize(trades_a_net, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    sum_b_net = _summarize(trades_b_net, deposit_usdt=deposit, risk_pct=risk, leverage=1)
    avg_cost_a = (
        round(float(np.mean([t["cost_r"] for t in trades_a_net])), 3) if trades_a_net else 0.0
    )
    avg_cost_b = (
        round(float(np.mean([t["cost_r"] for t in trades_b_net])), 3) if trades_b_net else 0.0
    )

    def _score(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pf_ok = sum(1 for r in rows if r["pf_gt_1"])
        rs = [float(r["total_r"]) for r in rows]
        return {
            "windows_pf_gt_1": pf_ok,
            "windows_total": len(rows),
            "windows_positive_r": sum(1 for x in rs if x > 0),
            "sum_r": round(float(np.sum(rs)), 2) if rs else 0.0,
            "median_r": round(float(np.median(rs)), 2) if rs else 0.0,
        }

    score_a, score_b = _score(rows_a), _score(rows_b)
    score_a_net, score_b_net = _score(rows_a_net), _score(rows_b_net)

    def _print_block(title: str, rows: list[dict[str, Any]], summ: dict[str, Any], score: dict[str, Any]) -> None:
        print(f"\n[wf] === {title} ===", flush=True)
        for r in rows:
            print(
                f"  {r['window']} {r['start']}..{r['end']}: n={r['trades']} win%={r['win_rate_pct']} "
                f"R={r['total_r']} PF={r['profit_factor']} PnL=${r['pnl_usdt']}",
                flush=True,
            )
        print(
            f"  TOTAL n={summ['trades']} R={summ['total_r']} PF={summ.get('profit_factor')} | {score}",
            flush=True,
        )

    _print_block("A baseline GROSS (без издержек)", rows_a, sum_a, score_a)
    _print_block(f"A baseline NET (fee {fee}%+slip {slip}%/сторона, avg cost {avg_cost_a}R)", rows_a_net, sum_a_net, score_a_net)
    _print_block("B BTC+HTF GROSS", rows_b, sum_b, score_b)
    _print_block(f"B BTC+HTF NET (avg cost {avg_cost_b}R)", rows_b_net, sum_b_net, score_b_net)

    print(
        f"\n[wf] VERDICT: A gross PF>1 {score_a['windows_pf_gt_1']}/{n_win} → net {score_a_net['windows_pf_gt_1']}/{n_win}; "
        f"B gross {score_b['windows_pf_gt_1']}/{n_win} → net {score_b_net['windows_pf_gt_1']}/{n_win}; "
        f"sum_R A {score_a['sum_r']}→{score_a_net['sum_r']}, B {score_b['sum_r']}→{score_b_net['sum_r']}",
        flush=True,
    )

    trade_keys = ("symbol", "side", "entry_time", "entry", "stop", "tp", "exit_reason", "r_multiple")
    out = Path(__file__).resolve().parent / "data/processed" / f"walkforward_{n_win}x{win_days}d_{tf}.json"
    payload = {
        "status": "done",
        "timeframe": tf,
        "htf": htf,
        "period": {"start": start_s, "end": end_s},
        "windows": windows,
        "symbols": list(dfs.keys()),
        "deposit_usdt": deposit,
        "risk_pct": risk,
        "costs": {"fee_pct_per_side": fee, "slippage_pct_per_side": slip, "avg_cost_r_a": avg_cost_a, "avg_cost_r_b": avg_cost_b},
        "duration_sec": round(time.perf_counter() - t0, 1),
        "A_baseline": {"summary": sum_a, "by_window": rows_a, "score": score_a},
        "A_baseline_net": {"summary": sum_a_net, "by_window": rows_a_net, "score": score_a_net},
        "B_btc_htf": {"summary": sum_b, "by_window": rows_b, "score": score_b},
        "B_btc_htf_net": {"summary": sum_b_net, "by_window": rows_b_net, "score": score_b_net},
        "all_trades_a": [{k: t.get(k) for k in trade_keys} for t in trades_a],
        "all_trades_b": [{k: t.get(k) for k in trade_keys} for t in trades_b],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[wf] saved: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
