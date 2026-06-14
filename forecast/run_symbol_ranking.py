"""Rank symbols by total R (per-symbol backtest, same trend params as multi_bt)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import ccxt

from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .single_symbol_backtest import _aggregate_by_symbol, backtest_single_symbol
from .auto_trader import load_auto_trade_config
from .tf_backtest import BARS_BY_TF, DEFAULT_SYMBOLS, _fetch_df, fetch_top_usdt_symbols
from .trend_rules import DEFAULT_TREND_PARAMS, TrendPullbackParams

SYMBOL_RANKING_PATH = PROCESSED_DATA_DIR / "symbol_ranking_latest.json"
SYMBOL_RANKING_FILTERED_PATH = PROCESSED_DATA_DIR / "symbol_ranking_filtered_r05_win50.json"
DEFAULT_RANK_TOP_N = 400
FILTER_TOTAL_R_GT = 0.5
FILTER_WIN_RATE_PCT_GT = 50.0


def build_filtered_ranking(
    ranking: list[dict],
    *,
    source_path: str | None = None,
    parent_symbols_count: int | None = None,
) -> dict:
    """Пары с total_r > 0.5, win_rate > 50%, хотя бы 1 сделка."""
    filtered = [
        dict(r)
        for r in ranking
        if float(r["total_r"]) > FILTER_TOTAL_R_GT
        and float(r["win_rate_pct"]) > FILTER_WIN_RATE_PCT_GT
        and int(r["trades"]) > 0
    ]
    filtered.sort(key=lambda x: -float(x["total_r"]))
    for i, row in enumerate(filtered, 1):
        row["rank"] = i
    symbols = [str(r["symbol"]) for r in filtered]
    return {
        "status": "done",
        "source": source_path or str(SYMBOL_RANKING_PATH),
        "criteria": {
            "total_r_gt": FILTER_TOTAL_R_GT,
            "win_rate_pct_gt": FILTER_WIN_RATE_PCT_GT,
            "min_trades": 1,
        },
        "parent_symbols_count": parent_symbols_count,
        "count": len(filtered),
        "symbols": symbols,
        "ranking": filtered,
        "ranking_note": "sorted by total_r descending (best first)",
    }


def save_filtered_ranking(payload: dict) -> Path:
    ensure_directories()
    SYMBOL_RANKING_FILTERED_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return SYMBOL_RANKING_FILTERED_PATH


def save_symbol_ranking_result(payload: dict[str, Any]) -> None:
    ensure_directories()
    SYMBOL_RANKING_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _ranking_rows_from_by(
    symbols: tuple[str, ...] | list[str],
    by: dict[str, dict[str, Any]],
    *,
    risk_usd: float = 5.0,
) -> list[dict[str, Any]]:
    rows = sorted(by.items(), key=lambda x: x[1]["total_r"])
    return [
        {
            "rank": i,
            "symbol": sym,
            "total_r": float(d["total_r"]),
            "estimated_pnl_usdt": round(float(d["total_r"]) * risk_usd, 2),
            "trades": int(d["trades"]),
            "wins": int(d["wins"]),
            "win_rate_pct": float(d["win_rate_pct"]),
        }
        for i, (sym, d) in enumerate(rows, 1)
    ]


def _auto_trade_cfg_for_ranking() -> Any:
    from .strategy_config import yaml_section

    at_yaml = yaml_section("auto_trade")
    auto_cfg = load_auto_trade_config(at_yaml)
    auto_cfg.min_probability_pct = float(at_yaml.get("min_probability_pct", 50))
    auto_cfg.min_score = float(at_yaml.get("min_score", 18))
    auto_cfg.min_atr_pct = float(at_yaml.get("min_atr_pct", 0))
    auto_cfg.allow_level_breakout = bool(at_yaml.get("allow_level_breakout", False))
    auto_cfg.allow_triangle = bool(at_yaml.get("allow_triangle", False))
    return auto_cfg


def run_symbol_ranking_job(
    *,
    top_n: int | None = None,
    save_auto_filtered: bool = False,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Backtest top-N USDT pairs; returns ranking payload (status=done)."""
    load_project_env(force=True)
    n = int(top_n if top_n is not None else os.environ.get("MULTI_BT_TOP_N", str(DEFAULT_RANK_TOP_N)))
    tf = os.environ.get("MULTI_BT_TIMEFRAME", "1h").strip() or "1h"
    per_sym = int(os.environ.get("RANK_TARGET_PER_SYMBOL", "30"))
    use_top = os.environ.get("MULTI_BT_USE_TOP_VOLUME", "1").strip() not in ("0", "false", "no")

    min_vol = float(os.environ.get("TREND_MIN_REL_VOLUME", str(DEFAULT_TREND_PARAMS.min_rel_volume)))
    trend_params = TrendPullbackParams(
        require_pullback=False,
        require_htf_align=False,
        min_rel_volume=min_vol,
        min_atr_pct=float(os.environ.get("TREND_MIN_ATR_PCT", "0")),
        trend_lookback=int(os.environ.get("TREND_LOOKBACK", str(DEFAULT_TREND_PARAMS.trend_lookback))),
        min_trend_move_pct=float(os.environ.get("TREND_MIN_MOVE_PCT", str(DEFAULT_TREND_PARAMS.min_trend_move_pct))),
    )

    exchange = ccxt.binance({"enableRateLimit": True})
    if use_top and n > len(DEFAULT_SYMBOLS):
        symbols = fetch_top_usdt_symbols(exchange, limit=n)
    else:
        symbols = DEFAULT_SYMBOLS[:n]

    auto_cfg = _auto_trade_cfg_for_ranking()
    bars = BARS_BY_TF.get(tf, 1000)
    started_at = datetime.now(timezone.utc).isoformat()

    running: dict[str, Any] = {
        "status": "running",
        "started_at": started_at,
        "symbols_count": len(symbols),
        "timeframe": tf,
        "tp_target_pct": trend_params.tp_target_pct,
        "min_rel_volume": trend_params.min_rel_volume,
        "target_trades_per_symbol": per_sym,
        "progress": {"current": 0, "total": len(symbols), "symbol": None},
        "ranking": [],
        "ranking_note": "sorted by total_r ascending (worst first); see rank field",
    }
    save_symbol_ranking_result(running)

    print(
        f"[rank] {len(symbols)} pairs, {tf}, TP={trend_params.tp_target_pct * 100:.0f}%, "
        f"rel_vol>={trend_params.min_rel_volume}, up to {per_sym} trades/symbol...",
        flush=True,
    )

    all_trades: list[dict] = []
    skipped: list[str] = []
    for i, symbol in enumerate(symbols, 1):
        running["progress"] = {"current": i, "total": len(symbols), "symbol": symbol}
        if progress_cb:
            progress_cb(running)
        else:
            save_symbol_ranking_result(running)

        df = _fetch_df(exchange, symbol, tf, bars)
        if df is None:
            skipped.append(symbol)
            continue
        sym_trades = backtest_single_symbol(
            df,
            symbol=symbol,
            timeframe=tf,
            auto_cfg=auto_cfg,
            stage1_min=18.0,
            target_trades=per_sym,
            trend_params=trend_params,
        )
        if not sym_trades and per_sym > 0:
            sym_trades = backtest_single_symbol(
                df,
                symbol=symbol,
                timeframe=tf,
                auto_cfg=auto_cfg,
                stage1_min=12.0,
                target_trades=per_sym,
                trend_params=trend_params,
            )
        all_trades.extend(sym_trades)
        r = sum(float(t["r_multiple"]) for t in sym_trades)
        print(f"[rank] {i}/{len(symbols)} {symbol}: {len(sym_trades)} trades, R={r:.2f}", flush=True)

    by = _aggregate_by_symbol(all_trades)
    for sym in symbols:
        if sym not in by:
            by[sym] = {"trades": 0, "wins": 0, "win_rate_pct": 0.0, "total_r": 0.0}

    total_r = sum(float(v["total_r"]) for v in by.values())
    ranking = _ranking_rows_from_by(symbols, by)
    payload: dict[str, Any] = {
        "status": "done",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "symbols_count": len(symbols),
        "timeframe": tf,
        "tp_target_pct": trend_params.tp_target_pct,
        "min_rel_volume": trend_params.min_rel_volume,
        "target_trades_per_symbol": per_sym,
        "total_trades": len(all_trades),
        "total_r": round(total_r, 2),
        "skipped_symbols": skipped,
        "ranking": ranking,
        "ranking_note": "sorted by total_r ascending (worst first); see rank field",
        "progress": {"current": len(symbols), "total": len(symbols), "symbol": None},
    }
    save_symbol_ranking_result(payload)
    print(f"\n[rank] сохранено: {SYMBOL_RANKING_PATH}", flush=True)

    if save_auto_filtered:
        filtered_payload = build_filtered_ranking(
            ranking,
            source_path=str(SYMBOL_RANKING_PATH),
            parent_symbols_count=len(symbols),
        )
        filtered_payload["created_at"] = payload["finished_at"]
        filt_path = save_filtered_ranking(filtered_payload)
        print(
            f"[rank] фильтр R>{FILTER_TOTAL_R_GT} win>{FILTER_WIN_RATE_PCT_GT}%: "
            f"{filtered_payload['count']} пар → {filt_path}",
            flush=True,
        )

    rows = sorted(by.items(), key=lambda x: x[1]["total_r"])
    print(f"\n=== Рейтинг {len(symbols)} пар (по возрастанию total R) ===", flush=True)
    print(f"{'#':>3} {'Пара':14} {'R':>8} {'$':>8} {'Сделок':>7} {'Win%':>6}", flush=True)
    risk_usd = 5.0
    for i, (sym, d) in enumerate(rows, 1):
        r = float(d["total_r"])
        print(
            f"{i:3} {sym:14} {r:+8.2f} {r * risk_usd:+8.2f} {int(d['trades']):7} {d['win_rate_pct']:6.1f}",
            flush=True,
        )
    print(f"\nСумма R: {total_r:+.2f} | сделок: {len(all_trades)} | без данных: {len(skipped)}", flush=True)
    if skipped:
        print("Пропущены:", ", ".join(skipped[:10]), ("..." if len(skipped) > 10 else ""), flush=True)
    return payload


