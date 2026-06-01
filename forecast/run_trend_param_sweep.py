"""CLI: grid search trend/pullback thresholds, then apply best and save."""

from __future__ import annotations

import json

from .paths import load_project_env
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_multi_symbol_backtest
from .trend_param_sweep import run_trend_param_sweep
from .trend_rules import TrendPullbackParams


def main() -> int:
    load_project_env(force=True)
    result = run_trend_param_sweep()
    if result.get("status") != "done":
        print(f"[sweep] failed: {result.get('error')}", flush=True)
        return 1

    best = result.get("best") or {}
    params_d = best.get("params") or {}
    summary = best.get("summary") or {}
    print("\n[sweep] TOP-1 params:", flush=True)
    for k, v in params_d.items():
        print(f"  {k}: {v}", flush=True)
    print(
        f"[sweep] trades={summary.get('trades')} win%={summary.get('win_rate_pct')} "
        f"total_R={summary.get('total_r')} PF={summary.get('profit_factor')} score={best.get('rank_score')}",
        flush=True,
    )

    print("\n[sweep] TOP-5:", flush=True)
    for i, row in enumerate((result.get("top10") or [])[:5]):
        p = row["params"]
        s = row["summary"]
        print(
            f"  #{i + 1} score={row['rank_score']:.2f} R={s.get('total_r')} "
            f"trades={s.get('trades')} pullback={p.get('require_pullback')} "
            f"long={p.get('long_pos_min')}-{p.get('long_pos_max')} "
            f"short={p.get('short_pos_min')}-{p.get('short_pos_max')} "
            f"swing={p.get('min_pullback_from_swing_pct')} lb={p.get('pullback_lookback')}",
            flush=True,
        )

    best_by_r = result.get("best_by_total_r") or best
    print("\n[sweep] best by total_R:", flush=True)
    br = best_by_r.get("summary") or {}
    bp = best_by_r.get("params") or {}
    print(
        f"  R={br.get('total_r')} trades={br.get('trades')} pullback={bp.get('require_pullback')} "
        f"long={bp.get('long_pos_min')}-{bp.get('long_pos_max')} short={bp.get('short_pos_min')}-{bp.get('short_pos_max')}",
        flush=True,
    )

    best_params = TrendPullbackParams(**{k: v for k, v in (best_by_r.get("params") or params_d).items()})
    print("\n[sweep] re-running multi backtest with best-by-R params...", flush=True)
    bt = run_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(trend_params=best_params),
    )
    s = bt.get("summary") or {}
    print(
        f"[sweep] final trades={s.get('trades')} win%={s.get('win_rate_pct')} "
        f"total_R={s.get('total_r')} PnL=${s.get('estimated_profit_usdt')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
