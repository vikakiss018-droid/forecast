from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

from .liquidity_model import liquidity_zone_and_volume
from .signal_combiner import (
    combine_probabilities,
    compute_liquidity_distance_score,
    compute_volume_scores,
    detect_regime,
    detect_trend_direction,
    ui_volatility_regime_last,
)
from .similarity import SimilarityConfig, forecast_direction, forecast_neighbor_stats
from .backtest_analytics import effective_neighbors_bucket_label, ev_bucket_label, post_trade_analytics
from .ev_calibration import load_ev_calibration
from .trade_gate import (
    GateMode,
    MAX_DAILY_TRADES,
    SAME_SIDE_REENTRY_BLOCKED,
    TradeGateConfig,
    TradeGateResult,
    TRADING_COOLDOWN,
    evaluate_trade_gate,
)


@dataclass
class BacktestConfig:
    threshold_long: float = 0.55
    threshold_short: float = 0.45
    transaction_cost_bp: float = 2.0  # basis points per trade
    # If set, post_trade_analytics reports mean/fraction vs this target (not enforced in the gate).
    target_min_trades_per_day: int | None = None


def run_simple_backtest(
    df: pd.DataFrame,
    feature_cols: list[str],
    sim_cfg: SimilarityConfig,
    bt_cfg: BacktestConfig,
    step: int = 1,
) -> pd.DataFrame:
    """Very simple walk-forward backtest."""
    equity = 1.0
    records = []

    start = sim_cfg.window_bars + sim_cfg.forecast_horizon_bars
    end = len(df)

    for i in tqdm(
        range(start, end, step),
        desc="Backtest",
        total=max((end - start) // step, 0),
    ):
        sub = df.iloc[: i + 1].copy()
        prob_up, prob_down = forecast_direction(sub, feature_cols, sim_cfg)

        position = 0  # 1 long, -1 short, 0 flat
        if prob_up >= bt_cfg.threshold_long:
            position = 1
        elif prob_down >= (1 - bt_cfg.threshold_short):
            position = -1

        if position == 0:
            records.append(
                {
                    "index": sub.index[-1],
                    "equity": equity,
                    "position": position,
                    "prob_up": prob_up,
                    "prob_down": prob_down,
                    "ret": 0.0,
                }
            )
            continue

        # Next bar return
        if i + 1 >= len(df):
            break
        price_now = df["close"].iloc[i]
        price_next = df["close"].iloc[i + 1]
        bar_ret = price_next / price_now - 1.0

        trade_cost = bt_cfg.transaction_cost_bp / 10000.0
        net_ret = position * bar_ret - trade_cost

        equity *= 1.0 + net_ret
        records.append(
            {
                "index": df.index[i + 1],
                "equity": equity,
                "position": position,
                "prob_up": prob_up,
                "prob_down": prob_down,
                "ret": net_ret,
            }
        )

    return pd.DataFrame.from_records(records).set_index("index")


def _calendar_date_key(ts) -> object:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        return t.tz_convert("UTC").date()
    return t.date()


def run_enriched_gate_backtest(
    df: pd.DataFrame,
    feature_cols: list[str],
    sim_cfg: SimilarityConfig,
    bt_cfg: BacktestConfig,
    tg_cfg: TradeGateConfig,
    timeframe: str,
    step: int = 1,
    gate_mode: GateMode = GateMode.C,
    ev_calibration_curve: dict[str, float] | None = None,
    quiet: bool = False,
) -> pd.DataFrame:
    """
    Walk-forward bars with trade gate (historical liquidity only), EV/skew from kNN,
    plus forward returns for post_trade_analytics.
    """
    equity = 1.0
    records = []
    trade_cost = bt_cfg.transaction_cost_bp / 10000.0

    start = sim_cfg.window_bars + sim_cfg.forecast_horizon_bars
    end = len(df)

    curve_pre: dict[str, float] | None = ev_calibration_curve
    if curve_pre is None and tg_cfg.use_auto_ev_calibration and gate_mode != GateMode.A:
        curve_pre = load_ev_calibration(tg_cfg.ev_calibration_json_path)
    elif curve_pre is None:
        curve_pre = {}

    _rng = range(start, end, step)
    _total = max((end - start) // step, 0)
    _iter = _rng if quiet else tqdm(_rng, desc="Enriched gate BT", total=_total)
    prev_position = 0
    cooldown_until_step = -1
    block_long_until_step = -1
    block_short_until_step = -1
    trades_calendar_key_count: dict = {}

    def _trade_gate_result_blocked(
        base: TradeGateResult, code: str, msg: str
    ) -> TradeGateResult:
        return TradeGateResult(
            direction="No trade",
            confidence=base.confidence,
            edge=base.edge,
            reasons=list(base.reasons) + [msg],
            reason_code=code,
            ev=base.ev,
            ev_adj=base.ev_adj,
            skew=base.skew,
            edge_threshold_used=base.edge_threshold_used,
            size_mult=0.0,
            gate_mode=base.gate_mode,
        )

    for step_ix, i in enumerate(_iter):
        sub = df.iloc[: i + 1].copy()
        last_price = float(sub["close"].iloc[-1])

        liq_low, liq_high, _volz = liquidity_zone_and_volume(sub, timeframe)
        st = forecast_neighbor_stats(sub, feature_cols, sim_cfg)
        pu_dir = st.directional_prob_up()
        pd_dir = 1.0 - pu_dir
        regime = detect_regime(sub)
        trend_direction = detect_trend_direction(sub, drift=tg_cfg.trend_ema_drift)

        vol_last = float(sub["volume"].iloc[-1]) if "volume" in sub.columns else float("nan")
        vol_ma = (
            float(sub["volume_ema_20"].iloc[-1])
            if "volume_ema_20" in sub.columns
            else float("nan")
        )
        rsi_b = float(sub["rsi_14"].iloc[-1]) if "rsi_14" in sub.columns else float("nan")
        atr_abs = float(sub["atr_14"].iloc[-1]) if "atr_14" in sub.columns else float("nan")
        em20 = float(sub["ema_20"].iloc[-1]) if "ema_20" in sub.columns else float("nan")
        em50 = float(sub["ema_50"].iloc[-1]) if "ema_50" in sub.columns else float("nan")
        S_liq_u, S_liq_d, S_dist = compute_liquidity_distance_score(last_price, liq_low, liq_high)
        S_vol_u, S_vol_d = compute_volume_scores(sub)
        comb_u, comb_d = combine_probabilities(
            pu_dir, pd_dir, S_liq_u, S_liq_d, S_dist, S_vol_u, S_vol_d, regime
        )
        vol_ui = ui_volatility_regime_last(sub)
        natr = float(sub["natr_14"].iloc[-1]) if "natr_14" in sub.columns else 0.0

        evb = ev_bucket_label(st.ev)
        knn_r_gate = str(sub["knn_regime"].iloc[-1]) if "knn_regime" in sub.columns else regime
        eff_n = float(st.effective_neighbors)
        eff_b = effective_neighbors_bucket_label(eff_n)
        tg = evaluate_trade_gate(
            comb_up_1h=comb_u,
            comb_down_1h=comb_d,
            low_ret_1h=st.low_ret,
            high_ret_1h=st.high_ret,
            S_distance=S_dist,
            vol_regime=vol_ui,
            natr_14=natr,
            ev=st.ev,
            skew=st.skew,
            knn_regime=knn_r_gate,
            ev_bucket=evb,
            effective_neighbors=eff_n,
            trend_direction=trend_direction,
            last_price=last_price,
            support_level=float(liq_low),
            resistance_level=float(liq_high),
            volume_last=vol_last,
            volume_sma20=vol_ma,
            rsi_bar=rsi_b,
            close_bar=last_price,
            ema_20_bar=em20,
            ema_50_bar=em50,
            atr_14_abs=atr_abs,
            gate_mode=gate_mode,
            ev_calibration_curve=curve_pre,
            cfg=tg_cfg,
        )

        raw_pos = 1 if tg.direction == "Long" else (-1 if tg.direction == "Short" else 0)

        opening_exposure = raw_pos != 0 and raw_pos != prev_position
        if opening_exposure:
            dk = (
                _calendar_date_key(df.index[i])
                if tg_cfg.max_trades_per_calendar_day is not None
                else None
            )
            daily_full = dk is not None and trades_calendar_key_count.get(dk, 0) >= int(
                tg_cfg.max_trades_per_calendar_day
            )
            veto = False
            if cooldown_until_step >= 0 and step_ix < cooldown_until_step:
                veto = True
                tg = _trade_gate_result_blocked(
                    tg,
                    TRADING_COOLDOWN,
                    f"trading cooldown until step>={cooldown_until_step}",
                )
            elif raw_pos == 1 and block_long_until_step >= 0 and step_ix < block_long_until_step:
                veto = True
                tg = _trade_gate_result_blocked(
                    tg,
                    SAME_SIDE_REENTRY_BLOCKED,
                    f"same-side long blocked until step>={block_long_until_step}",
                )
            elif raw_pos == -1 and block_short_until_step >= 0 and step_ix < block_short_until_step:
                veto = True
                tg = _trade_gate_result_blocked(
                    tg,
                    SAME_SIDE_REENTRY_BLOCKED,
                    f"same-side short blocked until step>={block_short_until_step}",
                )
            elif daily_full:
                veto = True
                tg = _trade_gate_result_blocked(
                    tg,
                    MAX_DAILY_TRADES,
                    f"daily trades limit ({tg_cfg.max_trades_per_calendar_day}) reached",
                )

            if veto:
                if prev_position != 0 and raw_pos != 0 and raw_pos != prev_position:
                    pos = 0
                elif prev_position == 0:
                    pos = 0
                else:
                    pos = prev_position
            else:
                pos = raw_pos
        else:
            pos = raw_pos

        knn_r = knn_r_gate

        closed_prev = prev_position != 0 and (
            pos == 0 or (pos != 0 and pos != prev_position)
        )
        if closed_prev:
            cd = int(tg_cfg.cooldown_bars_after_trade)
            sb = int(tg_cfg.block_same_direction_reentry_bars)
            cooldown_until_step = step_ix + 1 + cd if cd > 0 else -1
            if sb > 0:
                if prev_position == 1:
                    block_long_until_step = step_ix + 1 + sb
                elif prev_position == -1:
                    block_short_until_step = step_ix + 1 + sb

        opened_from_flat = prev_position == 0 and pos != 0
        if opened_from_flat and tg_cfg.max_trades_per_calendar_day is not None:
            dk_inc = _calendar_date_key(df.index[i])
            trades_calendar_key_count[dk_inc] = trades_calendar_key_count.get(dk_inc, 0) + 1

        prev_position = pos

        fwd_ret = float("nan")
        net_ret = 0.0
        prob_edge_w = 0.0
        if i + 1 < len(df):
            price_now = float(df["close"].iloc[i])
            price_next = float(df["close"].iloc[i + 1])
            fwd_ret = float(price_next / price_now - 1.0)
            if pos != 0:
                prob_edge_w = 1.0
                if tg_cfg.use_prob_edge_sizing:
                    prob_edge_w = abs(float(pu_dir - pd_dir))
                    prob_edge_w = float(np.clip(prob_edge_w, 0.0, 1.0))
                    pw = float(tg_cfg.prob_edge_power)
                    if pw > 0.0 and abs(pw - 1.0) > 1e-12:
                        prob_edge_w = float(np.clip(prob_edge_w**pw, 0.0, 1.0))
                    if tg_cfg.prob_edge_min_size > 0:
                        prob_edge_w = max(prob_edge_w, float(tg_cfg.prob_edge_min_size))
                net_ret = prob_edge_w * (float(pos) * fwd_ret - trade_cost)
                equity *= 1.0 + net_ret

        records.append(
            {
                "index": df.index[i],
                "equity": equity,
                "position": pos,
                "direction": tg.direction,
                "reason_code": tg.reason_code,
                "reasons": "; ".join(tg.reasons),
                "comb_up": comb_u,
                "comb_down": comb_d,
                "edge": float(comb_u - comb_d),
                "edge_threshold_used": tg.edge_threshold_used,
                "ev": st.ev,
                "ev_adj": tg.ev_adj,
                "skew": st.skew,
                "ev_bucket": evb,
                "knn_regime": knn_r,
                "vol_regime": vol_ui,
                "size_mult": tg.size_mult,
                "prob_edge_weight": prob_edge_w,
                "natr_14": natr,
                "effective_neighbors": eff_n,
                "eff_neighbors_bucket": eff_b,
                "fwd_ret": fwd_ret,
                "net_ret": net_ret,
            }
        )

    return pd.DataFrame.from_records(records).set_index("index")


def run_strategy_abc_comparison(
    df: pd.DataFrame,
    feature_cols: list[str],
    sim_cfg: SimilarityConfig,
    bt_cfg: BacktestConfig,
    tg_cfg: TradeGateConfig,
    timeframe: str,
    step: int = 1,
) -> dict[str, Any]:
    """
    Same walk-forward path, three gate modes: A (prob+edge), B (+EV), C (full).
    Returns compact metrics for profit_factor, max_drawdown, expectancy per mode.
    """
    curve_pre: dict[str, float] = {}
    if tg_cfg.use_auto_ev_calibration:
        curve_pre = load_ev_calibration(tg_cfg.ev_calibration_json_path)

    out: dict[str, Any] = {}
    for mode in (GateMode.A, GateMode.B, GateMode.C):
        enr = run_enriched_gate_backtest(
            df,
            feature_cols,
            sim_cfg,
            bt_cfg,
            tg_cfg,
            timeframe,
            step=step,
            gate_mode=mode,
            ev_calibration_curve=curve_pre if mode != GateMode.A else {},
        )
        rep = post_trade_analytics(enr)
        out[mode.value] = {
            "profit_factor": rep.get("profit_factor"),
            "max_drawdown": rep.get("max_drawdown"),
            "expectancy": rep.get("expectancy"),
            "n_trades": rep.get("n_trades"),
            "final_equity": rep.get("final_equity"),
            "win_rate": rep.get("win_rate"),
        }
    return out

