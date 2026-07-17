"""
Основной цикл: тренд + флет на 1h по 50 парам (symbol_ranking_filtered_r05_win50.json).

Запуск (cron / systemd каждый час :03 UTC):
  python -m forecast.run_scheduled_scan

Параметры: configs/config.yaml → trend_scan (+ фильтры из auto_trade для validate_setup)
Переменные: FORECAST_USE_FILTERED, FORECAST_ALLOW_TREND, FORECAST_ALLOW_RANGE,
  FORECAST_LONG_ONLY, TREND_MIN_REL_VOLUME, AUTO_TRADE_MIN_* …
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from .auto_trader import load_auto_trade_config
from .paths import CONFIGS_DIR, load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .scan_cache import save_scan_result
from .trend_scanner import (
    SCAN_MODE,
    scan_combined_filtered_setups,
    trend_scan_config_from_env,
)


def _load_auto_trade_yaml(config_path: str) -> dict:
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = CONFIGS_DIR.parent / config_path
    if not cfg_path.is_file():
        return {}
    with cfg_path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("auto_trade") or {}


def main() -> int:
    load_project_env(force=True)
    config_path = os.environ.get("FORECAST_CONFIG", "configs/config.yaml")
    scan_cfg = trend_scan_config_from_env()
    auto_yaml = _load_auto_trade_yaml(config_path)
    auto_cfg = load_auto_trade_config(auto_yaml)
    params = scan_cfg.trend_params

    symbols = scan_cfg.symbols or ()
    if not symbols and scan_cfg.use_filtered_symbols:
        symbols = load_filtered_symbols()

    if not symbols:
        print(
            "[combined] ERROR: нет символов. Запустите run_symbol_ranking или задайте FORECAST_SYMBOLS",
            flush=True,
        )
        return 1

    regimes = []
    if scan_cfg.allow_trend:
        regimes.append("trend")
    if scan_cfg.allow_range:
        regimes.append("range")
    if not regimes:
        print("[combined] ERROR: отключены и trend, и range (FORECAST_ALLOW_TREND/RANGE)", flush=True)
        return 1

    min_vol = params.min_rel_volume if params else 1.2
    print(
        f"[combined] scan {len(symbols)} pairs "
        f"({'filtered R>0.5 win>50%' if scan_cfg.use_filtered_symbols else 'custom'}), "
        f"{scan_cfg.timeframe} regimes={'+'.join(regimes)} "
        f"stage1>={scan_cfg.stage1_min_score} rel_vol>={min_vol} "
        f"RR>={auto_cfg.min_risk_reward} "
        f"{'long-only ' if scan_cfg.long_only else ''}"
        f"(scanner only, no auto-trade)",
        flush=True,
    )

    report = scan_combined_filtered_setups(scan_cfg, auto_cfg=auto_cfg)
    if report.get("status") == "error":
        print(f"[combined] scan failed: {report.get('error')}", flush=True)
        return 1

    by_regime = report.get("candidates_by_regime") or {}
    path = save_scan_result(
        report,
        scan_config={
            "mode": SCAN_MODE,
            "timeframe": scan_cfg.timeframe,
            "bars": scan_cfg.bars or None,
            "top_n": scan_cfg.top_n,
            "stage1_min_score": scan_cfg.stage1_min_score,
            "symbols_count": len(symbols),
            "use_filtered": scan_cfg.use_filtered_symbols,
            "allow_trend": scan_cfg.allow_trend,
            "allow_range": scan_cfg.allow_range,
            "long_only": scan_cfg.long_only,
            "scan_duration_sec": report.get("scan_duration_sec"),
            "candidates_found": report.get("candidates_found"),
            "candidates_by_regime": by_regime,
        },
    )
    n_top = len(report.get("top_setups") or [])
    print(
        f"[combined] done candidates={report.get('candidates_found')} "
        f"(trend={by_regime.get('trend', 0)} range={by_regime.get('range', 0)}) "
        f"top={n_top} duration_sec={report.get('scan_duration_sec')} saved={path}",
        flush=True,
    )
    for row in (report.get("top_setups") or [])[:8]:
        plan = row.get("setup") or {}
        print(
            f"  · {row.get('symbol')} [{row.get('regime', '?')}] "
            f"{plan.get('direction')} score={row.get('score')} RR={plan.get('risk_reward')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
