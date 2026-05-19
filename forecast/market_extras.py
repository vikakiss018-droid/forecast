from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd
import requests

from .paths import LIQUIDATIONS_DIR, LIQUIDITY_DIR, ensure_directories


def _download_json_to_df(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return pd.json_normalize(data)


def download_liquidations(url: str, filename: str = "liquidations.json") -> str:
    """Generic downloader for liquidations data from a custom API."""
    ensure_directories()
    df = _download_json_to_df(url)
    path = LIQUIDATIONS_DIR / filename
    df.to_csv(path, index=False)
    return str(path)


def download_liquidity_map(url: str, filename: str = "liquidity_map.json") -> str:
    """Generic downloader for liquidity heatmap / orderbook snapshots."""
    ensure_directories()
    df = _download_json_to_df(url)
    path = LIQUIDITY_DIR / filename
    df.to_csv(path, index=False)
    return str(path)


def load_extras(
    kind: Literal["liquidations", "liquidity"],
    filename: str,
) -> pd.DataFrame:
    if kind == "liquidations":
        path = LIQUIDATIONS_DIR / filename
    else:
        path = LIQUIDITY_DIR / filename
    return pd.read_csv(path)

