"""Rank symbols by total R (per-symbol backtest, same trend params as multi_bt)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import ccxt

from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .single_symbol_backtest import _aggregate_by_symbol, backtest_single_symbol
from .auto_trader import load_auto_trade_config
from .tf_backtest import BARS_BY_TF, DEFAULT_SYMBOLS, _fetch_df, fetch_top_usdt_symbols
from .trend_rules import DEFAULT_TREND_PARAMS, TrendPullbackParams

SYMBOL_RANKING_PATH = PROCESSED_DATA_DIR / "symbol_ranking_latest.json"
SYMBOL_RANKING_FILTERED_PATH = PROCESSED_DATA_DIR / "symbol_ranking_filtered_r05_win50.json"
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


def main() -> int:
    load_project_env(force=True)
    n = int(os.environ.get("MULTI_BT_TOP_N", "200"))
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

    auto_cfg = load_auto_trade_config()
    auto_cfg.min_probability_pct = 50.0
    bars = BARS_BY_TF.get(tf, 1000)

    print(
        f"[rank] {len(symbols)} pairs, {tf}, TP={trend_params.tp_target_pct * 100:.0f}%, "
        f"rel_vol>={trend_params.min_rel_volume}, up to {per_sym} trades/symbol...",
        flush=True,
    )

    all_trades: list[dict] = []
    skipped: list[str] = []
    for i, symbol in enumerate(symbols, 1):
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
            extra = backtest_single_symbol(
                df,
                symbol=symbol,
                timeframe=tf,
                auto_cfg=auto_cfg,
                stage1_min=12.0,
                target_trades=per_sym,
                trend_params=trend_params,
            )
            sym_trades = extra
        all_trades.extend(sym_trades)
        r = sum(float(t["r_multiple"]) for t in sym_trades)
        print(f"[rank] {i}/{len(symbols)} {symbol}: {len(sym_trades)} trades, R={r:.2f}", flush=True)

    by = _aggregate_by_symbol(all_trades)
    for sym in symbols:
        if sym not in by:
            by[sym] = {"trades": 0, "wins": 0, "win_rate_pct": 0.0, "total_r": 0.0}

    rows = sorted(by.items(), key=lambda x: x[1]["total_r"])
    total_r = sum(float(v["total_r"]) for v in by.values())
    risk_usd = 5.0

    ranking = [
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
    payload = {
        "status": "done",
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
    }
    ensure_directories()
    SYMBOL_RANKING_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[rank] сохранено: {SYMBOL_RANKING_PATH}", flush=True)

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

    print(f"\n=== Рейтинг {len(symbols)} пар (по возрастанию total R) ===", flush=True)
    print(f"{'#':>3} {'Пара':14} {'R':>8} {'$':>8} {'Сделок':>7} {'Win%':>6}", flush=True)
    for i, (sym, d) in enumerate(rows, 1):
        r = float(d["total_r"])
        print(
            f"{i:3} {sym:14} {r:+8.2f} {r * risk_usd:+8.2f} {int(d['trades']):7} {d['win_rate_pct']:6.1f}",
            flush=True,
        )
    print(f"\nСумма R: {total_r:+.2f} | сделок: {len(all_trades)} | без данных: {len(skipped)}", flush=True)
    if skipped:
        print("Пропущены:", ", ".join(skipped[:10]), ("..." if len(skipped) > 10 else ""), flush=True)
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
