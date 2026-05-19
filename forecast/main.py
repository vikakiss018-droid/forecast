from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import yaml

import json

from .backtest import BacktestConfig, run_enriched_gate_backtest, run_simple_backtest, run_strategy_abc_comparison
from .backtest_analytics import post_trade_analytics
from .ev_calibration import fit_ev_calibration_from_enriched, save_ev_calibration
from .data_loader import download_ohlcv_to_csv, load_ohlcv_from_csv
from .indicators import add_basic_indicators
from .features import SIMILARITY_FEATURE_COLS, add_basic_features
from .market_scanner import ScanConfig, scan_market_top_setups
from .paths import RAW_DATA_DIR
from .similarity import SimilarityConfig, forecast_direction
from .trade_gate import TradeGateConfig, trade_gate_config_from_mapping


@dataclass
class AppConfig:
    exchange: str
    symbol: str
    timeframe: str
    limit: int
    use_futures: bool
    similarity: SimilarityConfig
    backtest: BacktestConfig
    trade_gate: TradeGateConfig
    liquidations: dict | None = None
    liquidity: dict | None = None


def load_config(path: str | Path) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    similarity_cfg = SimilarityConfig(**cfg["similarity"])
    backtest_cfg = BacktestConfig(**cfg["backtest"])
    trade_gate_cfg = trade_gate_config_from_mapping(cfg.get("trade_gate"))
    return AppConfig(
        exchange=cfg.get("exchange", "binance"),
        symbol=cfg["symbol"],
        timeframe=cfg.get("timeframe", "1h"),
        limit=cfg.get("limit", 1000),
        use_futures=cfg.get("use_futures", False),
        similarity=similarity_cfg,
        backtest=backtest_cfg,
        trade_gate=trade_gate_cfg,
        liquidations=cfg.get("liquidations"),
        liquidity=cfg.get("liquidity"),
    )


