from __future__ import annotations

import pandas as pd
import ta


def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add a basic set of technical indicators using ta."""
    df = df.copy()

    # Trend indicators (scanner / legacy paths)
    df["ema_20"] = ta.trend.EMAIndicator(close=df["close"], window=20).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(close=df["close"], window=50).ema_indicator()
    df["ema_200"] = ta.trend.EMAIndicator(close=df["close"], window=200).ema_indicator()

    # Volatility
    df["bb_high"] = ta.volatility.BollingerBands(close=df["close"], window=20).bollinger_hband()
    df["bb_low"] = ta.volatility.BollingerBands(close=df["close"], window=20).bollinger_lband()

    # Momentum
    rsi_ind = ta.momentum.RSIIndicator(close=df["close"], window=14)
    df["rsi_14"] = rsi_ind.rsi()

    # Volume
    df["volume_ema_20"] = ta.trend.EMAIndicator(close=df["volume"], window=20).ema_indicator()

    atr = ta.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=14
    )
    df["atr_14"] = atr.average_true_range()

    df.dropna(inplace=True)
    return df

