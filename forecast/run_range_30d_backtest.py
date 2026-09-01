"""CLI: historical range-bounce backtest — entries only from heavily retested S/R levels."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_range_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env


def _resolve_days(tf: str) -> int:
    """Максимально практичное окно: 1h ≈ 3 года, 15m ≈ 1 год (лимит объёма запросов)."""
    raw = os.environ.get("RANGE_BT_DAYS", "max").strip().lower()
    if raw in ("", "max", "maximum", "full"):
        return 1095 if tf in ("1h", "4h", "1d") else 365
    return max(int(raw), 1)


def main() -> int:
    load_project_env(force=False)
    tf = os.environ.get("RANGE_BT_TIMEFRAME", os.environ.get("FORECAST_TIMEFRAME", "1h")).strip() or "1h"
    days = _resolve_days(tf)
    deposit = float(os.environ.get("RANGE_BT_DEPOSIT", os.environ.get("MULTI_BT_DEPOSIT", "1000")))
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

    scan_cfg = trend_scan_config_from_env()
    base = trend_params_from_yaml()
    # Входы только от уровней с наибольшим числом повторных касаний
    min_touches = int(os.environ.get("MIN_LEVEL_TOUCHES", "4"))
    require_max = os.environ.get("REQUIRE_MAX_TOUCHES_SIDE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    trend_params = replace(
        base,
        min_level_touches=min_touches,
        require_max_touches_side=require_max,
    )

    out_name = os.environ.get(
        "RANGE_BT_OUT",
        f"range_max_touches_{days}d_{tf.replace('/', '-')}{'_long_only' if long_only else ''}.json",
    )
    out_path = Path(__file__).resolve().parent / "data/processed" / out_name

    print(
        f"[range_bt] LEVEL entries, max-repetition only "
        f"(touches>={min_touches}, max_side={require_max}), "
        f"{len(symbols)} symbols, {days}d {start_s}..{end_s}, {tf}, "
        f"stage1>={scan_cfg.stage1_min_score} vol_range>={trend_params.min_rel_volume_range} "
        f"deposit=${deposit} risk={risk}% "
        f"{'long-only ' if long_only else ''}→ {out_path.name}",
        flush=True,
    )

    result = run_range_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=0,
            stage1_min_score=scan_cfg.stage1_min_score,
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
        print("[range_bt] failed", flush=True)
        return 1

    s = result["summary"]
    sides = result.get("summary_sides") or {}
    trades = result.get("trades") or []
    touch_meta = result.get("touch_stats") or {}
    touch_vals = [int(t.get("entry_level_touches") or 0) for t in trades if t.get("entry_level_touches") is not None]
    touch_note = ""
    if touch_meta:
        touch_note = (
            f" touches[med={touch_meta.get('median')} "
            f"avg={touch_meta.get('avg')} max={touch_meta.get('max')}]"
        )
    elif touch_vals:
        touch_note = f" touches[med={sorted(touch_vals)[len(touch_vals)//2]} max={max(touch_vals)}]"
    per_mo = round(float(s["trades"]) * 30.0 / max(days, 1), 1)
    print(
        f"[range_bt] trades={s['trades']} (~{per_mo}/mo) win%={s['win_rate_pct']} "
        f"total_R={s['total_r']} PF={s.get('profit_factor')} "
        f"(long={sides.get('long', 0)} short={sides.get('short', 0)}){touch_note}",
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