def run_symbol_ranking_background(*, top_n: int = DEFAULT_RANK_TOP_N) -> None:
    try:
        run_symbol_ranking_job(top_n=top_n, save_auto_filtered=False)
    except Exception as e:
        save_symbol_ranking_result(
            {
                "status": "error",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"[rank] error: {e}", flush=True)


def approve_live_symbols(symbols: list[str]) -> dict[str, Any]:
    """Утвердить выбранные пары для live-скана (symbol_ranking_filtered_r05_win50.json)."""
    latest = load_symbol_ranking_result()
    ranking = list(latest.get("ranking") or [])
    by_sym = {str(r["symbol"]): dict(r) for r in ranking}
    picked: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols, 1):
        row = by_sym.get(sym)
        if row is None:
            row = {
                "symbol": sym,
                "total_r": 0.0,
                "estimated_pnl_usdt": 0.0,
                "trades": 0,
                "wins": 0,
                "win_rate_pct": 0.0,
            }
        row = dict(row)
        row["rank"] = i
        picked.append(row)
    payload = {
        "status": "done",
        "source": str(SYMBOL_RANKING_PATH),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_manually": True,
        "criteria": {
            "manual_selection": True,
            "suggested_auto": {
                "total_r_gt": FILTER_TOTAL_R_GT,
                "win_rate_pct_gt": FILTER_WIN_RATE_PCT_GT,
                "min_trades": 1,
            },
        },
        "parent_symbols_count": latest.get("symbols_count"),
        "count": len(picked),
        "symbols": [str(r["symbol"]) for r in picked],
        "ranking": picked,
        "ranking_note": "sorted by user approval order",
    }
    save_filtered_ranking(payload)
    return payload


def main() -> int:
    run_symbol_ranking_job(save_auto_filtered=True)
    return 0


def load_symbol_ranking_result() -> dict:
    if not SYMBOL_RANKING_PATH.is_file():
        return {"status": "idle", "path": str(SYMBOL_RANKING_PATH)}
    try:
        return json.loads(SYMBOL_RANKING_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "path": str(SYMBOL_RANKING_PATH)}


def load_symbol_ranking_filtered() -> dict:
    if not SYMBOL_RANKING_FILTERED_PATH.is_file():
        return {"status": "idle", "path": str(SYMBOL_RANKING_FILTERED_PATH)}
    try:
        return json.loads(SYMBOL_RANKING_FILTERED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "path": str(SYMBOL_RANKING_FILTERED_PATH)}


def load_filtered_symbols() -> tuple[str, ...]:
    data = load_symbol_ranking_filtered()
    if data.get("status") != "done":
        return ()
    return tuple(str(s) for s in data.get("symbols") or [])


if __name__ == "__main__":
    raise SystemExit(main())
