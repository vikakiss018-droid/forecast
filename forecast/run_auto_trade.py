"""Run auto-trader from latest scanner cache (manual or cron)."""

from __future__ import annotations

import yaml

from .auto_trader import load_auto_trade_config, run_from_cache
from .paths import CONFIGS_DIR


def main() -> int:
    cfg_path = CONFIGS_DIR / "config.yaml"
    yaml_cfg: dict = {}
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as f:
            yaml_cfg = (yaml.safe_load(f) or {}).get("auto_trade") or {}
    at = load_auto_trade_config(yaml_cfg)
    print(
        f"[auto_trade] enabled={at.enabled} dry_run={at.dry_run} "
        f"min_score={at.min_score} max_notional={at.max_notional_usdt} lev={at.leverage}x",
        flush=True,
    )
    run_from_cache(yaml_cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
