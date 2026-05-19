from __future__ import annotations

import numpy as np
import pandas as pd


def detect_regime(df: pd.DataFrame) -> str:
    """Detect simple market regime: trend / range / high_vol."""
    trend = detect_trend_direction(df)
    vol_24 = float(df["volatility_24"].iloc[-1])  # std(ret_1) over 24 bars

    if vol_24 < 0.5 / 100:
        vol_regime = "low"
    elif vol_24 < 1.5 / 100:
        vol_regime = "normal"
    else:
        vol_regime = "high"

    if vol_regime == "high":
        return "high_vol"
    if trend in ("up", "down"):
        return "trend"
    return "range"


def detect_trend_direction(df: pd.DataFrame, drift: float | None = None) -> str:
    """Return directional trend label: up / down / range (EMA20 vs EMA50 with relative drift).

    Larger ``drift`` (e.g. 0.002) ⇒ меньше «трендовых» классификаций, жёстче фильтр.
    """
    ema_fast = float(df["ema_20"].iloc[-1])
    ema_slow = float(df["ema_50"].iloc[-1])
    d = float(0.001 if drift is None else drift)
    if ema_fast > ema_slow * (1 + d):
        return "up"
    if ema_fast < ema_slow * (1 - d):
        return "down"
    return "range"


def ui_volatility_regime(vol_24: float) -> str:
    """Labels used by trade gate (same thresholds as API snapshot)."""
    if not np.isfinite(vol_24):
        return "N/A"
    if vol_24 < 0.5 / 100:
        return "Low vol"
    if vol_24 < 1.5 / 100:
        return "Normal vol"
    return "High vol"


def ui_volatility_regime_last(df: pd.DataFrame) -> str:
    if df.empty or "volatility_24" not in df.columns:
        return "N/A"
    return ui_volatility_regime(float(df["volatility_24"].iloc[-1]))


def precompute_knn_regime_column(df: pd.DataFrame) -> pd.Series:
    """
    Per-bar kNN regime bucket matching detect_regime(df.iloc[:i+1]) (vectorized).
    """
    if df.empty or not all(c in df.columns for c in ("ema_20", "ema_50", "volatility_24")):
        return pd.Series(dtype=str)

    ema20 = df["ema_20"].to_numpy(dtype=float)
    ema50 = df["ema_50"].to_numpy(dtype=float)
    vol24 = df["volatility_24"].to_numpy(dtype=float)
    drift = 0.001
    trend_up = ema20 > ema50 * (1.0 + drift)
    trend_dn = ema20 < ema50 * (1.0 - drift)
    vol_high = vol24 >= 1.5 / 100.0
    tr_trend = trend_up | trend_dn
    out = np.where(vol_high, "high_vol", np.where(tr_trend, "trend", "range"))
    s = pd.Series(out, index=df.index, dtype=str)
    return s.where(np.isfinite(ema20) & np.isfinite(ema50) & np.isfinite(vol24), "range")


def compute_liquidity_distance_score(
    last_price: float,
    liq_low: float,
    liq_high: float,
    sigma: float = 0.003,
) -> tuple[float, float, float]:
    """
    Return (S_liq_up, S_liq_down, S_distance) in [0,1] using historical liquidity zone.
    """
    if not np.isfinite(liq_low) or not np.isfinite(liq_high) or liq_low >= liq_high:
        return 0.5, 0.5, 0.0

    center = 0.5 * (liq_low + liq_high)
    width = (liq_high - liq_low) / max(center, 1e-9)
    liq_weight = 1.0 / (1.0 + width * 50.0)

    d = abs(last_price - center) / max(last_price, 1e-9)
    S_distance = float(np.exp(-d * d / (2.0 * sigma * sigma)) * liq_weight)
    S_distance = float(np.clip(S_distance, 0.0, 1.0))

    if center > last_price:
        S_liq_up, S_liq_down = 1.0, 0.0
    elif center < last_price:
        S_liq_up, S_liq_down = 0.0, 1.0
    else:
        S_liq_up = S_liq_down = 0.5

    return S_liq_up, S_liq_down, S_distance


def compute_volume_scores(df: pd.DataFrame, window: int = 50) -> tuple[float, float]:
    """
    Compute S_vol_up / S_vol_down from volume and proxy delta (close within bar).
    Returns values in [0,1].
    """
    if len(df) < window + 1:
        return 0.5, 0.5

    sub = df.iloc[-window:]
    vol_now = float(sub["volume"].iloc[-1])
    vol_ma = float(sub["volume"].mean())
    volume_ratio = vol_now / max(vol_ma, 1e-9)
    volume_ratio = float(np.clip(volume_ratio, 0.0, 5.0))

    high = float(sub["high"].iloc[-1])
    low = float(sub["low"].iloc[-1])
    close = float(sub["close"].iloc[-1])
    if high > low:
        pos = (close - low) / (high - low)  # 0..1
    else:
        pos = 0.5

    delta_proxy = (pos - 0.5) * 2.0  # -1..1
    alpha = 3.0

    sig_up = 1.0 / (1.0 + np.exp(-alpha * delta_proxy))
    sig_down = 1.0 / (1.0 + np.exp(alpha * delta_proxy))

    S_vol_up = volume_ratio * sig_up
    S_vol_down = volume_ratio * sig_down

    # Нормируем только по верхней границе (без деления на 5),
    # чтобы сильные объёмные всплески сильнее влияли на сигнал.
    S_vol_up = float(np.clip(S_vol_up, 0.0, 1.0))
    S_vol_down = float(np.clip(S_vol_down, 0.0, 1.0))
    return S_vol_up, S_vol_down


def combine_probabilities(
    P_hist_up: float,
    P_hist_down: float,
    S_liq_up: float,
    S_liq_down: float,
    S_distance: float,
    S_vol_up: float,
    S_vol_down: float,
    regime: str,
) -> tuple[float, float]:
    """
    Multiplicative combined probability (no real order-flow yet).
    Returns (P_comb_up, P_comb_down).
    """
    eps = 1e-6
    P_hist_up = float(np.clip(P_hist_up, eps, 1.0 - eps))
    P_hist_down = float(np.clip(P_hist_down, eps, 1.0 - eps))
    S_liq_up = float(np.clip(S_liq_up, eps, 1.0))
    S_liq_down = float(np.clip(S_liq_down, eps, 1.0))
    S_distance = float(np.clip(S_distance, eps, 1.0))
    S_vol_up = float(np.clip(S_vol_up, eps, 1.0))
    S_vol_down = float(np.clip(S_vol_down, eps, 1.0))

    if regime == "trend":
        w1, w2, w3, w4 = 1.5, 1.0, 1.2, 2.0
    elif regime == "range":
        w1, w2, w3, w4 = 0.8, 2.0, 1.0, 1.2
    elif regime == "high_vol":
        w1, w2, w3, w4 = 1.0, 1.0, 2.0, 1.0
    else:
        w1 = w2 = w3 = w4 = 1.0

    S_up = (
        (S_liq_up ** w1)
        * (S_distance ** w2)
        * (S_vol_up ** w3)
        * (P_hist_up ** w4)
    )
    S_down = (
        (S_liq_down ** w1)
        * ((1.0 - S_distance) ** w2)
        * (S_vol_down ** w3)
        * (P_hist_down ** w4)
    )

    if not np.isfinite(S_up) or not np.isfinite(S_down) or (S_up + S_down) <= 0:
        return 0.5, 0.5

    P_comb_up = float(S_up / (S_up + S_down))
    P_comb_down = 1.0 - P_comb_up
    return P_comb_up, P_comb_down

