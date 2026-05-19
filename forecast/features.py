from __future__ import annotations

import numpy as np
import pandas as pd

from .signal_combiner import precompute_knn_regime_column

# Columns passed into similarity / kNN (scale-free where possible; no raw EMA/BB prices).
SIMILARITY_FEATURE_COLS: list[str] = [
    "ret_1",
    "ret_4",
    "ret_24",
    "ret_48",
    "range",
    "natr_14",
    "volatility_24",
    "volatility_48",
    "dist_ema20_pct",
    "dist_ema50_pct",
    "bb_dist_upper_pct",
    "bb_dist_lower_pct",
    "rsi_14",
    "ema20_slope",
    "ema50_slope",
    "trend_strength",
    "rel_volume",
    "volume_change",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "is_weekend",
]


def add_basic_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add simple derived features from price and indicators."""
    df = df.copy()

    df["ret_1"] = df["close"].pct_change()
    df["ret_4"] = df["close"].pct_change(4)
    df["ret_24"] = df["close"].pct_change(24)
    # 48 баров: на 1h ≈ 48h доходности, на 30m ≈ 24h (доп. контекст kNN в одних и тех же колонках).
    df["ret_48"] = df["close"].pct_change(48)

    df["range"] = (df["high"] - df["low"]) / df["close"]
    df["volatility_24"] = df["ret_1"].rolling(24).std()
    df["volatility_48"] = df["ret_1"].rolling(48).std()

    if "atr_14" in df.columns:
        df["natr_14"] = df["atr_14"] / df["close"].replace(0.0, np.nan)
    else:
        df["natr_14"] = df["range"]

    c = df["close"].replace(0.0, np.nan)
    df["dist_ema20_pct"] = (df["close"] - df["ema_20"]) / c
    df["dist_ema50_pct"] = (df["close"] - df["ema_50"]) / c
    df["bb_dist_upper_pct"] = (df["bb_high"] - df["close"]) / c
    df["bb_dist_lower_pct"] = (df["close"] - df["bb_low"]) / c

    df["ema20_slope"] = df["ema_20"].pct_change(4)
    df["ema50_slope"] = df["ema_50"].pct_change(4)
    df["trend_strength"] = (df["ema_20"] - df["ema_50"]) / c

    ve = df["volume_ema_20"].replace(0.0, np.nan)
    df["rel_volume"] = (df["volume"] / ve).clip(lower=0.0, upper=10.0)
    df["volume_change"] = df["volume"].pct_change()
    df["volume_change"] = df["volume_change"].replace([np.inf, -np.inf], np.nan)

    idx = df.index
    if isinstance(idx, pd.DatetimeIndex):
        hour = idx.hour.astype(float)
        if getattr(idx, "tz", None) is not None:
            hour = idx.tz_convert("UTC").hour.astype(float)
        dow = idx.dayofweek.astype(float)
        df["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
        df["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
        df["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
        df["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
        df["is_weekend"] = (dow >= 5.0).astype(float)
    else:
        df["hour_sin"] = 0.0
        df["hour_cos"] = 1.0
        df["dow_sin"] = 0.0
        df["dow_cos"] = 1.0
        df["is_weekend"] = 0.0

    df.dropna(inplace=True)
    if all(c in df.columns for c in ("ema_20", "ema_50", "volatility_24")):
        df["knn_regime"] = precompute_knn_regime_column(df)
    return df


def make_windowed_matrix(
    df: pd.DataFrame,
    feature_cols: list[str],
    window: int,
) -> tuple[np.ndarray, list[pd.Timestamp]]:
    """Transform time series features into rolling windows matrix."""
    values = df[feature_cols].values
    n = len(df)
    if n <= window:
        raise ValueError("Not enough data for given window size.")

    X = []
    idx = []
    for i in range(window, n):
        X.append(values[i - window : i, :].flatten())
        idx.append(df.index[i])
    return np.asarray(X), idx

