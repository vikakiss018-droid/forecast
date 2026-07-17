"""CLI: 10 alts, 1h (default), indicators only — historical OHLCV, no kNN."""

from __future__ import annotations

import os

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env


def main() -> int:
    # force=False: CLI/shell env перекрывает .env (удобно для абляций)
    load_project_env(force=False)
    tf = os.environ.get("MULTI_BT_TIMEFRAME", "1h").strip() or "1h"
    deposit = float(os.environ.get("MULTI_BT_DEPOSIT", "1000"))
    risk = float(os.environ.get("MULTI_BT_RISK_PCT", "0.5"))
    target = int(os.environ.get("MULTI_BT_TARGET_TRADES", "100"))
    target_label = "all signals" if target <= 0 else str(target)
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
    entry_start = os.environ.get("MULTI_BT_START", "").strip() or os.environ.get(
        "MULTI_BT_ENTRY_START", ""
    ).strip()
    entry_end = os.environ.get("MULTI_BT_END", "").strip() or os.environ.get(
        "MULTI_BT_ENTRY_END", ""
    ).strip()

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

    # Те же параметры, что у live-скана (config.yaml trend_scan + env-переопределения)
    scan_cfg = trend_scan_config_from_env()
    trend_params = trend_params_from_yaml()
    print(
        f"[multi_bt] {len(symbols)} symbols"
        + (" (R>0.5, win>50%)" if use_filtered and not symbols_env else "")
        + f", {tf} trend+range stage1>={scan_cfg.stage1_min_score} "
        f"lookback={trend_params.trend_lookback} "
        f"move>={trend_params.min_trend_move_pct} vol>={trend_params.min_rel_volume} "
        f"vol_range>={trend_params.min_rel_volume_range} atr>={trend_params.min_atr_pct} "
        f"pullback={trend_params.require_pullback} htf={trend_params.require_htf_align} "
        f"opp_level={trend_params.block_opposite_level}, "
        f"{'lev=' + str(leverage) + 'x ' if use_leverage_sizing else 'spot/1x '}"
        f"{'long-only ' if long_only else ''}"
        f"{'window ' + entry_start + '..' + entry_end + ' ' if entry_start and entry_end else ''}"
        f"target={target_label}...",
        flush=True,
    )
    result = run_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=target,
            stage1_min_score=scan_cfg.stage1_min_score,
            trend_only=False,
            trend_params=trend_params,
            require_htf_align=trend_params.require_htf_align,
            htf_timeframe=trend_params.htf_timeframe,
            leverage=leverage,
            max_notional_usdt=max_notional,
            use_leverage_sizing=use_leverage_sizing,
            long_only=long_only,
            entry_window_start=entry_start or None,
            entry_window_end=entry_end or None,
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
