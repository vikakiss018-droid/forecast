"""A/B/C backtest: 15m trend vs soft level filter vs pullback-to-level."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .single_symbol_backtest import MultiSymbolBacktestConfig, run_multi_symbol_backtest
from .tf_backtest import DEFAULT_SYMBOLS
from .trend_rules import trend_params_for_timeframe
from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env


def _run_one(
    *,
    label: str,
    symbols: tuple[str, ...],
    tf: str,
    days: int,
    start_s: str,
    end_s: str,
    deposit: float,
    risk: float,
    stage1: float,
    trend_params,
    out_path: Path,
) -> dict:
    print(
        f"[trend_lvl] {label}: {len(symbols)} sym, {days}d {start_s}..{end_s} {tf}, "
        f"with_level={trend_params.require_with_trend_level} "
        f"zone={trend_params.with_trend_level_zone_frac} "
        f"pullback={trend_params.require_pullback} "
        f"stage1>={stage1} vol>={trend_params.min_rel_volume} → {out_path.name}",
        flush=True,
    )
    result = run_multi_symbol_backtest(
        cfg=MultiSymbolBacktestConfig(
            symbols=symbols,
            timeframe=tf,
            target_trades=0,
            stage1_min_score=stage1,
            trend_only=True,
            trend_params=trend_params,
            require_htf_align=trend_params.require_htf_align,
            htf_timeframe=trend_params.htf_timeframe,
            long_only=False,
            entry_window_start=start_s,
            entry_window_end=end_s,
        ),
        deposit_usdt=deposit,
        risk_pct=risk,
        result_path=out_path,
    )
    s = result.get("summary") or {}
    sides = result.get("summary_sides") or {}
    per_mo = round(float(s.get("trades", 0)) * 30.0 / max(days, 1), 1)
    print(
        f"[trend_lvl] {label}: trades={s.get('trades')} (~{per_mo}/mo) "
        f"win%={s.get('win_rate_pct')} total_R={s.get('total_r')} PF={s.get('profit_factor')} "
        f"(L={sides.get('long', 0)} S={sides.get('short', 0)}) "
        f"PnL ${s.get('estimated_profit_usdt')} ({s.get('estimated_return_pct')}%)",
        flush=True,
    )
    return result


def main() -> int:
    load_project_env(force=False)
    tf = os.environ.get("TREND_LVL_TF", "15m").strip() or "15m"
    days = int(os.environ.get("TREND_LVL_DAYS", "30"))
    deposit = float(os.environ.get("TREND_LVL_DEPOSIT", "1000"))
    risk = float(os.environ.get("TREND_LVL_RISK_PCT", "0.5"))
    soft_zone = float(os.environ.get("WITH_TREND_LEVEL_ZONE_FRAC", "0.35"))

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    use_filtered = os.environ.get("TREND_LVL_USE_FILTERED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    symbols_env = os.environ.get("TREND_LVL_SYMBOLS", "").strip()
    if symbols_env:
        symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip())
    elif use_filtered:
        symbols = load_filtered_symbols() or DEFAULT_SYMBOLS
    else:
        symbols = DEFAULT_SYMBOLS

    scan_cfg = trend_scan_config_from_env()
    base = trend_params_for_timeframe(tf, base=trend_params_from_yaml())
    out_dir = Path(__file__).resolve().parent / "data/processed"

    variants = [
        (
            "A_trend",
            replace(base, require_with_trend_level=False, block_opposite_level=False, require_pullback=False),
            out_dir / f"trend_only_{days}d_{tf}.json",
        ),
        (
            "B_soft_level",
            # Мягко: тренд + ближе к своему уровню (широкая зона), блок чужого
            replace(
                base,
                require_pullback=False,
                require_with_trend_level=True,
                with_trend_level_zone_frac=soft_zone,
                block_opposite_level=True,
                opposite_level_zone_frac=soft_zone,
            ),
            out_dir / f"trend_soft_level_{days}d_{tf}.json",
        ),
        (
            "C_pullback_level",
            # Откат к уровню в сторону тренда
            replace(
                base,
                require_pullback=True,
                require_with_trend_level=True,
                with_trend_level_zone_frac=0.30,
                block_opposite_level=True,
                opposite_level_zone_frac=0.30,
                long_pos_min=0.0,
                long_pos_max=0.35,
                short_pos_min=0.65,
                short_pos_max=1.0,
                min_pullback_from_swing_pct=0.001,
            ),
            out_dir / f"trend_pullback_level_{days}d_{tf}.json",
        ),
    ]

    results: dict[str, dict] = {}
    for label, params, path in variants:
        results[label] = _run_one(
            label=label,
            symbols=symbols,
            tf=tf,
            days=days,
            start_s=start_s,
            end_s=end_s,
            deposit=deposit,
            risk=risk,
            stage1=scan_cfg.stage1_min_score,
            trend_params=params,
            out_path=path,
        )

    parts = []
    for label, _p, _path in variants:
        s = (results[label].get("summary") or {})
        parts.append(f"{label}: R={s.get('total_r')} PF={s.get('profit_factor')} n={s.get('trades')}")
    print(f"[trend_lvl] COMPARE {days}d {tf}: " + " | ".join(parts), flush=True)
    ok = all(r.get("status") == "done" for r in results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
