from __future__ import annotations

from datetime import datetime
from typing import Literal

import pandas as pd

from .binance_client import BinanceConfig, fetch_ohlcv
from .paths import RAW_DATA_DIR, ensure_directories


COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def download_ohlcv_to_csv(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 1000,
    use_futures: bool = False,
    filename: str | None = None,
) -> str:
    """Download OHLCV from Binance via ccxt and save to data/raw/."""
    ensure_directories()
    cfg = BinanceConfig(symbol=symbol, timeframe=timeframe, limit=limit, use_futures=use_futures)
    raw_ohlcv = fetch_ohlcv(cfg)

    df = pd.DataFrame(raw_ohlcv, columns=COLUMNS)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("datetime", inplace=True)

    if filename is None:
        safe_symbol = symbol.replace("/", "")
        filename = f"{safe_symbol}_{timeframe}.csv"

    path = RAW_DATA_DIR / filename
    df.to_csv(path)
    return str(path)


def load_ohlcv_from_csv(path: str, tz: Literal["utc", "local"] = "utc") -> pd.DataFrame:
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    if tz == "local":
        df["datetime"] = df["datetime"].dt.tz_convert(None)
    df.set_index("datetime", inplace=True)
    return df

