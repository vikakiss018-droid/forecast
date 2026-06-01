"""CLI: compare timeframes on 10 volatile alts (walk-forward, ~100 trades per TF)."""

from __future__ import annotations

import os
import sys

from .paths import load_project_env
from .tf_backtest import run_timeframe_study


def main() -> int:
    load_project_env(force=True)
    config_path = os.environ.get("FORECAST_CONFIG", "configs/config.yaml")
    print("[tf_backtest] starting (may take 15–40 min on VPS)...", flush=True)
    result = run_timeframe_study(config_path=config_path)
    print(f"[tf_backtest] done status={result.get('status')} best={result.get('best_timeframe')}", flush=True)
    for row in result.get("ranking") or []:
        print(
            f"  {row.get('timeframe')}: trades={row.get('trades')} "
            f"win%={row.get('win_rate_pct')} total_R={row.get('total_r')}",
            flush=True,
        )
    return 0 if result.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
