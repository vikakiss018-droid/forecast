from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import ccxt
import numpy as np
import pandas as pd

from .features import SIMILARITY_FEATURE_COLS, add_basic_features
from .indicators import add_basic_indicators
from .signal_combiner import compute_volume_scores
from .similarity import SimilarityConfig, forecast_neighbor_stats


@dataclass
class ScanConfig:
    timeframe: str = "1h"
    bars: int = 320
    top_n: int = 5
    stage1_min_score: float = 20.0
    max_symbols: int | None = None


LEVEL_LOOKBACK = 120
LEVEL_PROXIMITY_FRAC = 0.15
RANGE_ENTRY_ZONE_FRAC = 0.20


def _level_proximity(
    close: float,
    support: float,
    resistance: float,
    *,
    zone_frac: float = LEVEL_PROXIMITY_FRAC,
) -> tuple[bool, bool]:
    span = max(resistance - support, 1e-9)
    near_support = abs(close - support) / span < zone_frac
    near_resistance = abs(close - resistance) / span < zone_frac
    return near_support, near_resistance


def _is_tradeable_usdt_spot(sym: str, m: dict[str, Any]) -> bool:
    if not bool(m.get("active", True)):
        return False
    if m.get("spot") is False:
        return False
    if not sym.endswith("/USDT"):
        return False
    base = str(sym.split("/")[0]).upper()
    banned_suffix = ("UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S")
    return not any(base.endswith(s) for s in banned_suffix)


def _trend_label(last: pd.Series) -> str:
    e20 = float(last["ema_20"])
    e50 = float(last["ema_50"])
    e200 = float(last["ema_200"])
    if e20 > e50 > e200:
        return "up"
    if e20 < e50 < e200:
        return "down"
    return "range"


def _stage1_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    last = df.iloc[-1]
    close = float(last["close"])
    look = min(LEVEL_LOOKBACK, len(df))
    hi_look = float(df["high"].iloc[-look:].max())
    lo_look = float(df["low"].iloc[-look:].min())
    atr = float(last["atr_14"])
    atr = max(atr, 1e-9)

    near_support, near_resistance = _level_proximity(close, lo_look, hi_look)
    impulse = abs(float(last.get("ret_24", 0.0))) > 0.025
    squeeze = float(df["range"].iloc[-20:].mean()) < float(df["range"].iloc[-80:].mean()) * 0.7
    retest_breakout = bool(
        len(df) > 25
        and float(df["close"].iloc[-6:-1].max()) > float(df["high"].iloc[-30:-6].max())
        and near_resistance
    )

    # Pattern heuristics (lightweight, proxy-level)
    range20 = (df["high"].iloc[-20:].max() - df["low"].iloc[-20:].min()) / close
    range60 = (df["high"].iloc[-60:].max() - df["low"].iloc[-60:].min()) / close
    trend = _trend_label(last)
    triangle = squeeze and range20 < range60 * 0.65
    flag = impulse and squeeze
    wedge = squeeze and abs(float(last["ema_20"] - last["ema_50"]) / close) < 0.008
    channel = range60 > 0.04 and abs(float(last["ema_20"] - last["ema_50"]) / close) < 0.02
    double_bottom = (
        len(df) > 80
        and abs(float(df["low"].iloc[-15:].min() - df["low"].iloc[-70:-30].min()) / close) < 0.01
    )
    double_top = (
        len(df) > 80
        and abs(float(df["high"].iloc[-15:].max() - df["high"].iloc[-70:-30].max()) / close) < 0.01
    )
    accumulation = trend == "range" and range20 < 0.06
    breakout_cons = bool(
        len(df) > 30 and float(df["close"].iloc[-1]) > float(df["high"].iloc[-30:-1].max())
    )

    rel_vol = float(last["rel_volume"])
    vol_regime = float(last["volatility_24"])
    strong_move_to_zone = impulse

    score = 0.0
    score += 12.0 if (near_support or near_resistance) else 0.0
    score += 8.0 if squeeze else 0.0
    score += 8.0 if retest_breakout else 0.0
    score += 8.0 if (triangle or flag or wedge or channel) else 0.0
    score += 6.0 if (double_bottom or double_top) else 0.0
    score += 6.0 if (accumulation or breakout_cons) else 0.0
    score += 5.0 if strong_move_to_zone else 0.0

    return {
        "trend": trend,
        "near_support": near_support,
        "near_resistance": near_resistance,
        "impulse_to_zone": impulse,
        "squeeze_at_level": squeeze,
        "retest_breakout": retest_breakout,
        "patterns": {
            "triangle": triangle,
            "flag": flag,
            "wedge": wedge,
            "channel": channel,
            "double_bottom": double_bottom,
            "double_top": double_top,
            "accumulation": accumulation,
            "breakout_consolidation": breakout_cons,
        },
        "context": {
            "rel_volume": rel_vol,
            "open": float(last["open"]),
            "close": close,
            "volatility_24": vol_regime,
            "strong_move_to_zone": strong_move_to_zone,
        },
        "stage1_score": float(score),
        "support_level": lo_look,
        "resistance_level": hi_look,
    }


