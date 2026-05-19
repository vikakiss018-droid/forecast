from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .paths import PROCESSED_DATA_DIR, ensure_directories


DEFAULT_CALIBRATION_FILENAME = "ev_calibration.json"


def default_calibration_path() -> Path:
    return PROCESSED_DATA_DIR / DEFAULT_CALIBRATION_FILENAME


def load_ev_calibration(path: str | Path | None = None) -> dict[str, float]:
    """Load empirical EV curve: ev_bucket -> mean realized net_ret (fraction)."""
    p = Path(path) if path else default_calibration_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw: Any = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def fit_ev_calibration_from_enriched(enriched: pd.DataFrame) -> dict[str, float]:
    """
    Mean net PnL by ev_bucket on traded rows (empirical calibration target).
    Buckets with no trades are omitted.
    """
    if enriched.empty or "ev_bucket" not in enriched.columns:
        return {}
    sub = enriched[enriched.get("position", 0) != 0]
    if sub.empty or "net_ret" not in sub.columns:
        return {}
    g = sub.groupby("ev_bucket", observed=True)["net_ret"].mean()
    return {str(k): float(v) for k, v in g.items() if np.isfinite(v)}


def save_ev_calibration(curve: dict[str, float], path: str | Path | None = None) -> Path:
    ensure_directories()
    p = Path(path) if path else default_calibration_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(curve, f, indent=2, sort_keys=True)
    return p
