"""Эталонные параметры стратегии: config.yaml + переопределение из .env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIGS_DIR


def _config_path() -> Path:
    raw = os.environ.get("FORECAST_CONFIG", "configs/config.yaml").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = CONFIGS_DIR.parent / raw
    return p


@lru_cache(maxsize=1)
def load_strategy_yaml() -> dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def yaml_section(name: str) -> dict[str, Any]:
    block = load_strategy_yaml().get(name) or {}
    return block if isinstance(block, dict) else {}


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def env_float(name: str, default: float, *, positive: bool = False) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    val = float(raw)
    if positive and val <= 0:
        return default
    return val


def env_int(name: str, default: int, *, positive: bool = False) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    val = int(raw)
    if positive and val <= 0:
        return default
    return val


def env_str(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw if raw else default