def _pattern_name(patterns: dict[str, bool]) -> str:
    for k, v in patterns.items():
        if v:
            return k.replace("_", " ")
    return "level setup"


def _adjust_stage1_for_direction(
    stage1: float,
    *,
    direction: str,
    rel_vol: float,
    candle_bullish: bool,
    close: float,
    support: float,
    resistance: float,
    vol_up: float,
    vol_down: float,
) -> float:
    """Directed volume bonus/penalty, signal_combiner volume, level context."""
    vol_bonus = min(max((rel_vol - 1.0) * 10.0, 0.0), 15.0)
    if direction == "Long" and candle_bullish:
        stage1 += vol_bonus
    elif direction == "Short" and not candle_bullish:
        stage1 += vol_bonus
    else:
        stage1 -= vol_bonus * 0.5

    if direction == "Long":
        stage1 += vol_up * 10.0
    elif direction == "Short":
        stage1 += vol_down * 10.0

    near_support, near_resistance = _level_proximity(close, support, resistance)
    if direction == "Short" and near_resistance and not candle_bullish:
        stage1 += 10.0
    elif direction == "Short" and near_support:
        stage1 -= 15.0

    return stage1


def _build_why_selected(
    snap: dict[str, Any],
    *,
    rel_vol: float,
    candle_bullish: bool,
    direction: str,
    close: float,
    support: float,
    resistance: float,
) -> str:
    reasons: list[str] = []
    if snap.get("squeeze_at_level"):
        reasons.append("сжатие у уровня")
    if rel_vol > 1.1:
        if candle_bullish:
            reasons.append("объём растёт (покупки)")
        else:
            reasons.append("объём растёт (продажи)")
    if snap.get("retest_breakout"):
        reasons.append("ретест пробоя")
    reasons.append(f"тренд: {snap['trend']}")

    near_support, near_resistance = _level_proximity(close, support, resistance)
    if direction == "Short" and near_resistance and not candle_bullish:
        reasons.append("у сопротивления")
    elif direction == "Short" and near_support:
        reasons.append("у поддержки (осторожно)")

    return ", ".join(reasons)


def _historical_hit_for_direction(direction: str, st) -> float:
    if direction == "Long":
        return float(st.prob_up)
    if direction == "Short":
        return float(st.prob_down)
    return float(max(st.prob_up, st.prob_down))


def _build_trade_plan(df: pd.DataFrame, snap: dict[str, Any], st) -> dict[str, Any] | None:
    last = df.iloc[-1]
    close = float(last["close"])
    atr = max(float(last["atr_14"]), 1e-9)
    support = float(snap["support_level"])
    resistance = float(snap["resistance_level"])
    trend = str(snap["trend"])
    span = max(resistance - support, 1e-9)

    if trend == "up":
        direction = "Long"
        entry = max(close, resistance * 1.001)
        stop = min(close - 1.5 * atr, support - 0.25 * atr)
    elif trend == "down":
        direction = "Short"
        entry = min(close, support * 0.999)
        stop = max(close + 1.5 * atr, resistance + 0.25 * atr)
    else:
        # range/flat: вход только у уровня, иначе пропустить сетап
        if abs(close - support) <= abs(resistance - close):
            if abs(close - support) / span >= RANGE_ENTRY_ZONE_FRAC:
                return None
            direction = "Long"
            entry = close
            stop = support - 1.2 * atr
        else:
            if abs(close - resistance) / span >= RANGE_ENTRY_ZONE_FRAC:
                return None
            direction = "Short"
            entry = close
            stop = resistance + 1.2 * atr

    risk = abs(entry - stop)
    risk = max(risk, atr * 0.5)
    if direction == "Long":
        tp1 = entry + 1.0 * risk
        tp2 = entry + 2.8 * risk
    else:
        tp1 = entry - 1.0 * risk
        tp2 = entry - 2.8 * risk
    rr = abs(tp2 - entry) / max(abs(entry - stop), 1e-9)

    if direction == "Long":
        prob = st.prob_up * 100.0
    elif direction == "Short":
        prob = st.prob_down * 100.0
    else:
        prob = max(st.prob_up, st.prob_down) * 100.0
    return {
        "direction": direction,
        "probability_pct": float(prob),
        "entry": float(entry),
        "stop": float(stop),
        "target_1": float(tp1),
        "target_2": float(tp2),
        "risk_reward": float(rr),
    }


