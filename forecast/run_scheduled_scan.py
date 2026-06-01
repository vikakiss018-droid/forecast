"""
Основной цикл: тренд 1h по 50 парам (symbol_ranking_filtered_r05_win50.json), торговля на Binance spot.

Запуск (cron / systemd каждый час :03 UTC):
  python -m forecast.run_scheduled_scan

Переменные окружения:
  FORECAST_CONFIG              configs/config.yaml
  FORECAST_USE_FILTERED=1      50 пар из filtered JSON (по умолчанию)
  FORECAST_TIMEFRAME=1h
  FORECAST_BARS=1000
  FORECAST_TOP=20              сколько сетапов в отчёте
  FORECAST_STAGE1_MIN_SCORE=18
  AUTO_TRADE_MARKET=spot
  AUTO_TRADE_ENABLED / AUTO_TRADE_DRY_RUN
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from .auto_trader import load_auto_trade_config, manage_open_positions, maybe_run_auto_trade
from .paths import CONFIGS_DIR, load_project_env
from .run_symbol_ranking import load_filtered_symbols
from .scan_cache import save_scan_result
from .trend_scanner import scan_trend_filtered_setups, trend_scan_config_from_env


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

    symbols = scan_cfg.symbols or ()
    if not symbols and scan_cfg.use_filtered_symbols:
        symbols = load_filtered_symbols()

    if not symbols:
        print(
            "[trend] ERROR: нет символов. Запустите run_symbol_ranking или задайте FORECAST_SYMBOLS",
            flush=True,
        )
        return 1

    print(
        f"[trend] scan {len(symbols)} pairs ({'filtered R>0.5 win>50%' if scan_cfg.use_filtered_symbols else 'custom'}), "
        f"{scan_cfg.timeframe} stage1>={scan_cfg.stage1_min_score} "
        f"TP=4% rel_vol>={scan_cfg.trend_params.min_rel_volume if scan_cfg.trend_params else 1.2}; "
        f"trade market={auto_cfg.market_type} dry_run={auto_cfg.dry_run}",
        flush=True,
    )

    report = scan_trend_filtered_setups(scan_cfg, auto_cfg=auto_cfg)
    if report.get("status") == "error":
        print(f"[trend] scan failed: {report.get('error')}", flush=True)
        return 1

    path = save_scan_result(
        report,
        scan_config={
            "mode": "trend_momentum",
            "timeframe": scan_cfg.timeframe,
            "bars": scan_cfg.bars or None,
            "top_n": scan_cfg.top_n,
            "stage1_min_score": scan_cfg.stage1_min_score,
            "symbols_count": len(symbols),
            "use_filtered": scan_cfg.use_filtered_symbols,
            "scan_duration_sec": report.get("scan_duration_sec"),
            "candidates_found": report.get("candidates_found"),
        },
    )
    n_top = len(report.get("top_setups") or [])
    print(
        f"[trend] done candidates={report.get('candidates_found')} top={n_top} "
        f"duration_sec={report.get('scan_duration_sec')} saved={path}",
        flush=True,
    )
    for row in (report.get("top_setups") or [])[:5]:
        plan = row.get("setup") or {}
        print(
            f"  · {row.get('symbol')} {plan.get('direction')} score={row.get('score')} "
            f"RR={plan.get('risk_reward')}",
            flush=True,
        )

    pos_result = manage_open_positions(yaml_cfg=auto_yaml)
    print(
        f"[trend] positions open={pos_result.get('open_count')} "
        f"profit_closed={len(pos_result.get('profit_closed') or [])} "
        f"loss_closed={len(pos_result.get('loss_closed') or [])}",
        flush=True,
    )

    trade_result = maybe_run_auto_trade(report, yaml_cfg=auto_yaml)
    print(
        f"[trend] auto_trade: {trade_result.get('action')} opened={trade_result.get('opened_count', 0)} "
        f"{trade_result.get('reason', '')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
