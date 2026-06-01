"""
Grid search for TrendPullbackParams on cached OHLCV (10 alts, 1h).
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

import ccxt
import numpy as np

from .auto_trader import load_auto_trade_config
from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .single_symbol_backtest import (
    TARGET_TRADES,
    _summarize,
    backtest_single_symbol,
)
from .tf_backtest import BARS_BY_TF, DEFAULT_SYMBOLS, _fetch_df
from .trend_rules import DEFAULT_PULLBACK_PARAMS, TrendPullbackParams

SWEEP_RESULT_PATH = PROCESSED_DATA_DIR / "trend_param_sweep_latest.json"
MIN_TRADES_SOFT = 50
MIN_TRADES_HARD = 25


def _score(summary: dict[str, Any]) -> float:
    """Приоритет total_R при достаточном числе сделок."""
    n = int(summary.get("trades", 0))
    total_r = float(summary.get("total_r", 0.0))
    pf = summary.get("profit_factor")
    pf_bonus = 0.1 * min(float(pf), 2.5) if pf is not None else 0.0
    if n < MIN_TRADES_HARD:
        return total_r - (MIN_TRADES_HARD - n) * 1.2
    if n < MIN_TRADES_SOFT:
        return total_r * (n / MIN_TRADES_SOFT) + pf_bonus
    return total_r + pf_bonus


def _load_symbol_dfs(
    symbols: tuple[str, ...],
    timeframe: str,
    bars: int,
) -> dict[str, Any]:
    exchange = ccxt.binance({"enableRateLimit": True})
    out: dict[str, Any] = {}
    for sym in symbols:
        df = _fetch_df(exchange, sym, timeframe, bars)
        if df is not None:
            out[sym] = df
    return out


def _run_params_on_cache(
    params: TrendPullbackParams,
    dfs: dict[str, Any],
    *,
    timeframe: str,
    target_trades: int,
    stage1_min: float,
    stage1_relax: float,
    dfs_htf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    auto_cfg = load_auto_trade_config()
    auto_cfg.min_probability_pct = 50.0
    all_trades: list[dict[str, Any]] = []

    for symbol, df in dfs.items():
        if len(all_trades) >= target_trades:
            break
        need = target_trades - len(all_trades)
        df_htf = (dfs_htf or {}).get(symbol) if params.require_htf_align else None
        all_trades.extend(
            backtest_single_symbol(
                df,
                symbol=symbol,
                timeframe=timeframe,
                auto_cfg=auto_cfg,
                stage1_min=stage1_min,
                target_trades=need,
                trend_params=params,
                df_htf=df_htf,
            )
        )

    if len(all_trades) < target_trades:
        for symbol, df in dfs.items():
            if len(all_trades) >= target_trades:
                break
            need = target_trades - len(all_trades)
            df_htf = (dfs_htf or {}).get(symbol) if params.require_htf_align else None
            extra = backtest_single_symbol(
                df,
                symbol=symbol,
                timeframe=timeframe,
                auto_cfg=auto_cfg,
                stage1_min=stage1_relax,
                target_trades=need,
                trend_params=params,
                df_htf=df_htf,
            )
            seen = {(t["symbol"], t["entry_time"]) for t in all_trades}
            for t in extra:
                key = (t["symbol"], t["entry_time"])
                if key not in seen:
                    all_trades.append(t)
                    seen.add(key)
                if len(all_trades) >= target_trades:
                    break

    all_trades = all_trades[:target_trades]
    summary = _summarize(all_trades, deposit_usdt=1000.0, risk_pct=0.5)
    summary["score"] = round(_score(summary), 3)
    return summary


def _param_grid() -> list[TrendPullbackParams]:
    grid: list[TrendPullbackParams] = []
    base = DEFAULT_PULLBACK_PARAMS

    # baseline: без отката (как раньше +14R)
    grid.append(replace(base, require_pullback=False))

    # ~320 комбинаций (+ baseline без отката)
    long_mins = (0.10, 0.15, 0.20, 0.25)
    long_maxs = (0.52, 0.60, 0.68, 0.76)
    short_mins = (0.38, 0.45, 0.52)
    short_maxs = (0.72, 0.80, 0.88)
    swings = (0.001, 0.002, 0.0035)
    lookbacks = (8, 12, 16)

    for lm, lx, sm, sx, sw, lb in itertools.product(
        long_mins, long_maxs, short_mins, short_maxs, swings, lookbacks
    ):
        if lm >= lx or sm >= sx:
            continue
        p = TrendPullbackParams(
            require_pullback=True,
            long_pos_min=lm,
            long_pos_max=lx,
            short_pos_min=sm,
            short_pos_max=sx,
            min_pullback_from_swing_pct=sw,
            pullback_lookback=lb,
        )
        if p.valid():
            grid.append(p)
    return grid


def run_trend_param_sweep(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframe: str = "1h",
    target_trades: int = TARGET_TRADES,
    max_combos: int | None = None,
) -> dict[str, Any]:
    load_project_env(force=True)
    bars = BARS_BY_TF.get(timeframe, 1000)
    t0 = time.perf_counter()
    print(f"[sweep] loading {len(symbols)} symbols...", flush=True)
    dfs = _load_symbol_dfs(symbols, timeframe, bars)
    if not dfs:
        return {"status": "error", "error": "no data"}
    dfs_htf: dict[str, Any] | None = None
    if timeframe == "1h":
        htf_bars = BARS_BY_TF.get("4h", 800)
        dfs_htf = _load_symbol_dfs(symbols, "4h", htf_bars)

    grid = _param_grid()
    if max_combos:
        grid = grid[:max_combos]
    print(f"[sweep] testing {len(grid)} parameter sets...", flush=True)

    results: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    best_score = -1e9

    for i, params in enumerate(grid):
        summary = _run_params_on_cache(
            params,
            dfs,
            timeframe=timeframe,
            target_trades=target_trades,
            stage1_min=18.0,
            stage1_relax=12.0,
            dfs_htf=dfs_htf,
        )
        row = {
            "rank_score": summary["score"],
            "params": asdict(params),
            "summary": summary,
        }
        results.append(row)
        sc = float(summary["score"])
        if sc > best_score:
            best_score = sc
            best = row
        if (i + 1) % 50 == 0 or i == len(grid) - 1:
            print(
                f"[sweep] {i + 1}/{len(grid)} best_score={best_score:.2f} "
                f"trades={best['summary']['trades'] if best else 0} total_R={best['summary']['total_r'] if best else 0}",
                flush=True,
            )

    results.sort(key=lambda r: r["rank_score"], reverse=True)
    top10 = results[:10]
    by_total_r = sorted(
        results,
        key=lambda r: (r["summary"]["total_r"], r["summary"]["trades"]),
        reverse=True,
    )
    best_by_r = by_total_r[0] if by_total_r else best

    payload = {
        "status": "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round(time.perf_counter() - t0, 1),
        "symbols": list(dfs.keys()),
        "timeframe": timeframe,
        "combos_tested": len(grid),
        "best": best,
        "best_by_total_r": best_by_r,
        "top10": top10,
        "top5_by_total_r": by_total_r[:5],
        "scoring": (
            f"score=total_R, штраф если trades<{MIN_TRADES_SOFT}, "
            f"сильный штраф если<{MIN_TRADES_HARD}"
        ),
    }
    ensure_directories()
    SWEEP_RESULT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_sweep_result() -> dict[str, Any]:
    if not SWEEP_RESULT_PATH.is_file():
        return {"status": "idle"}
    try:
        return json.loads(SWEEP_RESULT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error"}
