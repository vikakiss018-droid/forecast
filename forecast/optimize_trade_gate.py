"""
Grid search over trade_gate parameters using the same enriched walk-forward as --analytics.

Offline: expects OHLC CSV under data/raw/{SYMBOL}_{timeframe}.csv (no Binance download).
Objective: maximize profit_factor among runs with n_trades >= --min-trades;
tie-break: expectancy, then final_equity, then mild preference for smaller |max_drawdown|.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
from itertools import product
from pathlib import Path
from typing import Any, Iterator

from tqdm import tqdm

from .backtest import run_enriched_gate_backtest
from .backtest_analytics import post_trade_analytics
from .data_loader import load_ohlcv_from_csv
from .features import SIMILARITY_FEATURE_COLS, add_basic_features
from .indicators import add_basic_indicators
from .main import AppConfig, load_config
from .paths import RAW_DATA_DIR
from .trade_gate import TradeGateConfig


def _default_csv_path(app: AppConfig) -> Path:
    safe = app.symbol.replace("/", "")
    return RAW_DATA_DIR / f"{safe}_{app.timeframe}.csv"


def iter_grid_configs(base: TradeGateConfig, *, coarse: bool) -> Iterator[TradeGateConfig]:
    if coarse:
        combs = [0.585, 0.592, 0.600]
        liqs = [0.085, 0.095, 0.11]
    else:
        combs = [0.58, 0.585, 0.592, 0.600]
        liqs = [0.080, 0.090, 0.100, 0.12]
    regimes = (frozenset(), frozenset({"trend"}))
    for c, liq, reg in product(combs, liqs, regimes):
        yield dataclasses.replace(
            base,
            comb_prob_min=float(c),
            liq_hard_min=float(liq),
            blocked_knn_regimes=reg,
            use_prob_edge_sizing=False,
            prob_edge_power=1.0,
        )
        for pw in (1.0, 2.0):
            yield dataclasses.replace(
                base,
                comb_prob_min=float(c),
                liq_hard_min=float(liq),
                blocked_knn_regimes=reg,
                use_prob_edge_sizing=True,
                prob_edge_power=float(pw),
                prob_edge_min_size=float(base.prob_edge_min_size),
            )


def _safe_pf(x: Any) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 1e12 if f > 0 else 0.0
    return f


def _rank_tuple(rep: dict[str, Any], min_trades: int) -> tuple[float, ...]:
    n = int(rep.get("n_trades", 0))
    if n < min_trades:
        return (-1e9,) * 4
    pf = _safe_pf(rep.get("profit_factor"))
    exp = float(rep.get("expectancy", 0.0))
    eq = float(rep.get("final_equity", 1.0))
    dd_bonus = abs(float(rep.get("max_drawdown", 0.0)))  # larger dd => worse tie-break
    return (pf, exp, eq, -dd_bonus)


def _tg_overrides_for_print(tg: TradeGateConfig) -> dict[str, Any]:
    return {
        "comb_prob_min": tg.comb_prob_min,
        "liq_hard_min": tg.liq_hard_min,
        "blocked_knn_regimes": sorted(tg.blocked_knn_regimes),
        "use_prob_edge_sizing": tg.use_prob_edge_sizing,
        "prob_edge_power": tg.prob_edge_power,
        "prob_edge_min_size": tg.prob_edge_min_size,
    }


def optimize_trade_gate(
    app: AppConfig,
    df_ready: Any,
    feature_cols: list[str],
    *,
    min_trades: int,
    coarse: bool,
    top: int,
) -> tuple[list[tuple[tuple[float, ...], dict[str, Any], TradeGateConfig]], int]:
    step = max(1, min(5, len(df_ready) // 800))
    results: list[tuple[tuple[float, ...], dict[str, Any], TradeGateConfig]] = []
    grid = list(iter_grid_configs(app.trade_gate, coarse=coarse))
    for tg in tqdm(grid, desc="Gate grid"):
        enr = run_enriched_gate_backtest(
            df_ready,
            feature_cols,
            app.similarity,
            app.backtest,
            tg,
            app.timeframe,
            step=step,
            quiet=True,
        )
        rep = post_trade_analytics(
            enr,
            min_trades_per_day_target=app.backtest.target_min_trades_per_day,
        )
        if "error" in rep:
            continue
        rk = _rank_tuple(rep, min_trades)
        results.append((rk, rep, tg))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[: max(top, 1)], len(grid)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grid-search trade_gate (offline CSV); maximize PF subject to min trades.",
    )
    p.add_argument("--config", type=str, default="configs/config.yaml", help="Base YAML.")
    p.add_argument(
        "--csv",
        type=str,
        default=None,
        help="Override CSV path (default: data/raw/SYMBOL_timeframe.csv).",
    )
    p.add_argument(
        "--min-trades",
        type=int,
        default=15,
        help="Drop runs with fewer trades from the ranking (noise control).",
    )
    p.add_argument("--top", type=int, default=12, help="How many best configs to print.")
    p.add_argument(
        "--coarse",
        action="store_true",
        help="Smaller grid (faster sanity check).",
    )
    return p


def run_from_cli(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    cfg_path = Path(args.config).resolve()
    app = load_config(cfg_path)

    csv_p = Path(args.csv).resolve() if args.csv else _default_csv_path(app)
    if not csv_p.is_file():
        print(json.dumps({"error": "csv_not_found", "path": str(csv_p)}, indent=2))
        return 1

    df = load_ohlcv_from_csv(str(csv_p))
    df = add_basic_indicators(df)
    df = add_basic_features(df)
    feature_cols = list(SIMILARITY_FEATURE_COLS)

    bests, n_grid = optimize_trade_gate(
        app,
        df,
        feature_cols,
        min_trades=int(args.min_trades),
        coarse=bool(args.coarse),
        top=int(args.top),
    )
    summary = [
        {
            "rank": i + 1,
            "rank_key": {"pf": tup[0], "expectancy": tup[1], "final_equity": tup[2], "dd_bonus": tup[3]},
            "trade_gate_delta": _tg_overrides_for_print(tg),
            "metrics": {
                "n_trades": rep["n_trades"],
                "profit_factor": rep["profit_factor"],
                "expectancy": rep["expectancy"],
                "max_drawdown": rep["max_drawdown"],
                "final_equity": rep["final_equity"],
                "win_rate": rep["win_rate"],
            },
            "eligible": rep["n_trades"] >= int(args.min_trades),
        }
        for i, (tup, rep, tg) in enumerate(bests)
    ]
    print(
        json.dumps(
            {"csv": str(csv_p), "n_bars": int(len(df)), "grid_combos": int(n_grid), "top": summary},
            indent=2,
            default=str,
        )
    )

    winner = bests[0] if bests else None
    if winner:
        tup, rep, tg = winner
        if rep["n_trades"] < int(args.min_trades):
            print(
                "# Note: best row still violates --min-trades; loosen min-trades "
                "or widen grid.",
                flush=True,
            )
        print(
            "\n# trade_gate YAML overrides (paste under trade_gate:)\n"
            + yaml_dumps_trade_gate_fragment(tg),
        )
    return 0


def yaml_dumps_trade_gate_fragment(tg: TradeGateConfig) -> str:
    lines = [
        f"  comb_prob_min: {tg.comb_prob_min}",
        f"  liq_hard_min: {tg.liq_hard_min}",
        f'  blocked_knn_regimes: {json.dumps(sorted(tg.blocked_knn_regimes))}',
        f"  use_prob_edge_sizing: {str(tg.use_prob_edge_sizing).lower()}",
        f"  prob_edge_power: {tg.prob_edge_power}",
        f"  prob_edge_min_size: {tg.prob_edge_min_size}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(run_from_cli())
