"""CLI: 30-day range (flat) backtest on filtered 50 pairs, 1h — same gates as trend bot."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from pathlib import Path

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_range_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_rules import DEFAULT_TREND_PARAMS, TrendPullbackParams


def main() -> int:
    load_project_env(force=True)
    days = int(os.environ.get("RANGE_BT_DAYS", "30"))
    tf = os.environ.get("RANGE_BT_TIMEFRAME", "1h").strip() or "1h"
    deposit = float(os.environ.get("RANGE_BT_DEPOSIT", os.environ.get("MULTI_BT_DEPOSIT", "300")))
    risk = float(os.environ.get("RANGE_BT_RISK_PCT", os.environ.get("MULTI_BT_RISK_PCT", "0.5")))
    long_only = os.environ.get("RANGE_BT_LONG_ONLY", "0").strip().lower() in ("1", "true", "yes")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    use_filtered = os.environ.get("RANGE_BT_USE_FILTERED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    symbols_env = os.environ.get("RANGE_BT_SYMBOLS", "").strip()
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or DEFAULT_SYMBOLS
    else:
        symbols = DEFAULT_SYMBOLS

    min_vol = float(os.environ.get("TREND_MIN_REL_VOLUME", str(DEFAULT_TREND_PARAMS.min_rel_volume)))
    lookback = int(os.environ.get("TREND_LOOKBACK", str(DEFAULT_TREND_PARAMS.trend_lookback)))
    min_move = float(os.environ.get("TREND_MIN_MOVE_PCT", str(DEFAULT_TREND_PARAMS.min_trend_move_pct)))
    trend_params = TrendPullbackParams(
        require_pullback=False,
        require_htf_align=False,
        min_rel_volume=min_vol,
        trend_lookback=lookback,
        min_trend_move_pct=min_move,
    )

    out_name = os.environ.get(
        "RANGE_BT_OUT",
        f"range_backtest_filtered50_{days}d{'_long_only' if long_only else ''}.json",
    )
    out_path = Path(__file__).resolve().parent / "data/processed" / out_name

    print(
        f"[range_bt] {len(symbols)} symbols, {days}d {start_s}..{end_s}, {tf}, "
        f"deposit=${deposit} risk={risk}% "
        f"{'long-only ' if long_only else ''}→ {out_path.name}",
        flush=True,
    )

    result = run_range_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=0,
            trend_params=trend_params,
            long_only=long_only,
            entry_window_start=start_s,
            entry_window_end=end_s,
        ),
        deposit_usdt=deposit,
        risk_pct=risk,
        result_path=out_path,
    )
    if result.get("status") != "done":
        print(f"[range_bt] failed", flush=True)
        return 1

    s = result["summary"]
    sides = result.get("summary_sides") or {}
    print(
        f"[range_bt] trades={s['trades']} win%={s['win_rate_pct']} "
        f"total_R={s['total_r']} PF={s.get('profit_factor')} "
        f"(long={sides.get('long', 0)} short={sides.get('short', 0)})",
        flush=True,
    )
    print(
        f"[range_bt] PnL ${s['estimated_profit_usdt']} ({s['estimated_return_pct']}%) "
        f"on ${s['deposit_usdt']}",
        flush=True,
    )
    print(f"[range_bt] saved: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
