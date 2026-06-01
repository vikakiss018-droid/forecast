"""CLI: 10 alts, 1h (default), indicators only — historical OHLCV, no kNN."""

from __future__ import annotations

import os

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_rules import DEFAULT_TREND_PARAMS, TrendPullbackParams


def main() -> int:
    load_project_env(force=True)
    tf = os.environ.get("MULTI_BT_TIMEFRAME", "1h").strip() or "1h"
    deposit = float(os.environ.get("MULTI_BT_DEPOSIT", "1000"))
    risk = float(os.environ.get("MULTI_BT_RISK_PCT", "0.5"))
    target = int(os.environ.get("MULTI_BT_TARGET_TRADES", "100"))
    leverage = int(os.environ.get("MULTI_BT_LEVERAGE", "1"))
    max_notional = float(os.environ.get("MULTI_BT_MAX_NOTIONAL", "50"))
    use_leverage_sizing = os.environ.get("MULTI_BT_USE_LEVERAGE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    long_only = os.environ.get("MULTI_BT_LONG_ONLY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    symbols_env = os.environ.get("MULTI_BT_SYMBOLS", "").strip()
    use_filtered = os.environ.get("MULTI_BT_USE_FILTERED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    elif use_filtered:
        filtered = load_filtered_symbols()
        symbols = filtered if filtered else DEFAULT_SYMBOLS
    else:
        symbols = DEFAULT_SYMBOLS

    print(
        f"[multi_bt] {len(symbols)} symbols"
        + (" (R>0.5, win>50%)" if use_filtered and not symbols_env else "")
        + f", {tf} lookback={os.environ.get('TREND_LOOKBACK', '60')} "
        f"move>={os.environ.get('TREND_MIN_MOVE_PCT', '0.008')} vol>={os.environ.get('TREND_MIN_REL_VOLUME', '1.2')}, "
        f"{'lev=' + str(leverage) + 'x ' if use_leverage_sizing else 'spot/1x '}"
        f"{'long-only ' if long_only else ''}"
        f"target={target}...",
        flush=True,
    )
    min_vol = float(os.environ.get("TREND_MIN_REL_VOLUME", str(DEFAULT_TREND_PARAMS.min_rel_volume)))
    min_atr = float(os.environ.get("TREND_MIN_ATR_PCT", "0"))
    lookback = int(os.environ.get("TREND_LOOKBACK", str(DEFAULT_TREND_PARAMS.trend_lookback)))
    min_move = float(os.environ.get("TREND_MIN_MOVE_PCT", str(DEFAULT_TREND_PARAMS.min_trend_move_pct)))
    trend_params = TrendPullbackParams(
        require_pullback=False,
        require_htf_align=False,
        min_rel_volume=min_vol,
        min_atr_pct=min_atr,
        trend_lookback=lookback,
        min_trend_move_pct=min_move,
    )
    result = run_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=target,
            trend_params=trend_params,
            leverage=leverage,
            max_notional_usdt=max_notional,
            use_leverage_sizing=use_leverage_sizing,
            long_only=long_only,
        ),
        deposit_usdt=deposit,
        risk_pct=risk,
    )
    if result.get("status") != "done":
        print(f"[multi_bt] failed: {result.get('error')}", flush=True)
        return 1

    s = result["summary"]
    print(
        f"[multi_bt] trades={s['trades']} win%={s['win_rate_pct']} "
        f"total_R={s['total_r']} PF={s.get('profit_factor')}",
        flush=True,
    )
    print(
        f"[multi_bt] deposit=${s['deposit_usdt']} risk={s['risk_pct']}% "
        f"→ PnL ${s['estimated_profit_usdt']} ({s['estimated_return_pct']}%)",
        flush=True,
    )
    for sym, row in sorted((result.get("by_symbol") or {}).items(), key=lambda x: -x[1].get("total_r", 0)):
        print(f"  {sym}: {row.get('trades')} trades, R={row.get('total_r')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
