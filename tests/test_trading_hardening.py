from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from forecast import auto_trader
from forecast.market_scanner import count_level_touches
from forecast.run_symbol_ranking import RankingJobConfig, build_filtered_ranking
from forecast.similarity import _standardize_train_query
from forecast.single_symbol_backtest import (
    MultiSymbolBacktestConfig,
    _apply_portfolio_concurrency,
    _load_btc_regime_df,
    _next_bar_fill,
)
from forecast.tf_backtest import _simulate_exit
from forecast.trend_rules import htf_bar_closed_as_of


class FakeFuturesExchange:
    def __init__(self) -> None:
        self.orders: list[tuple] = []
        self.positions: list[dict] = []

    def amount_to_precision(self, _symbol: str, amount: float) -> str:
        return str(amount)

    def price_to_precision(self, _symbol: str, price: float) -> str:
        return f"{price:.8f}"

    def create_order(self, *args):
        self.orders.append(args)
        return {"id": f"order-{len(self.orders)}", "filled": args[3], "average": 100.0}

    def fetch_positions(self, *_args):
        return self.positions


class FuturesSafetyTests(unittest.TestCase):
    def test_tp_failure_marks_bracket_as_failed_but_keeps_stop_reference(self) -> None:
        ex = FakeFuturesExchange()

        def create_order(*args):
            if args[1] == "TAKE_PROFIT_MARKET":
                raise RuntimeError("tp rejected")
            return {"id": "stop-1"}

        ex.create_order = create_order  # type: ignore[method-assign]
        result = auto_trader._place_futures_brackets(
            ex, "BTC/USDT:USDT", side="long", amount=0.1, stop=97.0, tp=106.0
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["protection_live"])
        self.assertEqual(result["stop_order_id"], "stop-1")

    def test_bracket_failure_attempts_close_then_places_three_pct_stop(self) -> None:
        ex = FakeFuturesExchange()
        cfg = auto_trader.AutoTradeConfig(enabled=True, dry_run=False)
        setup = {"entry": 100.0, "stop": 97.0, "target_1": 103.0, "target_2": 106.0}
        with (
            patch.object(auto_trader, "_futures_symbol", return_value="BTC/USDT:USDT"),
            patch.object(auto_trader, "_market_limits", return_value=(5.0, 0.001)),
            patch.object(auto_trader, "_estimate_entry_price", return_value=100.0),
            patch.object(auto_trader, "_configure_symbol"),
            patch.object(
                auto_trader,
                "_place_futures_brackets",
                return_value={"ok": False, "reason": "STOP_FAILED"},
            ),
            patch.object(
                auto_trader,
                "_emergency_close_futures",
                return_value={"ok": False, "reason": "close rejected"},
            ) as emergency_close,
        ):
            result = auto_trader.execute_futures_trade(
                ex,
                symbol="BTC/USDT",
                side="long",
                setup=setup,
                notional_usdt=20.0,
                cfg=cfg,
            )
        emergency_close.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertTrue(result["residual_position"])
        self.assertTrue(result["emergency_stop"]["ok"])
        self.assertAlmostEqual(result["emergency_stop"]["stop"], 97.0)

    def test_unknown_exchange_position_is_imported_and_protected(self) -> None:
        ex = FakeFuturesExchange()
        ex.positions = [
            {
                "symbol": "ETH/USDT:USDT",
                "contracts": 2.0,
                "side": "long",
                "entryPrice": 100.0,
                "markPrice": 101.0,
            }
        ]
        cfg = auto_trader.AutoTradeConfig(enabled=True, dry_run=False)
        state = auto_trader._sync_futures_positions(ex, {"open_positions": []}, cfg)
        self.assertEqual(len(state["open_positions"]), 1)
        self.assertTrue(state["open_positions"][0]["needs_protection"])

        state = auto_trader._protect_orphan_futures(ex, state, cfg)
        row = state["open_positions"][0]
        self.assertFalse(row["needs_protection"])
        self.assertAlmostEqual(row["stop"], 97.0)
        self.assertTrue(row["stop_order_id"])


class FreshSignalTests(unittest.TestCase):
    def test_cache_without_timestamp_fails_closed(self) -> None:
        cfg = auto_trader.AutoTradeConfig(enabled=True, dry_run=True)
        state = {"open_positions": []}
        identity = lambda _ex, value, _cfg: value
        with (
            patch.object(auto_trader, "load_auto_trade_config", return_value=cfg),
            patch.object(auto_trader, "load_trade_state", return_value=state),
            patch.object(auto_trader, "exchange_for_config", return_value=object()),
            patch.object(auto_trader, "_reconcile_spot_into_state", side_effect=identity),
            patch.object(auto_trader, "_sync_open_positions", side_effect=identity),
            patch.object(auto_trader, "_protect_orphan_futures", side_effect=identity),
            patch.object(auto_trader, "ensure_spot_exit_orders", side_effect=identity),
            patch.object(auto_trader, "_apply_auto_closes", return_value={}),
            patch.object(auto_trader, "save_trade_state"),
            patch.object(auto_trader, "_log_trade_attempt"),
            patch.object(auto_trader, "load_scan_result", return_value={"report": {"top_setups": []}}),
        ):
            result = auto_trader.maybe_run_auto_trade(None)
        self.assertEqual(result["reason"], "STALE_SIGNAL:NO_TIMESTAMP")

    def test_old_cache_age_is_detected(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        age = auto_trader._scan_cache_age_sec({"updated_at": old.isoformat()})
        self.assertIsNotNone(age)
        self.assertGreater(age, 10_000)

    def test_mark_deviation_and_rr_are_rechecked(self) -> None:
        exchange = Mock()
        exchange.fetch_ticker.return_value = {"last": 101.0}
        cfg = auto_trader.AutoTradeConfig(max_entry_deviation_pct=0.02, min_risk_reward=1.5)
        setup = {
            "direction": "Long",
            "entry": 100.0,
            "stop": 99.0,
            "target_1": 102.0,
            "target_2": 102.0,
        }
        ok, reason = auto_trader._entry_deviation_ok(exchange, "BTC/USDT", setup, cfg)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("STALE_RR"))


