"""CLI: 30-day combined trend + range backtest on filtered 50 pairs, 1h."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_combined_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env


def main() -> int:
    # force=False: CLI/shell env перекрывает .env (удобно для абляций)
    load_project_env(force=False)
    days = int(os.environ.get("COMBINED_BT_DAYS", "30"))
    tf = os.environ.get("COMBINED_BT_TIMEFRAME", "1h").strip() or "1h"
    deposit = float(
        os.environ.get("COMBINED_BT_DEPOSIT", os.environ.get("MULTI_BT_DEPOSIT", "300"))
    )
    risk = float(
        os.environ.get("COMBINED_BT_RISK_PCT", os.environ.get("MULTI_BT_RISK_PCT", "0.5"))
    )
    long_only = os.environ.get("COMBINED_BT_LONG_ONLY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    use_filtered = os.environ.get("COMBINED_BT_USE_FILTERED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    symbols_env = os.environ.get("COMBINED_BT_SYMBOLS", "").strip()
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or DEFAULT_SYMBOLS
    else:
        symbols = DEFAULT_SYMBOLS

    scan_cfg = trend_scan_config_from_env()
    trend_params = trend_params_from_yaml()

    out_name = os.environ.get(
        "COMBINED_BT_OUT",
        f"combined_backtest_filtered50_{days}d{'_long_only' if long_only else ''}.json",
    )
    out_path = Path(__file__).resolve().parent / "data/processed" / out_name

    print(
        f"[combined_bt] {len(symbols)} symbols, {days}d {start_s}..{end_s}, {tf}, "
        f"trend+range stage1>={scan_cfg.stage1_min_score} "
        f"vol>={trend_params.min_rel_volume}/{trend_params.min_rel_volume_range} "
        f"atr>={trend_params.min_atr_pct} "
        f"deposit=${deposit} risk={risk}% "
        f"{'long-only ' if long_only else ''}→ {out_path.name}",
        flush=True,
    )

    # Прокидываем allow_trend/allow_range через monkeypatch на combined runner —
    # проще: trend_only=True если range выключен.
    use_trend_only = bool(scan_cfg.allow_trend) and not bool(scan_cfg.allow_range)
    result = run_combined_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=0,
            stage1_min_score=scan_cfg.stage1_min_score,
            trend_only=use_trend_only,
            trend_params=trend_params,
            require_htf_align=trend_params.require_htf_align,
            htf_timeframe=trend_params.htf_timeframe,
            long_only=long_only,
            entry_window_start=start_s,
            entry_window_end=end_s,
        ),
        deposit_usdt=deposit,
        risk_pct=risk,
        result_path=out_path,
    )
    if result.get("status") != "done":
        print("[combined_bt] failed", flush=True)
        return 1

    s = result["summary"]
    st = result.get("summary_by_regime", {}).get("trend", {})
    sr = result.get("summary_by_regime", {}).get("range", {})
    trades_per_month = round(float(s["trades"]) * 30.0 / max(days, 1), 1)
    print(
        f"[combined_bt] ALL trades={s['trades']} (~{trades_per_month}/mo) "
        f"win%={s['win_rate_pct']} "
        f"total_R={s['total_r']} PF={s.get('profit_factor')} "
        f"PnL ${s['estimated_profit_usdt']} ({s['estimated_return_pct']}%)",
        flush=True,
    )
    print(
        f"[combined_bt] trend: n={st.get('trades')} win%={st.get('win_rate_pct')} R={st.get('total_r')} | "
        f"range: n={sr.get('trades')} win%={sr.get('win_rate_pct')} R={sr.get('total_r')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
