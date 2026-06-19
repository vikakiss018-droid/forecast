"""Rank USDT pairs for scalp trading (1m liquidity, spread, TP/SL opportunity sim)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import ccxt
import numpy as np
import pandas as pd

from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .scalp_config import load_scalp_config
from .strategy_config import env_float, env_int
from .tf_backtest import DEFAULT_SYMBOLS, _fetch_df, fetch_top_usdt_symbols

SYMBOL_RANKING_SCALP_PATH = PROCESSED_DATA_DIR / "symbol_ranking_scalp_latest.json"
SYMBOL_RANKING_SCALP_LIVE_PATH = PROCESSED_DATA_DIR / "symbol_ranking_scalp_live.json"
DEFAULT_SCALP_RANK_TOP_N = 400
DEFAULT_SCALP_LIVE_N = 8


@dataclass(frozen=True)
class ScalpRankConfig:
    top_n: int
    bars_1m: int
    live_n: int
    tp_pct: float
    sl_pct: float
    hold_bars: int
    fee_pct: float
    sim_step_bars: int
    refine_top: int

    def to_meta(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "bars_1m": self.bars_1m,
            "live_n": self.live_n,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "hold_bars": self.hold_bars,
            "fee_pct": self.fee_pct,
            "sim_step_bars": self.sim_step_bars,
            "refine_top": self.refine_top,
            "rule": self.rule_text(),
        }

    def rule_text(self) -> str:
        return (
            f"scalp rank top-{self.top_n} on 1m; sim long TP={self.tp_pct}% SL={self.sl_pct}% "
            f"hold={self.hold_bars}m; fee={self.fee_pct}% RT; pick top-{self.live_n}"
        )


def scalp_rank_config_from_env() -> ScalpRankConfig:
    load_project_env(force=True)
    scalp = load_scalp_config()
    hold = max(1, int(round(scalp.time_stop_sec / 60.0)))
    return ScalpRankConfig(
        top_n=env_int("SCALP_RANK_TOP_N", DEFAULT_SCALP_RANK_TOP_N, positive=True),
        bars_1m=env_int("SCALP_RANK_BARS_1M", 1000, positive=True),
        live_n=env_int("SCALP_RANK_TARGET", DEFAULT_SCALP_LIVE_N, positive=True),
        tp_pct=scalp.min_tp_pct,
        sl_pct=scalp.sl_pct,
        hold_bars=hold,
        fee_pct=scalp.round_trip_fee_pct,
        sim_step_bars=env_int("SCALP_RANK_SIM_STEP", 45, positive=True),
        refine_top=env_int("SCALP_RANK_REFINE_TOP", 40, positive=True),
    )


def _median_range_bps(df: pd.DataFrame) -> float:
    mid = (df["high"] + df["low"]) / 2.0
    bps = (df["high"] - df["low"]) / mid.replace(0, np.nan) * 10_000.0
    return float(bps.median())


def _quote_volume_usdt(df: pd.DataFrame) -> float:
    return float((df["close"] * df["volume"]).sum())


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    last = float(c.iloc[-1])
    if last <= 0 or pd.isna(atr):
        return 0.0
    return float(atr / last * 100.0)


def simulate_scalp_long_1m(
    df: pd.DataFrame,
    *,
    tp_pct: float,
    sl_pct: float,
    hold_bars: int,
    fee_pct: float,
    step_bars: int,
) -> dict[str, Any]:
    """Simple long-only scalp sim on 1m OHLCV (proxy without order book)."""
    n = len(df)
    warmup = 60
    if n < warmup + hold_bars + 10:
        return {
            "sim_trades": 0,
            "sim_wins": 0,
            "sim_win_rate_pct": 0.0,
            "sim_expectancy_pct": 0.0,
            "sim_tp": 0,
            "sim_sl": 0,
            "sim_time_stop": 0,
        }

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()

    nets: list[float] = []
    tp_n = sl_n = ts_n = 0

    for i in range(warmup, n - hold_bars, step_bars):
        entry = float(opens[i])
        if entry <= 0:
            continue
        tp_px = entry * (1.0 + tp_pct / 100.0)
        sl_px = entry * (1.0 - sl_pct / 100.0)
        exit_px = float(closes[i + hold_bars])
        outcome = "TIME_STOP"

        for j in range(i + 1, i + 1 + hold_bars):
            if float(lows[j]) <= sl_px:
                exit_px = sl_px
                outcome = "SL"
                sl_n += 1
                break
            if float(highs[j]) >= tp_px:
                exit_px = tp_px
                outcome = "TP"
                tp_n += 1
                break
        else:
            ts_n += 1

        gross = (exit_px - entry) / entry * 100.0
        nets.append(gross - fee_pct)

    trades = len(nets)
    wins = sum(1 for x in nets if x > 0)
    win_rate = (100.0 * wins / trades) if trades else 0.0
    expectancy = float(np.mean(nets)) if nets else 0.0

    return {
        "sim_trades": trades,
        "sim_wins": wins,
        "sim_win_rate_pct": round(win_rate, 1),
        "sim_expectancy_pct": round(expectancy, 4),
        "sim_tp": tp_n,
        "sim_sl": sl_n,
        "sim_time_stop": ts_n,
    }


def _live_spread_bps(exchange: ccxt.Exchange, symbol: str) -> float | None:
    try:
        ob = exchange.fetch_order_book(symbol, limit=5)
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        if not bids or not asks:
            return None
        bid = float(bids[0][0])
        ask = float(asks[0][0])
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return None
        return (ask - bid) / mid * 10_000.0
    except Exception:
        return None


def compute_scalp_score(row: dict[str, Any]) -> float:
    """Higher is better for scalp suitability."""
    if int(row.get("sim_trades") or 0) < 3:
        return -999.0

    expectancy = float(row.get("sim_expectancy_pct") or 0.0)
    win_rate = float(row.get("sim_win_rate_pct") or 0.0)
    quote_vol = float(row.get("quote_vol_usdt") or 0.0)
    range_bps = float(row.get("median_range_bps") or 0.0)
    spread = row.get("spread_bps")
    spread_bps = float(spread) if spread is not None else 20.0

    vol_score = min(1.0, math.log10(quote_vol + 1.0) / 8.0)
    range_score = max(0.0, 1.0 - abs(range_bps - 18.0) / 35.0)
    spread_score = max(0.0, 1.0 - spread_bps / 35.0)
    exp_score = max(-1.0, min(1.0, expectancy / 0.15))
    wr_score = win_rate / 100.0

    return round(
        exp_score * 35.0
        + wr_score * 20.0
        + vol_score * 20.0
        + range_score * 12.0
        + spread_score * 13.0,
        3,
    )


def analyze_symbol_for_scalp(
    exchange: ccxt.Exchange,
    symbol: str,
    cfg: ScalpRankConfig,
) -> dict[str, Any] | None:
    df = _fetch_df(exchange, symbol, "1m", cfg.bars_1m)
    if df is None or len(df) < 200:
        return None

    sim = simulate_scalp_long_1m(
        df,
        tp_pct=cfg.tp_pct,
        sl_pct=cfg.sl_pct,
        hold_bars=cfg.hold_bars,
        fee_pct=cfg.fee_pct,
        step_bars=cfg.sim_step_bars,
    )

    row: dict[str, Any] = {
        "symbol": symbol,
        "quote_vol_usdt": round(_quote_volume_usdt(df), 0),
        "median_range_bps": round(_median_range_bps(df), 2),
        "atr_1m_pct": round(_atr_pct(df), 4),
        "bars": len(df),
        **sim,
    }
    row["scalp_score"] = compute_scalp_score(row)
    return row


def save_scalp_ranking_result(payload: dict[str, Any]) -> None:
    ensure_directories()
    SYMBOL_RANKING_SCALP_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def save_scalp_live_ranking(payload: dict[str, Any]) -> Path:
    ensure_directories()
    SYMBOL_RANKING_SCALP_LIVE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return SYMBOL_RANKING_SCALP_LIVE_PATH


def load_scalp_ranking_result() -> dict[str, Any]:
    if not SYMBOL_RANKING_SCALP_PATH.is_file():
        return {"status": "idle", "path": str(SYMBOL_RANKING_SCALP_PATH)}
    try:
        return json.loads(SYMBOL_RANKING_SCALP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "path": str(SYMBOL_RANKING_SCALP_PATH)}


def load_scalp_ranking_live() -> dict[str, Any]:
    if not SYMBOL_RANKING_SCALP_LIVE_PATH.is_file():
        return {"status": "idle", "path": str(SYMBOL_RANKING_SCALP_LIVE_PATH)}
    try:
        return json.loads(SYMBOL_RANKING_SCALP_LIVE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "path": str(SYMBOL_RANKING_SCALP_LIVE_PATH)}


def load_scalp_live_symbols() -> tuple[str, ...]:
    data = load_scalp_ranking_live()
    syms = data.get("symbols") or []
    return tuple(str(s) for s in syms if s)


def approve_scalp_symbols(symbols: list[str]) -> dict[str, Any]:
    latest = load_scalp_ranking_result()
    ranking = list(latest.get("ranking") or [])
    by_sym = {str(r["symbol"]): dict(r) for r in ranking}
    picked: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols, 1):
        row = dict(by_sym.get(sym) or {"symbol": sym})
        row["rank"] = i
        picked.append(row)
    payload = {
        "status": "done",
        "source": str(SYMBOL_RANKING_SCALP_PATH),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_manually": True,
        "purpose": "scalp_top8",
        "count": len(picked),
        "symbols": [str(r["symbol"]) for r in picked],
        "ranking": picked,
        "ranking_note": "user-approved scalp pairs (used by scalp engine when SCALP_USE_DEDICATED_LIST=1)",
    }
    save_scalp_live_ranking(payload)
    return payload


def run_scalp_pair_ranking_job(
    *,
    top_n: int | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    cfg = scalp_rank_config_from_env()
    if top_n is not None:
        cfg = ScalpRankConfig(
            top_n=int(top_n),
            bars_1m=cfg.bars_1m,
            live_n=cfg.live_n,
            tp_pct=cfg.tp_pct,
            sl_pct=cfg.sl_pct,
            hold_bars=cfg.hold_bars,
            fee_pct=cfg.fee_pct,
            sim_step_bars=cfg.sim_step_bars,
            refine_top=cfg.refine_top,
        )

    exchange = ccxt.binance({"enableRateLimit": True})
    if cfg.top_n > len(DEFAULT_SYMBOLS):
        symbols = list(fetch_top_usdt_symbols(exchange, limit=cfg.top_n))
    else:
        symbols = list(DEFAULT_SYMBOLS[: cfg.top_n])

    started_at = datetime.now(timezone.utc).isoformat()
    running: dict[str, Any] = {
        "status": "running",
        "mode": "scalp_pair_rank",
        "kind": "scalp_pair_test",
        "test_config": cfg.to_meta(),
        "started_at": started_at,
        "symbols_count": len(symbols),
        "progress": {"current": 0, "total": len(symbols), "symbol": None},
        "ranking": [],
        "ranking_note": "sorted by scalp_score descending (best first)",
        "rule": cfg.rule_text(),
    }
    save_scalp_ranking_result(running)

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []

    for i, sym in enumerate(symbols, 1):
        prog = {"current": i, "total": len(symbols), "symbol": sym}
        running["progress"] = prog
        save_scalp_ranking_result(running)
        if progress_cb:
            progress_cb(prog)

        row = analyze_symbol_for_scalp(exchange, sym, cfg)
        if row is None:
            skipped.append(sym)
            continue
        rows.append(row)
        print(
            f"[scalp-rank] {i}/{len(symbols)} {sym} score={row['scalp_score']:.1f} "
            f"exp={row['sim_expectancy_pct']:+.3f}% spread=—",
            flush=True,
        )

    rows.sort(key=lambda r: -float(r.get("scalp_score") or -999))

    # Refine top candidates with live spread from order book
    refine_n = min(cfg.refine_top, len(rows))
    for row in rows[:refine_n]:
        spread = _live_spread_bps(exchange, str(row["symbol"]))
        if spread is not None:
            row["spread_bps"] = round(spread, 2)
            row["scalp_score"] = compute_scalp_score(row)

    rows.sort(key=lambda r: -float(r.get("scalp_score") or -999))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    top_suggested = [str(r["symbol"]) for r in rows[: cfg.live_n]]
    finished_at = datetime.now(timezone.utc).isoformat()

    payload: dict[str, Any] = {
        "status": "done",
        "mode": "scalp_pair_rank",
        "kind": "scalp_pair_test",
        "test_config": cfg.to_meta(),
        "started_at": started_at,
        "finished_at": finished_at,
        "symbols_count": len(symbols),
        "analyzed_count": len(rows),
        "skipped_count": len(skipped),
        "skipped": skipped[:30],
        "suggested_top_n": cfg.live_n,
        "suggested_symbols": top_suggested,
        "progress": {"current": len(symbols), "total": len(symbols), "symbol": None},
        "ranking": rows,
        "ranking_note": "sorted by scalp_score descending (best first)",
        "rule": cfg.rule_text(),
    }
    save_scalp_ranking_result(payload)

    print(f"\n[scalp-rank] top-{cfg.live_n} suggested: {', '.join(top_suggested)}", flush=True)
    print(f"[scalp-rank] saved: {SYMBOL_RANKING_SCALP_PATH}", flush=True)
    return payload


def run_scalp_pair_ranking_background() -> None:
    try:
        run_scalp_pair_ranking_job()
    except Exception as e:
        save_scalp_ranking_result(
            {
                "status": "error",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "kind": "scalp_pair_test",
            }
        )
        print(f"[scalp-rank] error: {e}", flush=True)