class BacktestIntegrityTests(unittest.TestCase):
    def test_htf_bar_must_be_fully_closed(self) -> None:
        open_time = pd.Timestamp("2026-01-01T00:00:00Z")
        self.assertFalse(
            htf_bar_closed_as_of(open_time, "4h", pd.Timestamp("2026-01-01T03:59:59Z"))
        )
        self.assertTrue(
            htf_bar_closed_as_of(open_time, "4h", pd.Timestamp("2026-01-01T04:00:00Z"))
        )

    def test_btc_regime_uses_only_closed_four_hour_bars(self) -> None:
        from forecast import trend_scanner

        idx = pd.to_datetime(["2026-01-01T00:00:00Z", "2026-01-01T04:00:00Z"])
        df = pd.DataFrame({"close": [100.0, 200.0], "ema_200": [100.0, 100.0]}, index=idx)
        with patch.object(trend_scanner, "_btc_regime_from_df", side_effect=lambda x: str(len(x))):
            result = trend_scanner.btc_regime_at(df, pd.Timestamp("2026-01-01T05:00:00Z"))
        self.assertEqual(result, "1")

    def test_btc_loader_requests_four_hour_timeframe(self) -> None:
        sentinel = pd.DataFrame({"close": [1.0]})
        with patch("forecast.single_symbol_backtest._fetch_df", return_value=sentinel) as fetch:
            got = _load_btc_regime_df(
                object(), enabled=True, window_start=None, window_end=None
            )
        self.assertIs(got, sentinel)
        self.assertEqual(fetch.call_args.args[2], "4h")

    def test_next_bar_open_is_entry_and_deviation_is_bounded(self) -> None:
        df = pd.DataFrame(
            {"open": [100.0, 101.0, 102.0], "high": [1, 1, 1], "low": [1, 1, 1], "close": [1, 1, 1]}
        )
        with patch.dict(os.environ, {"BT_MAX_ENTRY_DEV_PCT": "0.015"}):
            self.assertEqual(
                _next_bar_fill(df, 0, side="long", plan_entry=100.0, stop=95.0),
                (1, 101.0),
            )
            self.assertIsNone(
                _next_bar_fill(df, 1, side="long", plan_entry=100.0, stop=95.0)
            )

    def test_entry_bar_high_low_are_simulated(self) -> None:
        df = pd.DataFrame(
            {
                "open": [100.0, 100.0],
                "high": [106.0, 101.0],
                "low": [99.0, 99.0],
                "close": [100.0, 100.0],
            }
        )
        exit_px, reason, exit_i = _simulate_exit(
            df,
            0,
            side="long",
            entry=100.0,
            stop=95.0,
            tp=105.0,
            max_bars=2,
            include_entry_bar=True,
        )
        self.assertEqual((exit_px, reason, exit_i), (105.0, "tp", 0))

    def test_portfolio_cap_is_global_and_chronological(self) -> None:
        trades = [
            {"symbol": "B", "entry_time": "2026-01-01 02:00+00:00", "exit_time": "2026-01-01 05:00+00:00"},
            {"symbol": "A", "entry_time": "2026-01-01 01:00+00:00", "exit_time": "2026-01-01 04:00+00:00"},
            {"symbol": "C", "entry_time": "2026-01-01 03:00+00:00", "exit_time": "2026-01-01 06:00+00:00"},
        ]
        kept, skipped = _apply_portfolio_concurrency(trades, max_open_positions=2)
        self.assertEqual([t["symbol"] for t in kept], ["A", "B"])
        self.assertEqual(skipped, 1)
        self.assertFalse(MultiSymbolBacktestConfig().allow_stage1_relax)
        self.assertFalse(RankingJobConfig.__dataclass_fields__["allow_stage1_relax"].default)


class StatisticsIntegrityTests(unittest.TestCase):
    def test_filtered_ranking_requires_three_trades_and_warns_about_bias(self) -> None:
        ranking = [
            {"symbol": "A/USDT", "total_r": 2.0, "win_rate_pct": 60.0, "trades": 2},
            {"symbol": "B/USDT", "total_r": 1.0, "win_rate_pct": 55.0, "trades": 3},
        ]
        with patch.dict(os.environ, {"RANK_FILTER_MIN_TRADES": "3"}):
            result = build_filtered_ranking(ranking)
        self.assertEqual(result["symbols"], ["B/USDT"])
        self.assertIn("in-sample bias", result["selection_bias_warning"])

    def test_level_touches_use_atr_and_exclude_current_bar(self) -> None:
        df = pd.DataFrame(
            {
                "high": [101.0, 103.0, 104.0, 101.0],
                "low": [99.7, 102.0, 103.0, 100.0],
                "atr_14": [1.0, 1.0, 1.0, 1.0],
            }
        )
        self.assertEqual(count_level_touches(df, 100.0, kind="support"), 1)

    def test_zscore_statistics_come_only_from_train(self) -> None:
        train = np.array([[0.0], [2.0]])
        query = np.array([[100.0]])
        train_z, query_z = _standardize_train_query(train, query)
        np.testing.assert_allclose(train_z[:, 0], [-1.0, 1.0])
        np.testing.assert_allclose(query_z[:, 0], [99.0])


if __name__ == "__main__":
    unittest.main()
