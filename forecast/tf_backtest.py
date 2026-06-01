"""
Walk-forward backtest: compare timeframes on historical altcoin data (scanner logic).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
import numpy as np
import pandas as pd

from .auto_trader import AutoTradeConfig, load_auto_trade_config, validate_setup
from .features import SIMILARITY_FEATURE_COLS, add_basic_features
from .indicators import add_basic_indicators
from .main import load_config
from .market_scanner import (
    _adjust_stage1_for_direction,
    _build_trade_plan,
    _historical_hit_for_direction,
    _level_proximity,
    _pattern_name,
    _stage1_snapshot,
)
from .paths import CONFIGS_DIR, PROCESSED_DATA_DIR, ensure_directories
from .signal_combiner import compute_volume_scores
from .similarity import SimilarityConfig, forecast_neighbor_stats

TF_BACKTEST_PATH = PROCESSED_DATA_DIR / "tf_backtest_latest.json"

# Исходный набор (10 альтов)
DEFAULT_SYMBOLS_LEGACY: tuple[str, ...] = (
    "SOL/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "NEAR/USDT",
    "INJ/USDT",
    "SUI/USDT",
    "WIF/USDT",
    "PEPE/USDT",
    "ARB/USDT",
)

# Топ-50 прогон (TP 4%, rel_vol>=1.2): пары с total R > 1
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "DOGE/USDT",
    "ONDO/USDT",
    "INJ/USDT",
    "HOME/USDT",
    "SUI/USDT",
    "NOM/USDT",
    "RENDER/USDT",
    "ICP/USDT",
)

_STABLE_OR_FIAT_BASES = frozenset(
    {
        "USDT",
        "USDC",
        "DAI",
        "TUSD",
        "FDUSD",
        "USDE",
        "EUR",
        "BUSD",
        "AEUR",
        "USD1",
    }
)


def fetch_top_usdt_symbols(exchange: ccxt.Exchange, *, limit: int = 200) -> tuple[str, ...]:
    """Top USDT spot pairs by 24h quote volume (liquid alts + majors)."""
    exchange.load_markets()
    tickers = exchange.fetch_tickers()
    rows: list[tuple[float, str]] = []
    for sym, t in tickers.items():
        if not sym.endswith("/USDT"):
            continue
        m = exchange.markets.get(sym) or {}
        if not m.get("active", True) or not m.get("spot"):
            continue
        base = sym.split("/")[0]
        if base in _STABLE_OR_FIAT_BASES:
            continue
        qv = float(t.get("quoteVolume") or 0.0)
        if qv <= 0:
            continue
        rows.append((qv, sym))
    rows.sort(key=lambda x: -x[0])
    return tuple(sym for _, sym in rows[:limit])


DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "30m", "1h", "2h", "4h")

TARGET_TRADES_PER_TF = 100
FEE_R_PER_TRADE = 0.08  # ~0.08R round-trip cost (2×4bp)

BARS_BY_TF: dict[str, int] = {
    "15m": 1000,
    "30m": 1000,
    "1h": 1000,
    "2h": 1000,
    "4h": 800,
}

STEP_BY_TF: dict[str, int] = {
    "15m": 8,
    "30m": 4,
    "1h": 2,
    "2h": 1,
    "4h": 1,
}

MAX_HOLD_BARS_BY_TF: dict[str, int] = {
    "15m": 96,
    "30m": 72,
    "1h": 48,
    "2h": 36,
    "4h": 24,
}


@dataclass
class TfBacktestConfig:
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    target_trades_per_tf: int = TARGET_TRADES_PER_TF
    stage1_min_score: float = 20.0
    stage1_relax_score: float = 12.0


def load_tf_backtest_result() -> dict[str, Any]:
    if not TF_BACKTEST_PATH.is_file():
        return {"status": "idle", "by_timeframe": {}, "trades": []}
    try:
        return json.loads(TF_BACKTEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": "bad cache file", "by_timeframe": {}, "trades": []}


def save_tf_backtest_result(payload: dict[str, Any]) -> None:
    ensure_directories()
    TF_BACKTEST_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _fetch_df(exchange: ccxt.Exchange, symbol: str, timeframe: str, bars: int) -> pd.DataFrame | None:
    try:
        rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=bars)
    except Exception:
        return None
    if not rows or len(rows) < 280:
        return None
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime")
    try:
        return add_basic_features(add_basic_indicators(df))
    except Exception:
        return None


def _simulate_exit(
    df: pd.DataFrame,
    entry_i: int,
    *,
    side: str,
    entry: float,
    stop: float,
    tp: float,
    max_bars: int,
) -> tuple[float, str, int]:
    end = min(entry_i + 1 + max_bars, len(df))
    for j in range(entry_i + 1, end):
        row = df.iloc[j]
        hi = float(row["high"])
        lo = float(row["low"])
        if side == "long":
            if lo <= stop:
                return stop, "stop", j
            if hi >= tp:
                return tp, "tp", j
        else:
            if hi >= stop:
                return stop, "stop", j
            if lo <= tp:
                return tp, "tp", j
    exit_px = float(df.iloc[end - 1]["close"])
    return exit_px, "time", end - 1


def _trade_r(side: str, entry: float, exit_px: float, stop: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if side == "long":
        raw = (exit_px - entry) / risk
    else:
        raw = (entry - exit_px) / risk
    return raw - FEE_R_PER_TRADE


def _build_candidate(
    *,
    symbol: str,
    snap: dict[str, Any],
    plan: dict[str, Any],
    st,
    stage1: float,
    df: pd.DataFrame,
) -> dict[str, Any]:
    last = df.iloc[-1]
    close = float(last["close"])
    support = float(snap["support_level"])
    resistance = float(snap["resistance_level"])
    vol_up, vol_down = compute_volume_scores(df)
    near_support, near_resistance = _level_proximity(close, support, resistance)
    hist_hit = _historical_hit_for_direction(str(plan["direction"]), st)
    score = float(
        np.clip(
            stage1 * 0.45
            + hist_hit * 100.0 * 0.35
            + min(float(plan["risk_reward"]) / 3.0, 1.0) * 20.0,
            0.0,
            100.0,
        )
    )
    return {
        "symbol": symbol,
        "pattern": _pattern_name(snap["patterns"]),
        "setup": plan,
        "score": score,
        "atr_pct": float(last["atr_14"]) / max(close, 1e-12),
        "vol_s_up": float(vol_up),
        "vol_s_down": float(vol_down),
        "short_near_support": str(plan["direction"]) == "Short" and near_support,
        "retest_breakout": bool(snap.get("retest_breakout")),
        "breakout_consolidation": bool((snap.get("patterns") or {}).get("breakout_consolidation")),
    }


def backtest_symbol_timeframe(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    similarity_cfg: SimilarityConfig,
    auto_cfg: AutoTradeConfig,
    stage1_min: float,
    cooldown_bars: int = 4,
) -> list[dict[str, Any]]:
    min_len = similarity_cfg.window_bars + similarity_cfg.forecast_horizon_bars + 12
    min_len = max(min_len, 72)
    step = STEP_BY_TF.get(timeframe, 2)
    max_hold = MAX_HOLD_BARS_BY_TF.get(timeframe, 48)
    trades: list[dict[str, Any]] = []
    next_i = min_len
    end = len(df) - max_hold - 1

    while next_i < end:
        sub = df.iloc[: next_i + 1]
        snap = _stage1_snapshot(sub)
        if float(snap["stage1_score"]) < stage1_min * 0.85:
            next_i += step
            continue
        try:
            st = forecast_neighbor_stats(sub, list(SIMILARITY_FEATURE_COLS), similarity_cfg)
        except Exception:
            next_i += step
            continue
        plan = _build_trade_plan(sub, snap, st)
        if plan is None:
            next_i += step
            continue

        last = sub.iloc[-1]
        close = float(last["close"])
        candle_bullish = close > float(last["open"])
        rel_vol = float(snap["context"]["rel_volume"])
        support = float(snap["support_level"])
        resistance = float(snap["resistance_level"])
        vol_up, vol_down = compute_volume_scores(sub)
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
        if stage1 < stage1_min:
            next_i += step
            continue

        cand = _build_candidate(symbol=symbol, snap=snap, plan=plan, st=st, stage1=stage1, df=sub)
        ok, reason = validate_setup(cand, auto_cfg)
        if not ok:
            next_i += step
            continue

        side = str(plan["direction"]).lower()
        entry = close
        stop = float(plan["stop"])
        tp = float(plan["target_2"])
        exit_px, exit_reason, exit_i = _simulate_exit(
            df,
            next_i,
            side=side,
            entry=entry,
            stop=stop,
            tp=tp,
            max_bars=max_hold,
        )
        r_mult = _trade_r(side, entry, exit_px, stop)
        trades.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "side": side,
                "entry_time": str(sub.index[-1]),
                "exit_time": str(df.index[exit_i]),
                "entry": entry,
                "exit": exit_px,
                "stop": stop,
                "tp": tp,
                "exit_reason": exit_reason,
                "r_multiple": round(r_mult, 3),
                "win": r_mult > 0,
                "score": float(cand["score"]),
                "rr_plan": float(plan["risk_reward"]),
            }
        )
        next_i = exit_i + cooldown_bars

    return trades


def _aggregate_by_tf(trades: list[dict[str, Any]], target: int) -> dict[str, Any]:
    by_tf: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_tf.setdefault(str(t["timeframe"]), []).append(t)

    summary: dict[str, Any] = {}
    for tf, rows in by_tf.items():
        rs = [float(r["r_multiple"]) for r in rows]
        wins = sum(1 for r in rs if r > 0)
        n = len(rs)
        summary[tf] = {
            "trades": n,
            "target": target,
            "target_reached": n >= target,
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / n, 1) if n else 0.0,
            "avg_r": round(float(np.mean(rs)), 3) if rs else 0.0,
            "total_r": round(float(np.sum(rs)), 2) if rs else 0.0,
            "profit_factor": _profit_factor(rs),
            "best_symbol": _best_symbol(rows),
        }
    return summary


def _profit_factor(rs: list[float]) -> float | None:
    gains = sum(r for r in rs if r > 0)
    losses = abs(sum(r for r in rs if r < 0))
    if losses <= 0:
        return None if gains <= 0 else 99.0
    return round(gains / losses, 2)


def _best_symbol(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    by_sym: dict[str, float] = {}
    for r in rows:
        by_sym.setdefault(str(r["symbol"]), 0.0)
        by_sym[str(r["symbol"])] += float(r["r_multiple"])
    return max(by_sym, key=by_sym.get)


def run_timeframe_study(
  *,
    bt_cfg: TfBacktestConfig | None = None,
    yaml_cfg: dict[str, Any] | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Run full multi-TF study; saves JSON cache."""
    bt_cfg = bt_cfg or TfBacktestConfig()
    cfg_path = Path(config_path) if config_path else CONFIGS_DIR / "config.yaml"
    app_cfg = load_config(cfg_path)
    similarity_cfg = app_cfg.similarity
    auto_cfg = load_auto_trade_config(yaml_cfg)

    payload: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "symbols": list(bt_cfg.symbols),
        "timeframes": list(bt_cfg.timeframes),
        "target_trades_per_tf": bt_cfg.target_trades_per_tf,
        "by_timeframe": {},
        "trades": [],
    }
    save_tf_backtest_result(payload)

    exchange = ccxt.binance({"enableRateLimit": True})
    all_trades: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    for tf in bt_cfg.timeframes:
        tf_trades: list[dict[str, Any]] = []
        bars = BARS_BY_TF.get(tf, 1000)
        stage1_min = bt_cfg.stage1_min_score

        for symbol in bt_cfg.symbols:
            if len(tf_trades) >= bt_cfg.target_trades_per_tf:
                break
            df = _fetch_df(exchange, symbol, tf, bars)
            if df is None:
                continue
            sym_trades = backtest_symbol_timeframe(
                df,
                symbol=symbol,
                timeframe=tf,
                similarity_cfg=similarity_cfg,
                auto_cfg=auto_cfg,
                stage1_min=stage1_min,
            )
            tf_trades.extend(sym_trades)
            if len(tf_trades) >= bt_cfg.target_trades_per_tf:
                tf_trades = tf_trades[: bt_cfg.target_trades_per_tf]
                break

        if len(tf_trades) < bt_cfg.target_trades_per_tf:
            for symbol in bt_cfg.symbols:
                if len(tf_trades) >= bt_cfg.target_trades_per_tf:
                    break
                df = _fetch_df(exchange, symbol, tf, bars)
                if df is None:
                    continue
                extra = backtest_symbol_timeframe(
                    df,
                    symbol=symbol,
                    timeframe=tf,
                    similarity_cfg=similarity_cfg,
                    auto_cfg=auto_cfg,
                    stage1_min=bt_cfg.stage1_relax_score,
                )
                seen = {(t["symbol"], t["entry_time"]) for t in tf_trades}
                for t in extra:
                    key = (t["symbol"], t["entry_time"])
                    if key not in seen:
                        tf_trades.append(t)
                        seen.add(key)
                    if len(tf_trades) >= bt_cfg.target_trades_per_tf:
                        tf_trades = tf_trades[: bt_cfg.target_trades_per_tf]
                        break

        all_trades.extend(tf_trades)
        print(f"[tf_backtest] {tf}: {len(tf_trades)} trades", flush=True)

    by_tf = _aggregate_by_tf(all_trades, bt_cfg.target_trades_per_tf)
    ranked = sorted(
        by_tf.items(),
        key=lambda kv: (kv[1].get("total_r") or -999, kv[1].get("win_rate_pct") or 0),
        reverse=True,
    )
    best_tf = ranked[0][0] if ranked else None

    payload = {
        "status": "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round(time.perf_counter() - t0, 1),
        "symbols": list(bt_cfg.symbols),
        "timeframes": list(bt_cfg.timeframes),
        "target_trades_per_tf": bt_cfg.target_trades_per_tf,
        "best_timeframe": best_tf,
        "by_timeframe": by_tf,
        "ranking": [{"timeframe": tf, **stats} for tf, stats in ranked],
        "trades_count": len(all_trades),
        "note": (
            "Логика как у сканера: stage1 + kNN + validate_setup; выход по стоп/тейк2 "
            f"или тайм-аут; цель {bt_cfg.target_trades_per_tf} сделок на TF."
        ),
    }
    save_tf_backtest_result(payload)
    return payload


def run_timeframe_study_background(yaml_cfg: dict[str, Any] | None = None) -> None:
    try:
        run_timeframe_study(yaml_cfg=yaml_cfg)
    except Exception as e:
        save_tf_backtest_result(
            {
                "status": "error",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"[tf_backtest] error: {e}", flush=True)
