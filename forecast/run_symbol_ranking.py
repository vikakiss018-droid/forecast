"""Rank symbols by total R (per-symbol combined trend+range backtest, as live scan)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from dataclasses import dataclass

import ccxt

from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .single_symbol_backtest import _aggregate_by_symbol, backtest_combined_single_symbol
from .auto_trader import load_auto_trade_config, apply_scan_auto_filters
from .tf_backtest import BARS_BY_TF, DEFAULT_SYMBOLS, _fetch_df, fetch_top_usdt_symbols
from pathlib import Path
from typing import Any, Callable

from .strategy_config import env_float, env_int, yaml_section
from .trend_rules import TrendPullbackParams

SYMBOL_RANKING_PATH = PROCESSED_DATA_DIR / "symbol_ranking_latest.json"
SYMBOL_RANKING_FILTERED_PATH = PROCESSED_DATA_DIR / "symbol_ranking_filtered_r05_win50.json"
DEFAULT_RANK_TOP_N = 400
FILTER_TOTAL_R_GT = 0.5
FILTER_WIN_RATE_PCT_GT = 50.0


@dataclass(frozen=True)
class RankingJobConfig:
    top_n: int
    timeframe: str
    bars: int
    stage1_min: float
    stage1_relax: float
    target_trades_per_symbol: int
    use_top_volume: bool
    long_only: bool
    allow_trend: bool
    allow_range: bool
    trend_params: TrendPullbackParams
    auto_cfg: Any

    def to_meta(self) -> dict[str, Any]:
        at = self.auto_cfg
        tp = self.trend_params
        return {
            "top_n": self.top_n,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "stage1_min": self.stage1_min,
            "stage1_relax": self.stage1_relax,
            "target_trades_per_symbol": self.target_trades_per_symbol,
            "use_top_volume": self.use_top_volume,
            "long_only": self.long_only,
            "allow_trend": self.allow_trend,
            "allow_range": self.allow_range,
            "min_rel_volume": tp.min_rel_volume,
            "min_atr_pct_trend": tp.min_atr_pct,
            "trend_lookback": tp.trend_lookback,
            "min_trend_move_pct": tp.min_trend_move_pct,
            "min_score": at.min_score,
            "min_probability_pct": at.min_probability_pct,
            "min_risk_reward": at.min_risk_reward,
            "min_atr_pct_auto": at.min_atr_pct,
            "rule": self.rule_text(),
        }

    def rule_text(self) -> str:
        modes = []
        if self.allow_trend:
            modes.append("trend")
        if self.allow_range:
            modes.append("range")
        mode_s = "+".join(modes) if modes else "none"
        return (
            f"combined {mode_s} {self.timeframe}; bars={self.bars}; "
            f"stage1>={self.stage1_min}; RR>={self.auto_cfg.min_risk_reward}; "
            f"rel_vol>={self.trend_params.min_rel_volume}"
            + ("; long only" if self.long_only else "")
        )


def ranking_config_from_env() -> RankingJobConfig:
    """Параметры теста пар: .env + config.yaml (как live-скан)."""
    from .trend_scanner import trend_params_from_yaml, trend_scan_config_from_env

    load_project_env(force=True)
    scan_cfg = trend_scan_config_from_env()
    trend_params = scan_cfg.trend_params or trend_params_from_yaml()
    ref = yaml_section("reference_backtest")
    scan_yaml = yaml_section("trend_scan")

    top_n = env_int(
        "RANK_TOP_N",
        env_int("MULTI_BT_TOP_N", DEFAULT_RANK_TOP_N, positive=True),
        positive=True,
    )
    per_sym = env_int("RANK_TARGET_PER_SYMBOL", 30, positive=True)
    use_top = os.environ.get("MULTI_BT_USE_TOP_VOLUME", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    stage1_relax = env_float(
        "RANK_STAGE1_RELAX_SCORE",
        float(ref.get("stage1_relax_score", scan_yaml.get("stage1_relax_score", 12))),
        positive=True,
    )
    tf = scan_cfg.timeframe
    bars = scan_cfg.bars or BARS_BY_TF.get(tf, 1000)

    at_yaml = yaml_section("auto_trade")
    auto_cfg = load_auto_trade_config(at_yaml)
    apply_scan_auto_filters(auto_cfg, scan_cfg)
    auto_cfg.allow_level_breakout = bool(at_yaml.get("allow_level_breakout", False))
    auto_cfg.allow_triangle = bool(at_yaml.get("allow_triangle", False))
    atr_env = os.environ.get("AUTO_TRADE_MIN_ATR_PCT", "").strip()
    if atr_env:
        auto_cfg.min_atr_pct = float(atr_env)
    else:
        auto_cfg.min_atr_pct = float(at_yaml.get("min_atr_pct", 0))

    return RankingJobConfig(
        top_n=top_n,
        timeframe=tf,
        bars=bars,
        stage1_min=float(scan_cfg.stage1_min_score),
        stage1_relax=stage1_relax,
        target_trades_per_symbol=per_sym,
        use_top_volume=use_top,
        long_only=scan_cfg.long_only,
        allow_trend=scan_cfg.allow_trend,
        allow_range=scan_cfg.allow_range,
        trend_params=trend_params,
        auto_cfg=auto_cfg,
    )


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



def run_symbol_ranking_job(
    *,
    top_n: int | None = None,
    save_auto_filtered: bool = False,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Backtest top-N USDT pairs; returns ranking payload (status=done)."""
    cfg = ranking_config_from_env()
    if top_n is not None:
        cfg = RankingJobConfig(
            top_n=int(top_n),
            timeframe=cfg.timeframe,
            bars=cfg.bars,
            stage1_min=cfg.stage1_min,
            stage1_relax=cfg.stage1_relax,
            target_trades_per_symbol=cfg.target_trades_per_symbol,
            use_top_volume=cfg.use_top_volume,
            long_only=cfg.long_only,
            allow_trend=cfg.allow_trend,
            allow_range=cfg.allow_range,
            trend_params=cfg.trend_params,
            auto_cfg=cfg.auto_cfg,
        )

    n = cfg.top_n
    tf = cfg.timeframe
    per_sym = cfg.target_trades_per_symbol
    trend_params = cfg.trend_params
    auto_cfg = cfg.auto_cfg
    bars = cfg.bars

    exchange = ccxt.binance({"enableRateLimit": True})
    if cfg.use_top_volume and n > len(DEFAULT_SYMBOLS):
        symbols = fetch_top_usdt_symbols(exchange, limit=n)
    else:
        symbols = DEFAULT_SYMBOLS[:n]

    test_meta = cfg.to_meta()
    started_at = datetime.now(timezone.utc).isoformat()

    running: dict[str, Any] = {
        "status": "running",
        "mode": "trend_plus_range",
        "test_config": test_meta,
        "long_only": cfg.long_only,
        "allow_trend": cfg.allow_trend,
        "allow_range": cfg.allow_range,
        "started_at": started_at,
        "symbols_count": len(symbols),
        "timeframe": tf,
        "bars": bars,
        "stage1_min": cfg.stage1_min,
        "stage1_relax": cfg.stage1_relax,
        "tp_target_pct": trend_params.tp_target_pct,
        "min_rel_volume": trend_params.min_rel_volume,
        "target_trades_per_symbol": per_sym,
        "progress": {"current": 0, "total": len(symbols), "symbol": None},
        "kind": "pair_test",
        "ranking": [],
        "ranking_note": "sorted by total_r ascending (worst first); see rank field",
        "rule": cfg.rule_text(),
    }
    save_symbol_ranking_result(running)

    print(
        f"[rank] {len(symbols)} pairs, {tf}, bars={bars}, {cfg.rule_text()}, "
        f"up to {per_sym} trades/symbol...",
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
        sym_trades = backtest_combined_single_symbol(
            df,
            symbol=symbol,
            timeframe=tf,
            auto_cfg=auto_cfg,
            stage1_min=cfg.stage1_min,
            target_trades=per_sym,
            trend_params=trend_params,
            long_only=cfg.long_only,
            allow_trend=cfg.allow_trend,
            allow_range=cfg.allow_range,
        )
        if not sym_trades and per_sym > 0:
            sym_trades = backtest_combined_single_symbol(
                df,
                symbol=symbol,
                timeframe=tf,
                auto_cfg=auto_cfg,
                stage1_min=cfg.stage1_relax,
                target_trades=per_sym,
                trend_params=trend_params,
                long_only=cfg.long_only,
                allow_trend=cfg.allow_trend,
                allow_range=cfg.allow_range,
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
        "mode": "trend_plus_range",
        "test_config": test_meta,
        "long_only": cfg.long_only,
        "allow_trend": cfg.allow_trend,
        "allow_range": cfg.allow_range,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "symbols_count": len(symbols),
        "timeframe": tf,
        "bars": bars,
        "stage1_min": cfg.stage1_min,
        "stage1_relax": cfg.stage1_relax,
        "tp_target_pct": trend_params.tp_target_pct,
        "min_rel_volume": trend_params.min_rel_volume,
        "target_trades_per_symbol": per_sym,
        "total_trades": len(all_trades),
        "total_r": round(total_r, 2),
        "skipped_symbols": skipped,
        "ranking": ranking,
        "ranking_note": "sorted by total_r ascending (worst first); see rank field",
        "rule": cfg.rule_text(),
        "progress": {"current": len(symbols), "total": len(symbols), "symbol": None},
        "kind": "pair_test",
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


def run_symbol_ranking_background() -> None:
    try:
        run_symbol_ranking_job(save_auto_filtered=False)
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
