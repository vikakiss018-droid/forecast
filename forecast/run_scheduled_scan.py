"""
Scheduled market scan — run via systemd timer or cron every 15 minutes.

Environment variables (optional):
  FORECAST_CONFIG          path to config.yaml (default: configs/config.yaml)
  FORECAST_TOP             default 10
  FORECAST_BARS            default 320
  FORECAST_TIMEFRAME       default from config or 1h
  FORECAST_STAGE1_MIN_SCORE default 20
  FORECAST_MAX_SYMBOLS     default 100; set empty for full universe
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from .auto_trader import manage_open_positions, maybe_run_auto_trade
from .main import load_config
from .market_scanner import ScanConfig, scan_market_top_setups
from .paths import CONFIGS_DIR, load_project_env
from .scan_cache import save_scan_result


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_max_symbols() -> int | None:
    raw = os.environ.get("FORECAST_MAX_SYMBOLS", "100").strip()
    if raw.lower() in ("", "none", "all", "0"):
        return None
    return int(raw)


def main() -> int:
    load_project_env(force=True)
    config_path = os.environ.get("FORECAST_CONFIG", "configs/config.yaml")
    cfg = load_config(config_path)

    timeframe = os.environ.get("FORECAST_TIMEFRAME", "").strip() or cfg.timeframe
    top_n = _env_int("FORECAST_TOP", 10)
    bars = _env_int("FORECAST_BARS", 320)
    stage1 = float(os.environ.get("FORECAST_STAGE1_MIN_SCORE", "20"))
    max_sym = _env_max_symbols()

    scan_cfg = ScanConfig(
        timeframe=timeframe,
        bars=bars,
        top_n=top_n,
        stage1_min_score=stage1,
        max_symbols=max_sym,
    )

    print(
        f"[scan] starting timeframe={timeframe} bars={bars} top={top_n} "
        f"stage1>={stage1} max_symbols={max_sym or 'ALL'}",
        flush=True,
    )
    report = scan_market_top_setups(similarity_cfg=cfg.similarity, scan_cfg=scan_cfg)
    path = save_scan_result(
        report,
        scan_config={
            "timeframe": timeframe,
            "bars": bars,
            "top_n": top_n,
            "stage1_min_score": stage1,
            "max_symbols": max_sym,
        },
    )
    n = len(report.get("top_setups", []))
    print(
        f"[scan] done candidates={report.get('candidates_found')} "
        f"top={n} saved={path}",
        flush=True,
    )

    auto_yaml: dict = {}
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = CONFIGS_DIR.parent / config_path
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as f:
            auto_yaml = (yaml.safe_load(f) or {}).get("auto_trade") or {}
    pos_result = manage_open_positions(yaml_cfg=auto_yaml)
    print(
        f"[scan] positions open={pos_result.get('open_count')} "
        f"profit_closed={len(pos_result.get('profit_closed') or [])}",
        flush=True,
    )
    trade_result = maybe_run_auto_trade(report, yaml_cfg=auto_yaml)
    print(f"[scan] auto_trade: {trade_result.get('action')} {trade_result.get('reason', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
