"""
Торговля по тренду на исторических OHLCV: структура HH/HL или LH/LL, без kNN.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
import pandas as pd

from .market_scanner import LEVEL_PROXIMITY_FRAC, _level_proximity

TREND_LOOKBACK = 60
MIN_TREND_MOVE_PCT = 0.008
RR_TARGET = 2.8
TP_TARGET_PCT = 0.04  # 4% от цены входа (target_2); 0 = использовать rr_target
# Оптимум по grid search (1297 комбинаций, 10 монет, 1h)
PULLBACK_LOOKBACK = 12
PULLBACK_LONG_POS_MIN = 0.10
PULLBACK_LONG_POS_MAX = 0.68
PULLBACK_SHORT_POS_MIN = 0.52
PULLBACK_SHORT_POS_MAX = 0.72
MIN_PULLBACK_FROM_SWING_PCT = 0.001
MIN_REL_VOLUME = 1.2
MIN_ATR_PCT = 0.0  # 0 = фильтр ATR выключен
MIN_SWING_COUNT = 2  # последние 2 локальных HH/HL (или LH/LL)
SWING_WING = 2
# Азия: низкая ликвидность на альтах, чаще ложные пробои (UTC, включительно)
ASIAN_SESSION_HOURS_UTC = (0, 8)


def bar_hour_utc(ts: pd.Timestamp) -> int:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    else:
        t = t.tz_convert("UTC")
    return int(t.hour)


def in_utc_hour_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


@dataclass(frozen=True)
class TrendPullbackParams:
    trend_lookback: int = TREND_LOOKBACK
    min_trend_move_pct: float = MIN_TREND_MOVE_PCT
    rr_target: float = RR_TARGET
    tp_target_pct: float = TP_TARGET_PCT
    require_pullback: bool = False
    pullback_lookback: int = PULLBACK_LOOKBACK
    long_pos_min: float = PULLBACK_LONG_POS_MIN
    long_pos_max: float = PULLBACK_LONG_POS_MAX
    short_pos_min: float = PULLBACK_SHORT_POS_MIN
    short_pos_max: float = PULLBACK_SHORT_POS_MAX
    min_pullback_from_swing_pct: float = MIN_PULLBACK_FROM_SWING_PCT
    require_htf_align: bool = False
    htf_timeframe: str = "4h"
    min_rel_volume: float = MIN_REL_VOLUME
    min_atr_pct: float = MIN_ATR_PCT
    require_swing_structure: bool = False
    min_swing_count: int = MIN_SWING_COUNT
    block_opposite_level: bool = False
    opposite_level_zone_frac: float = LEVEL_PROXIMITY_FRAC
    block_asian_session: bool = False
    asian_session_hours_utc: tuple[int, int] = ASIAN_SESSION_HOURS_UTC

    def valid(self) -> bool:
        return (
            self.long_pos_min < self.long_pos_max
            and self.short_pos_min < self.short_pos_max
            and self.trend_lookback >= 20
            and self.pullback_lookback >= 4
        )


# Дефолт: lookback 60, move 0.8%, импульс, rel_volume>=1.2, без 4h/ATR
DEFAULT_TREND_PARAMS = TrendPullbackParams(
    require_pullback=False,
    require_htf_align=False,
    min_rel_volume=MIN_REL_VOLUME,
    min_atr_pct=MIN_ATR_PCT,
)
DEFAULT_PULLBACK_PARAMS = DEFAULT_TREND_PARAMS


def _local_swing_indices(series: np.ndarray, *, kind: Literal["high", "low"], wing: int = SWING_WING) -> list[int]:
    n = len(series)
    if n < wing * 2 + 1:
        return []
    out: list[int] = []
    for i in range(wing, n - wing):
        v = float(series[i])
        left = series[i - wing : i]
        right = series[i + 1 : i + wing + 1]
        if kind == "high":
            if v >= float(np.max(left)) and v > float(np.max(right)):
                out.append(i)
        elif v <= float(np.min(left)) and v < float(np.min(right)):
            out.append(i)
    return out


def _last_swings_monotonic(values: list[float], count: int, direction: Literal["up", "down"]) -> bool:
    """Последние count локальных экстремумов строго растут (up) или падают (down)."""
    if len(values) < count or count < 2:
        return False
    tail = values[-count:]
    for i in range(1, len(tail)):
        if direction == "up" and tail[i] <= tail[i - 1]:
            return False
        if direction == "down" and tail[i] >= tail[i - 1]:
            return False
    return True


def _trend_from_halves(tail: pd.DataFrame, min_move: float) -> str:
    mid = len(tail) // 2
    h1 = float(tail["high"].iloc[:mid].max())
    h2 = float(tail["high"].iloc[mid:].max())
    l1 = float(tail["low"].iloc[:mid].min())
    l2 = float(tail["low"].iloc[mid:].min())
    close = float(tail["close"].iloc[-1])
    close_old = float(tail["close"].iloc[0])
    ref = max(abs(close_old), 1e-9)
    move = (close - close_old) / ref
    if h2 > h1 and l2 > l1 and move >= min_move:
        return "up"
    if h2 < h1 and l2 < l1 and move <= -min_move:
        return "down"
    return "range"


def _trend_from_swing_structure(tail: pd.DataFrame, *, min_move: float, min_swings: int) -> str:
    highs = tail["high"].to_numpy(dtype=float)
    lows = tail["low"].to_numpy(dtype=float)
    hi_vals = [float(highs[i]) for i in _local_swing_indices(highs, kind="high")]
    lo_vals = [float(lows[i]) for i in _local_swing_indices(lows, kind="low")]
    close = float(tail["close"].iloc[-1])
    close_old = float(tail["close"].iloc[0])
    ref = max(abs(close_old), 1e-9)
    move = (close - close_old) / ref

    up_struct = _last_swings_monotonic(hi_vals, min_swings, "up") and _last_swings_monotonic(
        lo_vals, min_swings, "up"
    )
    down_struct = _last_swings_monotonic(hi_vals, min_swings, "down") and _last_swings_monotonic(
        lo_vals, min_swings, "down"
    )
    if up_struct and move >= min_move:
        return "up"
    if down_struct and move <= -min_move:
        return "down"
    return "range"


def detect_price_trend(
    df: pd.DataFrame,
    params: TrendPullbackParams | None = None,
) -> str:
    params = params or DEFAULT_PULLBACK_PARAMS
    lookback = params.trend_lookback
    if len(df) < lookback + 5:
        return "range"
    tail = df.iloc[-lookback:]
    min_move = params.min_trend_move_pct
    if params.require_swing_structure:
        return _trend_from_swing_structure(
            tail, min_move=min_move, min_swings=max(2, int(params.min_swing_count))
        )
    return _trend_from_halves(tail, min_move)


def _range_position(close: float, support: float, resistance: float) -> float:
    span = max(resistance - support, 1e-9)
    return (close - support) / span


def _pullback_ready(
    df: pd.DataFrame,
    trend: str,
    *,
    support: float,
    resistance: float,
    params: TrendPullbackParams,
) -> tuple[bool, float]:
    if not params.require_pullback:
        close = float(df["close"].iloc[-1])
        return True, _range_position(close, support, resistance)

    pb = params.pullback_lookback
    if len(df) < pb + 2:
        return False, 0.0
    close = float(df["close"].iloc[-1])
    pos = _range_position(close, support, resistance)
    tail = df.iloc[-pb:]
    recent_high = float(tail["high"].max())
    recent_low = float(tail["low"].min())
    swing = params.min_pullback_from_swing_pct

    if trend == "up":
        dipped = recent_high > close * (1.0 + swing)
        in_zone = params.long_pos_min <= pos <= params.long_pos_max
        return bool(dipped and in_zone), pos
    if trend == "down":
        rallied = recent_low < close * (1.0 - swing)
        in_zone = params.short_pos_min <= pos <= params.short_pos_max
        return bool(rallied and in_zone), pos
    return False, pos


def _momentum_candle_confirms(df: pd.DataFrame, trend: str) -> bool:
    last = df.iloc[-1]
    close = float(last["close"])
    open_ = float(last["open"])
    if trend == "up":
        return close >= open_
    if trend == "down":
        return close <= open_
    return False


def build_trend_plan(
    df: pd.DataFrame,
    snap: dict[str, Any],
    params: TrendPullbackParams | None = None,
) -> dict[str, Any] | None:
    params = params or DEFAULT_PULLBACK_PARAMS
    trend = detect_price_trend(df, params)
    if trend == "range":
        return None

    support = float(snap["support_level"])
    resistance = float(snap["resistance_level"])
    pullback_ok, range_pos = _pullback_ready(
        df, trend, support=support, resistance=resistance, params=params
    )
    if not pullback_ok:
        return None
    if not _momentum_candle_confirms(df, trend):
        return None

    rel_vol = float(snap.get("context", {}).get("rel_volume", 0.0))
    if params.min_rel_volume > 0 and rel_vol < params.min_rel_volume:
        return None

    if params.block_asian_session:
        hour = bar_hour_utc(df.index[-1])
        if in_utc_hour_window(hour, params.asian_session_hours_utc):
            return None

    last = df.iloc[-1]
    close = float(last["close"])
    atr = max(float(last.get("atr_14", 0.0)), 1e-9)
    atr_pct = atr / max(close, 1e-12)
    if params.min_atr_pct > 0 and atr_pct < params.min_atr_pct:
        return None

    if params.block_opposite_level:
        near_support, near_resistance = _level_proximity(
            close,
            support,
            resistance,
            zone_frac=params.opposite_level_zone_frac,
        )
        if trend == "up" and near_resistance:
            return None
        if trend == "down" and near_support:
            return None

    if trend == "up":
        direction = "Long"
        entry = close
        stop = min(close - 1.5 * atr, support - 0.25 * atr)
    else:
        direction = "Short"
        entry = close
        stop = max(close + 1.5 * atr, resistance + 0.25 * atr)

    risk = max(abs(entry - stop), atr * 0.5)
    if risk <= 0:
        return None

    rr_tgt = params.rr_target
    tp_pct = float(params.tp_target_pct)
    if direction == "Long":
        if stop >= entry:
            return None
        tp2 = entry * (1.0 + tp_pct) if tp_pct > 0 else entry + rr_tgt * risk
        tp1 = entry + 1.0 * risk
    else:
        if stop <= entry:
            return None
        tp2 = entry * (1.0 - tp_pct) if tp_pct > 0 else entry - rr_tgt * risk
        tp1 = entry - 1.0 * risk

    rr = abs(tp2 - entry) / risk
    prob = 62.0 if direction == "Long" else 38.0
    if direction == "Short":
        prob = 62.0

    style = "pullback" if params.require_pullback else "momentum"
    return {
        "direction": direction,
        "probability_pct": float(prob),
        "entry": float(entry),
        "stop": float(stop),
        "target_1": float(tp1),
        "target_2": float(tp2),
        "risk_reward": float(rr),
        "trend": trend,
        "entry_style": style,
        "range_position_pct": round(100.0 * range_pos, 1),
        "trend_support": support,
        "trend_resistance": resistance,
        "rel_volume": round(rel_vol, 3),
        "atr_pct": round(atr_pct, 5),
    }


def htf_trend_at(
    df_htf: pd.DataFrame,
    as_of: pd.Timestamp,
    params: TrendPullbackParams | None = None,
) -> str:
    """Тренд на старшем TF по закрытым барам ≤ as_of (без lookahead)."""
    params = params or DEFAULT_PULLBACK_PARAMS
    if df_htf is None or df_htf.empty:
        return "range"
    sub = df_htf[df_htf.index <= as_of]
    if len(sub) < params.trend_lookback + 5:
        return "range"
    return detect_price_trend(sub, params)


def htf_trend_aligned(
    df_htf: pd.DataFrame,
    as_of: pd.Timestamp,
    ltf_trend: str,
    params: TrendPullbackParams | None = None,
) -> tuple[bool, str]:
    """1h-сигнал только если 4h (HTF) в том же направлении."""
    if ltf_trend not in ("up", "down"):
        return False, "ltf_range"
    htf = htf_trend_at(df_htf, as_of, params)
    if htf == "range":
        return False, "htf_range"
    if htf != ltf_trend:
        return False, f"htf_{htf}_vs_ltf_{ltf_trend}"
    return True, htf


def trend_only_stats(_snap: dict[str, Any], plan: dict[str, Any]) -> Any:
    trend = str(plan.get("trend", "range"))
    if trend == "up":
        return SimpleNamespace(prob_up=0.65, prob_down=0.35)
    if trend == "down":
        return SimpleNamespace(prob_up=0.35, prob_down=0.65)
    return SimpleNamespace(prob_up=0.5, prob_down=0.5)