def run_pipeline(
    config_path: str = "configs/config.yaml",
    *,
    run_analytics: bool = False,
    strategy_compare: bool = False,
):
    app_cfg = load_config(config_path)

    # 1–2. Download and save candles
    print("Step 1/8: downloading candles from Binance...")
    csv_path = download_ohlcv_to_csv(
        symbol=app_cfg.symbol,
        timeframe=app_cfg.timeframe,
        limit=app_cfg.limit,
        use_futures=app_cfg.use_futures,
    )
    print(f"Saved raw data to: {csv_path}")

    # 3. Load into pandas
    print("Step 2/8: loading data into pandas...")
    df = load_ohlcv_from_csv(csv_path)
    print(f"Loaded {len(df)} bars.")

    # 4. Indicators
    print("Step 3/8: calculating indicators...")
    df = add_basic_indicators(df)

    # 5. Features
    print("Step 4/8: building features...")
    df = add_basic_features(df)

    feature_cols = list(SIMILARITY_FEATURE_COLS)

    # 6–7. Similarity-based forecast
    print("Step 5/8: computing similarity-based forecast...")
    prob_up, prob_down = forecast_direction(df, feature_cols, app_cfg.similarity)
    print(f"Symbol: {app_cfg.symbol}, timeframe: {app_cfg.timeframe}")
    print(f"Prob up: {prob_up:.3f}, prob down: {prob_down:.3f}")

    # 8. Simple backtest
    print("Step 6–8: running simple backtest...")
    bt_df = run_simple_backtest(df, feature_cols, app_cfg.similarity, app_cfg.backtest)
    if not bt_df.empty:
        print(f"Backtest samples: {len(bt_df)}")
        print(f"Final equity: {bt_df['equity'].iloc[-1]:.3f}")
    else:
        print("Backtest: not enough data.")

    tg_cfg = app_cfg.trade_gate
    step_bt = max(1, min(5, len(df) // 800))

    # Run A/B/C before analytics + calibration save when both are requested, so the
    # comparison uses the same on-disk curve as the enriched run that produced the
    # printed analytics JSON — not a curve fit from that run and then reused mid-session.
    if strategy_compare:
        print("Strategy A/B/C comparison (enriched gate, same walk-forward)...")
        abc = run_strategy_abc_comparison(
            df,
            feature_cols,
            app_cfg.similarity,
            app_cfg.backtest,
            tg_cfg,
            app_cfg.timeframe,
            step=step_bt,
        )
        print(json.dumps(abc, indent=2, default=str))

    if run_analytics:
        print("Step 9/9: enriched gate backtest + post-trade analytics (step tuned for speed)...")
        enr = run_enriched_gate_backtest(
            df,
            feature_cols,
            app_cfg.similarity,
            app_cfg.backtest,
            tg_cfg,
            app_cfg.timeframe,
            step=step_bt,
        )
        rep = post_trade_analytics(
            enr,
            min_trades_per_day_target=app_cfg.backtest.target_min_trades_per_day,
        )
        print(json.dumps(rep, indent=2, default=str))
        curve = fit_ev_calibration_from_enriched(enr)
        if curve:
            pth = save_ev_calibration(curve, tg_cfg.ev_calibration_json_path)
            print(f"Saved EV calibration ({len(curve)} buckets) to {pth}")
            if len(curve) < 2:
                print(
                    "Note: only one EV bucket had trades — calibration is a single point; "
                    "Spearman/monotonicity need multiple buckets or more data."
                )

    return df, bt_df, prob_up, prob_down


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Forecast app: similarity-based directional forecast.")
    parser.add_argument(
        "--config",
        type=str,
        default="configs/config.yaml",
        help="Path to YAML config.",
    )
    parser.add_argument(
        "--analytics",
        action="store_true",
        help="After pipeline, run gated walk-forward backtest and print analytics JSON.",
    )
    parser.add_argument(
        "--strategy-compare",
        action="store_true",
        help=(
            "Run gate modes A vs B vs C and print PF / max_dd / expectancy. "
            "If combined with --analytics, runs first so A/B/C use the pre-existing calibration file."
        ),
    )
    parser.add_argument(
        "--optimize-trade-gate",
        action="store_true",
        help=(
            "Offline: grid-search trade_gate on data/raw CSV (no download); "
            "maximize PF with --optimize-min-trades floor. See optimize_trade_gate module."
        ),
    )
    parser.add_argument("--optimize-min-trades", type=int, default=15)
    parser.add_argument("--optimize-top", type=int, default=12)
    parser.add_argument("--optimize-csv", type=str, default=None)
    parser.add_argument("--optimize-coarse", action="store_true", help="Smaller search grid (faster).")
    parser.add_argument(
        "--market-scan",
        action="store_true",
        help="Run 4-stage market scan over Binance USDT spot pairs and print top setups.",
    )
    parser.add_argument("--scan-top", type=int, default=5, help="Top setups to return.")
    parser.add_argument("--scan-bars", type=int, default=320, help="Bars per symbol for scan.")
    parser.add_argument(
        "--scan-timeframe",
        type=str,
        default=None,
        help="Override scan timeframe (e.g. 1h, 4h, 1d).",
    )
    parser.add_argument(
        "--scan-stage1-min-score",
        type=float,
        default=20.0,
        help="Stage-1 minimum score to keep symbol as candidate.",
    )
    parser.add_argument(
        "--scan-max-symbols",
        type=int,
        default=None,
        help="Optional cap for number of symbols scanned (for fast tests).",
    )
    args = parser.parse_args()
    return args


def main() -> None:
    args = _parse_args()
    if getattr(args, "market_scan", False):
        app_cfg = load_config(args.config)
        rep = scan_market_top_setups(
            similarity_cfg=app_cfg.similarity,
            scan_cfg=ScanConfig(
                timeframe=args.scan_timeframe or app_cfg.timeframe,
                bars=int(args.scan_bars),
                top_n=int(args.scan_top),
                stage1_min_score=float(args.scan_stage1_min_score),
                max_symbols=args.scan_max_symbols,
            ),
        )
        print(json.dumps(rep, indent=2, default=str))
        return

    if getattr(args, "optimize_trade_gate", False):
        from .optimize_trade_gate import run_from_cli

        argv = ["--config", args.config, "--min-trades", str(args.optimize_min_trades), "--top", str(args.optimize_top)]
        if args.optimize_csv:
            argv += ["--csv", args.optimize_csv]
        if getattr(args, "optimize_coarse", False):
            argv.append("--coarse")
        raise SystemExit(run_from_cli(argv))

    run_pipeline(
        args.config,
        run_analytics=args.analytics,
        strategy_compare=args.strategy_compare,
    )


if __name__ == "__main__":
    main()

