from __future__ import annotations

import numpy as np
import pandas as pd


def timeframe_to_minutes(timeframe: str) -> int:
    tf = timeframe.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 60 * 24
    if tf.endswith("w"):
        return int(tf[:-1]) * 60 * 24 * 7
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def minutes_to_bars(minutes: int, timeframe: str) -> int:
    tf_min = timeframe_to_minutes(timeframe)
    bars = (minutes + tf_min - 1) // tf_min
    return max(int(bars), 1)


def hours_to_bars(hours: int, timeframe: str) -> int:
    return minutes_to_bars(hours * 60, timeframe)


def liquidity_zone_and_volume(df: pd.DataFrame, timeframe: str) -> tuple[float, float, float]:
    """Approximate liquidity zone and 24h volume from recent bars."""
    if df.empty:
        return float("nan"), float("nan"), 0.0

    bars_24h = hours_to_bars(24, timeframe)
    bars_24h = min(bars_24h, len(df))
    sub = df.iloc[-bars_24h:]

    mid_prices = ((sub["high"] + sub["low"]) / 2.0).to_numpy(dtype=float)
    volumes = sub["volume"].to_numpy(dtype=float)
    total_vol = float(volumes.sum())

    if total_vol <= 0:
        return float(mid_prices.min()), float(mid_prices.max()), total_vol

    order = np.argsort(mid_prices)
    p_sorted = mid_prices[order]
    v_sorted = volumes[order]
    cum = np.cumsum(v_sorted) / total_vol

    low_idx = np.searchsorted(cum, 0.2)
    high_idx = np.searchsorted(cum, 0.8)
    low_price = float(p_sorted[max(min(low_idx, len(p_sorted) - 1), 0)])
    high_price = float(p_sorted[max(min(high_idx, len(p_sorted) - 1), 0)])

    return low_price, high_price, total_vol