def scan_market_top_setups(
    *,
    similarity_cfg: SimilarityConfig,
    scan_cfg: ScanConfig | None = None,
) -> dict[str, Any]:
    scan_cfg = scan_cfg or ScanConfig()
    ex = ccxt.binance({"enableRateLimit": True})
    markets = ex.load_markets()
    universe = sorted([s for s, m in markets.items() if _is_tradeable_usdt_spot(s, m)])
    if scan_cfg.max_symbols is not None:
        universe = universe[: max(1, int(scan_cfg.max_symbols))]

    candidates: list[dict[str, Any]] = []
    t_pairs = time.perf_counter()
    for symbol in universe:
        try:
            rows = ex.fetch_ohlcv(symbol, timeframe=scan_cfg.timeframe, limit=scan_cfg.bars)
        except Exception:
            continue
        if rows is None or len(rows) < 260:
            continue

        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("datetime")
        try:
            df = add_basic_features(add_basic_indicators(df))
        except Exception:
            continue
        min_len = similarity_cfg.window_bars + similarity_cfg.forecast_horizon_bars + 12
        if len(df) < max(min_len, 72):
            continue

        snap = _stage1_snapshot(df)

        try:
            st = forecast_neighbor_stats(df, list(SIMILARITY_FEATURE_COLS), similarity_cfg)
        except Exception:
            continue

        pattern = _pattern_name(snap["patterns"])
        plan = _build_trade_plan(df, snap, st)
        if plan is None:
            continue

        last = df.iloc[-1]
        close = float(last["close"])
        open_price = float(last["open"])
        atr_pct = float(last["atr_14"]) / max(close, 1e-12)
        candle_bullish = close > open_price
        rel_vol = float(snap["context"]["rel_volume"])
        support = float(snap["support_level"])
        resistance = float(snap["resistance_level"])
        vol_up, vol_down = compute_volume_scores(df)

        stage1 = _adjust_stage1_for_direction(
            float(snap["stage1_score"]),
            direction=str(plan["direction"]),
            rel_vol=rel_vol,
            candle_bullish=candle_bullish,
            close=close,
            support=support,
            resistance=resistance,
            vol_up=vol_up,
            vol_down=vol_down,
        )
        if stage1 < float(scan_cfg.stage1_min_score):
            continue

        hist_hit = _historical_hit_for_direction(str(plan["direction"]), st)
        score = float(
            np.clip(
                stage1 * 0.45
                + hist_hit * 100.0 * 0.35
                + min(plan["risk_reward"] / 3.0, 1.0) * 20.0,
                0.0,
                100.0,
            )
        )
        why_selected = _build_why_selected(
            snap,
            rel_vol=rel_vol,
            candle_bullish=candle_bullish,
            direction=str(plan["direction"]),
            close=close,
            support=support,
            resistance=resistance,
        )

        near_support, near_resistance = _level_proximity(close, support, resistance)
        short_near_support = str(plan["direction"]) == "Short" and near_support

        candidates.append(
            {
                "symbol": symbol,
                "pattern": pattern,
                "trend": snap["trend"],
                "historical_pattern_hit_pct": float(hist_hit * 100.0),
                "score": score,
                "setup": plan,
                "support_level": support,
                "resistance_level": resistance,
                "why_selected": why_selected,
                "candle_bullish": candle_bullish,
                "atr_pct": float(atr_pct),
                "vol_s_up": float(vol_up),
                "vol_s_down": float(vol_down),
                "short_near_support": short_near_support,
                "near_support": near_support,
                "near_resistance": near_resistance,
                "retest_breakout": bool(snap.get("retest_breakout")),
                "breakout_consolidation": bool((snap.get("patterns") or {}).get("breakout_consolidation")),
            }
        )

    scan_duration_sec = round(time.perf_counter() - t_pairs, 2)

    candidates = sorted(candidates, key=lambda x: float(x["score"]), reverse=True)
    top = candidates[: max(1, int(scan_cfg.top_n))]
    return {
        "universe_size": int(len(universe)),
        "candidates_found": int(len(candidates)),
        "top_setups": top,
        "scan_duration_sec": scan_duration_sec,
        "symbols_scanned": int(len(universe)),
    }

