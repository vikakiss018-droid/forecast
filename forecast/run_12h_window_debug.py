"""12h short-window debug run on filtered 50 pairs with candidate/trade accounting."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd

from forecast.run_symbol_ranking import load_filtered_symbols
from forecast.single_symbol_backtest import _build_candidate
from forecast.auto_trader import load_auto_trade_config, validate_setup
from forecast.market_scanner import _stage1_snapshot, _adjust_stage1_for_direction
from forecast.trend_rules import (
    TrendPullbackParams,
    build_trend_plan,
    detect_price_trend,
    _pullback_ready,
    _momentum_candle_confirms,
)
from forecast.tf_backtest import _fetch_df_date_window

OUT = Path(__file__).resolve().parent / "data/processed/backtest_basic_filtered50_last12h_short_window.json"


def main() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=12)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if end_ts.hour == 0 and end_ts.minute == 0 and end_ts.second == 0:
        end_ts = end_ts + pd.Timedelta(hours=23, minutes=59, seconds=59)

    symbols = load_filtered_symbols()
    params = TrendPullbackParams(
        require_pullback=False,
        require_htf_align=False,
        min_rel_volume=1.2,
        trend_lookback=60,
        min_trend_move_pct=0.008,
    )
    auto_cfg = load_auto_trade_config()
    auto_cfg.min_probability_pct = 50.0
    auto_cfg.min_score = 18.0
    auto_cfg.allow_level_breakout = False
    auto_cfg.allow_triangle = False

    ex = ccxt.binance({"enableRateLimit": True})

    total_raw = 0
    total_kept = 0
    per_symbol_raw: Counter[str, int] = Counter()
    per_symbol_kept: Counter[str, int] = Counter()
    kept_trades: list[dict] = []
    out_of_window_samples: list[dict] = []
    bars_scanned = 0
    rejections: Counter[str] = Counter()
    symbols_skipped_short_data = 0

    def _bump(reason: str) -> None:
        rejections[reason] += 1

    for sym in symbols:
        df = _fetch_df_date_window(ex, sym, "1h", start=start_ts, end=end_ts)
        if df is None or len(df) < 130:
            symbols_skipped_short_data += 1
            _bump("SKIP_SHORT_DATA")
            continue

        for i in range(72, len(df) - 48 - 1, 2):
            bars_scanned += 1
            sub = df.iloc[: i + 1]
            snap = _stage1_snapshot(sub)
            trend = detect_price_trend(sub, params)
            if trend == "range":
                _bump("TREND_RANGE")
                continue

            support = float(snap["support_level"])
            resistance = float(snap["resistance_level"])
            pb_ok, _ = _pullback_ready(sub, trend, support=support, resistance=resistance, params=params)
            if not pb_ok:
                _bump("PULLBACK_FAIL")
                continue
            if not _momentum_candle_confirms(sub, trend):
                _bump("MOMENTUM_FAIL")
                continue

            rel_vol = float(snap.get("context", {}).get("rel_volume", 0.0))
            if rel_vol < params.min_rel_volume:
                _bump("LOW_REL_VOLUME")
                continue

            stage1 = _adjust_stage1_for_direction(
                float(snap["stage1_score"]),
                direction="Long" if trend == "up" else "Short",
                rel_vol=rel_vol,
                candle_bullish=float(sub.iloc[-1]["close"]) > float(sub.iloc[-1]["open"]),
                close=float(sub.iloc[-1]["close"]),
                support=support,
                resistance=resistance,
                vol_up=0.0,
                vol_down=0.0,
            )
            if stage1 < 18:
                _bump("LOW_STAGE1")
                continue

            plan = build_trend_plan(sub, snap, params)
            if not plan:
                _bump("BUILD_TREND_PLAN_FAIL")
                continue

            cand = _build_candidate(symbol=sym, snap=snap, plan=plan, stage1=stage1, df=sub)
            ok, reason = validate_setup(cand, auto_cfg)
            if not ok:
                key = reason.split(":", 1)[0] if reason else "VALIDATE_FAIL"
                _bump(key)
                if key == "LOW_RR":
                    entry_time = pd.Timestamp(sub.index[-1])
                    if start_ts <= entry_time <= end_ts:
                        _bump("LOW_RR_WOULD_BE_IN_12H_WINDOW")
                    else:
                        _bump("LOW_RR_OUT_OF_12H_WINDOW")
                continue

            total_raw += 1
            per_symbol_raw[sym] += 1

            entry_time = pd.Timestamp(sub.index[-1])
            side = str(plan.get("direction", "")).strip().lower()
            if start_ts <= entry_time <= end_ts:
                total_kept += 1
                per_symbol_kept[sym] += 1
                kept_trades.append(
                    {
                        "symbol": sym,
                        "entry_time": str(entry_time),
                        "side": side,
                    }
                )
            else:
                _bump("OUT_OF_12H_WINDOW")
                out_of_window_samples.append(
                    {
                        "symbol": sym,
                        "entry_time": str(entry_time),
                        "side": side,
                    }
                )

    total_rejected = sum(rejections.values())
    payload = {
        "status": "done",
        "test_name": "debug_12h_filtered50_short_window",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start_ts.isoformat(),
        "window_end": end_ts.isoformat(),
        "symbols_count": len(symbols),
        "symbols": list(symbols),
        "summary": {
            "bars_scanned": bars_scanned,
            "symbols_skipped_short_data": symbols_skipped_short_data,
            "rejections_total": total_rejected,
            "rejections_by_reason": dict(rejections.most_common()),
            "raw_candidates_total": total_raw,
            "kept_in_window_total": total_kept,
            "out_of_window_count": len(out_of_window_samples),
            "trades_in_window": total_kept,
        },
        "by_symbol": {
            s: {
                "raw_candidates": per_symbol_raw.get(s, 0),
                "kept_in_window": per_symbol_kept.get(s, 0),
            }
            for s in symbols
        },
        "out_of_window_samples": out_of_window_samples[:50],
        "trades": kept_trades,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("saved:", OUT)
    print("window:", start_ts, "..", end_ts)
    print("bars_scanned:", bars_scanned)
    print("rejections_total:", total_rejected)
    for reason, n in rejections.most_common(15):
        print(f"  {reason}: {n}")
    print("raw_candidates:", total_raw)
    print("kept_in_window:", total_kept)
    print("out_of_window_samples:", len(out_of_window_samples))


if __name__ == "__main__":
    main()