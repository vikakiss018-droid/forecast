from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from .ev_calibration import load_ev_calibration

# Machine-readable codes for logging / backtest analytics
LOW_LIQUIDITY = "LOW_LIQUIDITY"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
NEGATIVE_EV = "NEGATIVE_EV"
LOW_EV_MAGNITUDE = "LOW_EV_MAGNITUDE"
LOW_FORECAST_EV = "LOW_FORECAST_EV"
EV_BUCKET_BLOCKED = "EV_BUCKET_BLOCKED"
EV_BUCKET_NOT_ALLOWED = "EV_BUCKET_NOT_ALLOWED"
LOW_EV_TO_VOL = "LOW_EV_TO_VOL"
LOW_SKEW = "LOW_SKEW"
REGIME_BLOCKED = "REGIME_BLOCKED"
LOW_EDGE = "LOW_EDGE"
LOW_EXPECTED_MOVE = "LOW_EXPECTED_MOVE"
HIGH_RISK = "HIGH_RISK"
TREND_MISMATCH = "TREND_MISMATCH"
SR_LEVEL_MISMATCH = "SR_LEVEL_MISMATCH"
LOW_RELATIVE_VOLUME = "LOW_RELATIVE_VOLUME"
FORECAST_EV_NOT_POSITIVE = "FORECAST_EV_NOT_POSITIVE"
RSI_BLOCK_LONG = "RSI_BLOCK_LONG"
RSI_BLOCK_SHORT = "RSI_BLOCK_SHORT"
EMA50_ATR_DISTANCE_BLOCK = "EMA50_ATR_DISTANCE_BLOCK"
WEAK_BREAKOUT_EDGE = "WEAK_BREAKOUT_EDGE"
STRICT_TREND_EMA_BLOCK = "STRICT_TREND_EMA_BLOCK"
TRADING_COOLDOWN = "TRADING_COOLDOWN"
SAME_SIDE_REENTRY_BLOCKED = "SAME_SIDE_REENTRY_BLOCKED"
MAX_DAILY_TRADES = "MAX_DAILY_TRADES"
SHORTS_DISABLED = "SHORTS_DISABLED"
OK_LONG = "OK_LONG"
OK_SHORT = "OK_SHORT"
AMBIGUOUS = "AMBIGUOUS"
NO_SIGNAL = "NO_SIGNAL"


class GateMode(str, Enum):
    """A: prob+edge only; B: + EV calibration; C: full filters (N_eff, skew, regime, EV/NATR)."""

    A = "A"
    B = "B"
    C = "C"


def position_size_mult_v2(
    ev_adj: float,
    ev_target: float,
    edge: float,
    edge_threshold: float,
) -> float:
    """Primary scale from calibrated EV; secondary from directional edge."""
    et = max(abs(float(edge)) / max(float(edge_threshold), 1e-9), 1e-9)
    evp = max(abs(float(ev_adj)) / max(float(ev_target), 1e-9), 1e-9)
    sm = max(0.25, min(1.0, evp))
    sm *= max(0.5, min(1.0, et))
    return float(sm)


def _ev_bucket_calibration_mult(
    ev_bucket: str,
    pairs: tuple[tuple[str, float], ...],
) -> float:
    for k, v in pairs:
        if k == ev_bucket:
            return float(v)
    return 1.0


@dataclass(frozen=True)
class TradeGateConfig:
    comb_prob_min: float = 0.58
    fee_one_way: float = 0.0004
    slippage: float = 0.0002
    safety_margin: float = 0.0002
    liq_hard_min: float = 0.12
    expected_move_vs_cost_mult: float = 2.0
    expected_move_vs_natr_mult: float = 1.25
    expected_move_vs_natr_mult_high_vol: float = 1.8
    edge_base_normal: float = 0.12
    edge_base_high_vol: float = 0.18
    edge_natr_k: float = 0.5
    ev_min: float = 0.0
    min_ev_trade: float = 0.0
    # |kNN ev| floor in return units (not ev_adj); optional alternative to min_ev_trade on calibrated ev_adj.
    min_forecast_ev_abs: float | None = None
    # If False, skip NEGATIVE_EV / min_ev_trade in modes B and C (edge + move gates remain).
    use_ev_gate: bool = True
    # If False: no EV-бакеты, no ev_gate/min_forecast_ev_abs/min_ev_vol_ratio; решение только direction + edge/comb/move/режим и т.д.; size_mult=1.0.
    use_ev_metrics_in_gate: bool = True
    ev_bucket_multipliers: tuple[tuple[str, float], ...] = ()  # fallback when no empirical curve
    blocked_knn_regimes: frozenset[str] = field(default_factory=frozenset)
    # kNN ev_bucket in this set → no trade (например слабый/инвертированный бакет на выбранном таймфрейме).
    blocked_ev_buckets: frozenset[str] = field(default_factory=frozenset)
    # If non-empty, only these ev_bucket labels may trade (checked before blocked_ev_buckets).
    allowed_ev_buckets: frozenset[str] = field(default_factory=frozenset)
    min_skew: float | None = None
    use_auto_ev_calibration: bool = True
    ev_calibration_json_path: str | None = None
    min_effective_neighbors_gate: float | None = 3.0  # mode C: Kish N_eff floor
    min_ev_vol_ratio: float | None = 0.05  # mode C: |forecast ev|/natr
    ev_target_for_size: float = 0.002
    # В enriched gate backtest: вес позиции = |prob_up - prob_down| по kNN (до combine); комиссии масштабируются тем же коэффициентом.
    use_prob_edge_sizing: bool = False
    # Нижняя граница веса [0..1], чтобы при близости к 50/50 не обнулять весь вклад произвольным шумом.
    prob_edge_min_size: float = 0.0
    # После клипа [0..1]: вес ** prob_edge_power. >1 сильнее ужимает слабые сигналы (например 2 → квадрат).
    prob_edge_power: float = 1.0
    # If True, only long in up-trend and short in down-trend.
    trade_with_trend_only: bool = False
    # If True, short entries are disabled at gate level.
    long_only: bool = False
    # If True, require longs close to support and shorts close to resistance.
    use_support_resistance_filter: bool = False
    # Max allowed distance to nearest S/R level: sr_level_tolerance_natr_mult * NATR.
    sr_level_tolerance_natr_mult: float = 1.0
    # --- Volume / momentum / structure (снижение частоты сделок) ---
    # volume >= mult * SMA(volume,20); None = выкл.
    min_volume_vs_sma20_mult: float | None = None
    # Raw kNN forecast EV (return scale) must be > 0 (дублирует часть ev_lt_0 bucket).
    require_forecast_ev_positive: bool = False
    # RSI 14: лонг запрещён если rsi >= cap; None = выкл.
    rsi_max_long: float | None = None
    # Шорт запрещён если rsi <= floor.
    rsi_min_short: float | None = None
    # |close - EMA50| <= mult * ATR14 (цена); None = выкл.
    max_price_ema50_atr_mult: float | None = None
    # Слабый пробой: |edge| должен превышать edge_th * ratio (для стороны входа).
    weak_breakout_edge_ratio: float | None = None
    # Доп. тренд: long только close>EMA50>EMA20, short close<EMA50<EMA20.
    strict_trend_ema_alignment: bool = False
    # Drift EMA20/EMA50 в detect_trend_direction; None = 0.001 по умолчанию в вызывающем коде.
    trend_ema_drift: float | None = None
    # --- Частота (обрабатывается в run_enriched_gate_backtest, поля для YAML/единообразия) ---
    cooldown_bars_after_trade: int = 0
    block_same_direction_reentry_bars: int = 0
    max_trades_per_calendar_day: int | None = None


def trade_gate_config_from_mapping(raw: dict[str, Any] | None) -> TradeGateConfig:
    """Build TradeGateConfig from a YAML ``trade_gate:`` mapping (unknown keys ignored)."""
    if not raw:
        return TradeGateConfig()
    names = {f.name for f in dataclasses.fields(TradeGateConfig)}
    kw: dict[str, Any] = {}
    for key, val in raw.items():
        if key not in names:
            continue
        if key == "blocked_knn_regimes" and val is not None:
            kw[key] = frozenset(str(x) for x in val)
        elif key == "blocked_ev_buckets" and val is not None:
            kw[key] = frozenset(str(x) for x in val)
        elif key == "allowed_ev_buckets" and val is not None:
            kw[key] = frozenset(str(x) for x in val)
        elif key == "ev_bucket_multipliers" and isinstance(val, list):
            kw[key] = tuple((str(a), float(b)) for a, b in val)
        else:
            kw[key] = val
    return TradeGateConfig(**kw)


def _total_cost_frac(cfg: TradeGateConfig) -> float:
    return 2.0 * cfg.fee_one_way + cfg.slippage + cfg.safety_margin


@dataclass
class TradeGateResult:
    direction: str
    confidence: float
    edge: float
    reasons: list[str]
    reason_code: str
    ev: float = 0.0
    ev_adj: float = 0.0
    skew: float = 0.0
    edge_threshold_used: float = 0.0
    size_mult: float = 0.0
    gate_mode: str = "C"


def _resolve_ev_adj(
    ev: float,
    ev_bucket: str,
    cfg: TradeGateConfig,
    curve: dict[str, float],
) -> tuple[float, str]:
    """Returns (ev_adj, note_suffix for logging)."""
    if ev_bucket in curve and np.isfinite(float(curve[ev_bucket])):
        return float(curve[ev_bucket]), "empirical_curve"
    mult = _ev_bucket_calibration_mult(ev_bucket, cfg.ev_bucket_multipliers)
    return float(ev * mult), f"raw×{mult:.3g}"


def evaluate_trade_gate(
    *,
    comb_up_1h: float,
    comb_down_1h: float,
    low_ret_1h: float,
    high_ret_1h: float,
    S_distance: float,
    vol_regime: str,
    natr_14: float,
    ev: float,
    skew: float,
    knn_regime: str = "range",
    ev_bucket: str = "ev_nan",
    effective_neighbors: float = 100.0,
    trend_direction: str = "range",
    last_price: float | None = None,
    support_level: float | None = None,
    resistance_level: float | None = None,
    volume_last: float = float("nan"),
    volume_sma20: float = float("nan"),
    rsi_bar: float = float("nan"),
    close_bar: float = float("nan"),
    ema_20_bar: float = float("nan"),
    ema_50_bar: float = float("nan"),
    atr_14_abs: float = float("nan"),
    gate_mode: GateMode = GateMode.C,
    ev_calibration_curve: dict[str, float] | None = None,
    cfg: TradeGateConfig | None = None,
) -> TradeGateResult:
    cfg = cfg or TradeGateConfig()
    cost = _total_cost_frac(cfg)
    thresh_move = cfg.expected_move_vs_cost_mult * cost

    edge = float(comb_up_1h - comb_down_1h)
    natr = float(natr_14) if natr_14 == natr_14 else 0.0
    natr = max(natr, 1e-8)

    is_high_vol = vol_regime == "High vol"
    edge_base = cfg.edge_base_high_vol if is_high_vol else cfg.edge_base_normal
    edge_th = float(edge_base + cfg.edge_natr_k * natr)
    natr_move_mult = (
        cfg.expected_move_vs_natr_mult_high_vol if is_high_vol else cfg.expected_move_vs_natr_mult
    )

    conf = max(comb_up_1h, comb_down_1h) * 100.0

    curve: dict[str, float] = {}
    if gate_mode != GateMode.A:
        if ev_calibration_curve is not None:
            curve = dict(ev_calibration_curve)
        elif cfg.use_auto_ev_calibration:
            curve = load_ev_calibration(cfg.ev_calibration_json_path)

    ev_adj, ev_src = _resolve_ev_adj(ev, ev_bucket, cfg, curve)

    def _result(
        direction: str,
        reasons: list[str],
        code: str,
        sm: float = 0.0,
    ) -> TradeGateResult:
        return TradeGateResult(
            direction=direction,
            confidence=conf,
            edge=edge,
            reasons=reasons,
            reason_code=code,
            ev=ev,
            ev_adj=ev_adj,
            skew=skew,
            edge_threshold_used=edge_th,
            size_mult=sm,
            gate_mode=gate_mode.value,
        )

    if S_distance < cfg.liq_hard_min:
        return _result(
            "No trade",
            [f"hard_liquidity: S_distance {S_distance:.3f} < {cfg.liq_hard_min}"],
            LOW_LIQUIDITY,
        )

    if cfg.min_volume_vs_sma20_mult is not None:
        vl = float(volume_last)
        vsma = float(volume_sma20)
        if np.isfinite(vl) and np.isfinite(vsma) and vsma > 0:
            mul = vl / vsma
            thr = float(cfg.min_volume_vs_sma20_mult)
            if mul < thr:
                return _result(
                    "No trade",
                    [f"volume {vl:.6g} < {thr:.2f} × vol_SMA20 {vsma:.6g} (ratio {mul:.2f})"],
                    LOW_RELATIVE_VOLUME,
                )

    if cfg.require_forecast_ev_positive:
        evv = float(ev)
        if (not np.isfinite(evv)) or evv <= 0.0:
            return _result(
                "No trade",
                [f"forecast ev {evv:.4%} not positive"],
                FORECAST_EV_NOT_POSITIVE,
            )

    if cfg.use_ev_metrics_in_gate:
        if cfg.allowed_ev_buckets and ev_bucket not in cfg.allowed_ev_buckets:
            return _result(
                "No trade",
                [
                    f"ev_bucket {ev_bucket!r} not in allowed_ev_buckets "
                    f"({', '.join(sorted(cfg.allowed_ev_buckets))})"
                ],
                EV_BUCKET_NOT_ALLOWED,
            )

        if cfg.blocked_ev_buckets and ev_bucket in cfg.blocked_ev_buckets:
            return _result(
                "No trade",
                [f"ev_bucket {ev_bucket!r} in blocked_ev_buckets"],
                EV_BUCKET_BLOCKED,
            )

    if gate_mode == GateMode.C and knn_regime in cfg.blocked_knn_regimes:
        return _result(
            "No trade",
            [f"knn_regime {knn_regime!r} blocked by config"],
            REGIME_BLOCKED,
        )

    if (
        gate_mode == GateMode.C
        and cfg.min_effective_neighbors_gate is not None
        and effective_neighbors < cfg.min_effective_neighbors_gate
    ):
        return _result(
            "No trade",
            [
                f"effective_neighbors {effective_neighbors:.2f} < {cfg.min_effective_neighbors_gate} ({ev_src})"
            ],
            LOW_CONFIDENCE,
        )

    if gate_mode in (GateMode.B, GateMode.C) and cfg.use_ev_metrics_in_gate and cfg.use_ev_gate:
        if ev_adj <= cfg.ev_min:
            return _result(
                "No trade",
                [f"ev_adj {ev_adj:.4%} ({ev_src}) ≤ {cfg.ev_min:.4%}"],
                NEGATIVE_EV,
            )
        if cfg.min_ev_trade > 0 and ev_adj <= cfg.min_ev_trade:
            return _result(
                "No trade",
                [f"ev_adj {ev_adj:.4%} ≤ min_ev_trade {cfg.min_ev_trade:.4%}"],
                LOW_EV_MAGNITUDE,
            )

    if gate_mode in (GateMode.B, GateMode.C) and cfg.use_ev_metrics_in_gate and cfg.min_forecast_ev_abs is not None:
        ev_abs = abs(float(ev))
        if ev_abs < cfg.min_forecast_ev_abs:
            return _result(
                "No trade",
                [
                    f"|forecast ev| {ev_abs:.4%} < min_forecast_ev_abs {cfg.min_forecast_ev_abs:.4%}",
                ],
                LOW_FORECAST_EV,
            )

    if gate_mode == GateMode.C:
        if cfg.min_skew is not None and skew < cfg.min_skew:
            return _result(
                "No trade",
                [f"skew {skew:.3f} < min_skew {cfg.min_skew:.3f}"],
                LOW_SKEW,
            )
        if cfg.use_ev_metrics_in_gate and cfg.min_ev_vol_ratio is not None:
            # Use forecast EV (return scale), not ev_adj: empirical ev_adj is mean net PnL per bucket
            # and is not comparable to NATR; |ev|/natr is "edge vs noise" in like units.
            ev_to_vol = abs(float(ev)) / natr
            if ev_to_vol < cfg.min_ev_vol_ratio:
                return _result(
                    "No trade",
                    [f"|ev|/natr {ev_to_vol:.3f} < {cfg.min_ev_vol_ratio} (forecast ev={ev:.4%})"],
                    LOW_EV_TO_VOL,
                )

    sr_tol = max(float(cfg.sr_level_tolerance_natr_mult), 0.0) * natr
    price = float(last_price) if last_price is not None else float("nan")
    support = float(support_level) if support_level is not None else float("nan")
    resistance = float(resistance_level) if resistance_level is not None else float("nan")
    support_dist = float("inf")
    resistance_dist = float("inf")
    if np.isfinite(price) and np.isfinite(support) and price > 0 and support > 0:
        support_dist = abs(price - support) / price
    if np.isfinite(price) and np.isfinite(resistance) and price > 0 and resistance > 0:
        resistance_dist = abs(resistance - price) / price

    def long_checks_full() -> tuple[bool, list[str], str | None]:
        eu = max(float(high_ret_1h), 0.0)
        if cfg.trade_with_trend_only and trend_direction == "down":
            return (
                False,
                [f"trend_direction {trend_direction!r} forbids long (down trend)"],
                TREND_MISMATCH,
            )
        if cfg.use_support_resistance_filter and support_dist > sr_tol:
            return (
                False,
                [f"distance_to_support {support_dist:.3%} > {sr_tol:.3%}"],
                SR_LEVEL_MISMATCH,
            )
        if cfg.strict_trend_ema_alignment:
            c = float(close_bar)
            e2 = float(ema_20_bar)
            e5 = float(ema_50_bar)
            if not np.all(np.isfinite([c, e2, e5])):
                return False, ["strict trend: NaN close/ema20/ema50"], STRICT_TREND_EMA_BLOCK
            if not (c > e2 > e5):
                return (
                    False,
                    [f"strict trend failed: close {c:.6g} vs ema20 {e2:.6g}, ema50 {e5:.6g}"],
                    STRICT_TREND_EMA_BLOCK,
                )
        if cfg.rsi_max_long is not None and np.isfinite(float(rsi_bar)):
            rsi_v = float(rsi_bar)
            cap = float(cfg.rsi_max_long)
            # «не брать RSI > 70» → блок при rsi >= 70 при cap=70
            # Не брать если RSI выше порога (> cap при типичном cap=70)
            if rsi_v > cap:
                return False, [f"RSI14 {rsi_v:.1f} > {cap:.1f} (перегрет long)"], RSI_BLOCK_LONG
        if cfg.max_price_ema50_atr_mult is not None:
            atrv = float(atr_14_abs)
            if np.isfinite(atrv) and atrv > 0 and np.isfinite(float(close_bar)) and np.isfinite(float(ema_50_bar)):
                dist_abs = abs(float(close_bar) - float(ema_50_bar))
                lim = float(cfg.max_price_ema50_atr_mult) * atrv
                if dist_abs > lim:
                    return (
                        False,
                        [f"|close-EMA50| {dist_abs:.4g} > {cfg.max_price_ema50_atr_mult:g}×ATR {lim:.4g}"],
                        EMA50_ATR_DISTANCE_BLOCK,
                    )
        if cfg.weak_breakout_edge_ratio is not None:
            need = edge_th * float(cfg.weak_breakout_edge_ratio)
            if edge < need:
                return (
                    False,
                    [f"weak long edge: edge {edge:.3f} < {cfg.weak_breakout_edge_ratio:g} × edge_th {need:.3f}"],
                    WEAK_BREAKOUT_EDGE,
                )
        if edge <= edge_th:
            return False, [f"need edge > {edge_th:.3f}, got {edge:.3f}"], LOW_EDGE
        if comb_up_1h <= cfg.comb_prob_min:
            return False, [f"comb_up {comb_up_1h:.1%} ≤ {cfg.comb_prob_min:.0%}"], LOW_EDGE
        if eu <= thresh_move:
            return (
                False,
                [f"expected_up {eu:.3%} ≤ {thresh_move:.3%} (cost×{cfg.expected_move_vs_cost_mult})"],
                LOW_EXPECTED_MOVE,
            )
        if eu < natr_move_mult * natr:
            return (
                False,
                [f"expected_up {eu:.3%} < {natr_move_mult}×NATR {natr:.3%}"],
                HIGH_RISK,
            )
        return True, [], None

    def short_checks_full() -> tuple[bool, list[str], str | None]:
        ed = max(-float(low_ret_1h), 0.0)
        if cfg.trade_with_trend_only and trend_direction == "up":
            return (
                False,
                [f"trend_direction {trend_direction!r} forbids short (up trend)"],
                TREND_MISMATCH,
            )
        if cfg.use_support_resistance_filter and resistance_dist > sr_tol:
            return (
                False,
                [f"distance_to_resistance {resistance_dist:.3%} > {sr_tol:.3%}"],
                SR_LEVEL_MISMATCH,
            )
        if cfg.strict_trend_ema_alignment:
            c = float(close_bar)
            e2 = float(ema_20_bar)
            e5 = float(ema_50_bar)
            if not np.all(np.isfinite([c, e2, e5])):
                return False, ["strict trend: NaN close/ema20/ema50"], STRICT_TREND_EMA_BLOCK
            if not (c < e2 < e5):
                return (
                    False,
                    [f"strict trend failed (short): close {c:.6g} vs ema20 {e2:.6g}, ema50 {e5:.6g}"],
                    STRICT_TREND_EMA_BLOCK,
                )
        if cfg.rsi_min_short is not None and np.isfinite(float(rsi_bar)):
            rsi_v = float(rsi_bar)
            floorv = float(cfg.rsi_min_short)
            if rsi_v < floorv:
                return False, [f"RSI14 {rsi_v:.1f} < {floorv:.1f} (перепродан short)"], RSI_BLOCK_SHORT
        if cfg.max_price_ema50_atr_mult is not None:
            atrv = float(atr_14_abs)
            if np.isfinite(atrv) and atrv > 0 and np.isfinite(float(close_bar)) and np.isfinite(float(ema_50_bar)):
                dist_abs = abs(float(close_bar) - float(ema_50_bar))
                lim = float(cfg.max_price_ema50_atr_mult) * atrv
                if dist_abs > lim:
                    return (
                        False,
                        [f"|close-EMA50| {dist_abs:.4g} > {cfg.max_price_ema50_atr_mult:g}×ATR {lim:.4g}"],
                        EMA50_ATR_DISTANCE_BLOCK,
                    )
        if cfg.weak_breakout_edge_ratio is not None:
            need = edge_th * float(cfg.weak_breakout_edge_ratio)
            # шорт нужен достаточно отрицательный edge (не «полупробой»)
            if edge > -need:
                return (
                    False,
                    [f"weak short edge: edge {edge:.3f} > -{need:.3f} (threshold×{cfg.weak_breakout_edge_ratio:g})"],
                    WEAK_BREAKOUT_EDGE,
                )
        if edge >= -edge_th:
            return False, [f"need edge < {-edge_th:.3f}, got {edge:.3f}"], LOW_EDGE
        if comb_down_1h <= cfg.comb_prob_min:
            return False, [f"comb_down {comb_down_1h:.1%} ≤ {cfg.comb_prob_min:.0%}"], LOW_EDGE
        if ed <= thresh_move:
            return (
                False,
                [f"expected_down {ed:.3%} ≤ {thresh_move:.3%} (cost×{cfg.expected_move_vs_cost_mult})"],
                LOW_EXPECTED_MOVE,
            )
        if ed < natr_move_mult * natr:
            return (
                False,
                [f"expected_down {ed:.3%} < {natr_move_mult}×NATR {natr:.3%}"],
                HIGH_RISK,
            )
        return True, [], None

    def long_checks_edge() -> tuple[bool, list[str], str | None]:
        if cfg.trade_with_trend_only and trend_direction == "down":
            return (
                False,
                [f"trend_direction {trend_direction!r} forbids long (down trend)"],
                TREND_MISMATCH,
            )
        if cfg.use_support_resistance_filter and support_dist > sr_tol:
            return (
                False,
                [f"distance_to_support {support_dist:.3%} > {sr_tol:.3%}"],
                SR_LEVEL_MISMATCH,
            )
        if cfg.strict_trend_ema_alignment:
            c = float(close_bar)
            e2 = float(ema_20_bar)
            e5 = float(ema_50_bar)
            if not np.all(np.isfinite([c, e2, e5])):
                return False, ["strict trend: NaN close/ema20/ema50"], STRICT_TREND_EMA_BLOCK
            if not (c > e2 > e5):
                return (
                    False,
                    [f"strict trend failed: close {c:.6g} vs ema20 {e2:.6g}, ema50 {e5:.6g}"],
                    STRICT_TREND_EMA_BLOCK,
                )
        if cfg.rsi_max_long is not None and np.isfinite(float(rsi_bar)):
            rsi_v = float(rsi_bar)
            cap = float(cfg.rsi_max_long)
            if rsi_v > cap:
                return False, [f"RSI14 {rsi_v:.1f} > {cap:.1f} (перегрет long)"], RSI_BLOCK_LONG
        if cfg.max_price_ema50_atr_mult is not None:
            atrv = float(atr_14_abs)
            if np.isfinite(atrv) and atrv > 0 and np.isfinite(float(close_bar)) and np.isfinite(float(ema_50_bar)):
                dist_abs = abs(float(close_bar) - float(ema_50_bar))
                lim = float(cfg.max_price_ema50_atr_mult) * atrv
                if dist_abs > lim:
                    return (
                        False,
                        [f"|close-EMA50| {dist_abs:.4g} > {cfg.max_price_ema50_atr_mult:g}×ATR {lim:.4g}"],
                        EMA50_ATR_DISTANCE_BLOCK,
                    )
        if cfg.weak_breakout_edge_ratio is not None:
            need = edge_th * float(cfg.weak_breakout_edge_ratio)
            if edge < need:
                return (
                    False,
                    [f"weak long edge: edge {edge:.3f} < {need:.3f}"],
                    WEAK_BREAKOUT_EDGE,
                )
        if edge <= edge_th:
            return False, [f"need edge > {edge_th:.3f}, got {edge:.3f}"], LOW_EDGE
        if comb_up_1h <= cfg.comb_prob_min:
            return False, [f"comb_up {comb_up_1h:.1%} ≤ {cfg.comb_prob_min:.0%}"], LOW_EDGE
        return True, [], None

    def short_checks_edge() -> tuple[bool, list[str], str | None]:
        if cfg.trade_with_trend_only and trend_direction == "up":
            return (
                False,
                [f"trend_direction {trend_direction!r} forbids short (up trend)"],
                TREND_MISMATCH,
            )
        if cfg.use_support_resistance_filter and resistance_dist > sr_tol:
            return (
                False,
                [f"distance_to_resistance {resistance_dist:.3%} > {sr_tol:.3%}"],
                SR_LEVEL_MISMATCH,
            )
        if cfg.strict_trend_ema_alignment:
            c = float(close_bar)
            e2 = float(ema_20_bar)
            e5 = float(ema_50_bar)
            if not np.all(np.isfinite([c, e2, e5])):
                return False, ["strict trend: NaN close/ema20/ema50"], STRICT_TREND_EMA_BLOCK
            if not (c < e2 < e5):
                return (
                    False,
                    [f"strict trend failed (short): close {c:.6g} vs ema20 {e2:.6g}, ema50 {e5:.6g}"],
                    STRICT_TREND_EMA_BLOCK,
                )
        if cfg.rsi_min_short is not None and np.isfinite(float(rsi_bar)):
            rsi_v = float(rsi_bar)
            floorv = float(cfg.rsi_min_short)
            if rsi_v < floorv:
                return False, [f"RSI14 {rsi_v:.1f} < {floorv:.1f} (перепродан short)"], RSI_BLOCK_SHORT
        if cfg.max_price_ema50_atr_mult is not None:
            atrv = float(atr_14_abs)
            if np.isfinite(atrv) and atrv > 0 and np.isfinite(float(close_bar)) and np.isfinite(float(ema_50_bar)):
                dist_abs = abs(float(close_bar) - float(ema_50_bar))
                lim = float(cfg.max_price_ema50_atr_mult) * atrv
                if dist_abs > lim:
                    return (
                        False,
                        [f"|close-EMA50| {dist_abs:.4g} > {cfg.max_price_ema50_atr_mult:g}×ATR {lim:.4g}"],
                        EMA50_ATR_DISTANCE_BLOCK,
                    )
        if cfg.weak_breakout_edge_ratio is not None:
            need = edge_th * float(cfg.weak_breakout_edge_ratio)
            if edge > -need:
                return (
                    False,
                    [f"weak short edge: edge {edge:.3f} > -{need:.3f}"],
                    WEAK_BREAKOUT_EDGE,
                )
        if edge >= -edge_th:
            return False, [f"need edge < {-edge_th:.3f}, got {edge:.3f}"], LOW_EDGE
        if comb_down_1h <= cfg.comb_prob_min:
            return False, [f"comb_down {comb_down_1h:.1%} ≤ {cfg.comb_prob_min:.0%}"], LOW_EDGE
        return True, [], None

    if gate_mode == GateMode.A:
        long_ok, long_reasons, long_code = long_checks_edge()
        short_ok, short_reasons, short_code = short_checks_edge()
    else:
        long_ok, long_reasons, long_code = long_checks_full()
        short_ok, short_reasons, short_code = short_checks_full()

    if cfg.long_only:
        short_ok = False
        short_reasons = ["long_only enabled: shorts disabled"]
        short_code = SHORTS_DISABLED

    if long_ok and not short_ok:
        sm = (
            position_size_mult_v2(ev_adj, cfg.ev_target_for_size, edge, edge_th)
            if cfg.use_ev_metrics_in_gate
            else 1.0
        )
        return TradeGateResult(
            "Long",
            conf,
            edge,
            ["gate_ok_long"],
            OK_LONG,
            ev=ev,
            ev_adj=ev_adj,
            skew=skew,
            edge_threshold_used=edge_th,
            size_mult=sm,
            gate_mode=gate_mode.value,
        )
    if short_ok and not long_ok:
        sm = (
            position_size_mult_v2(ev_adj, cfg.ev_target_for_size, edge, edge_th)
            if cfg.use_ev_metrics_in_gate
            else 1.0
        )
        return TradeGateResult(
            "Short",
            conf,
            edge,
            ["gate_ok_short"],
            OK_SHORT,
            ev=ev,
            ev_adj=ev_adj,
            skew=skew,
            edge_threshold_used=edge_th,
            size_mult=sm,
            gate_mode=gate_mode.value,
        )
    if long_ok and short_ok:
        return TradeGateResult(
            "No trade",
            conf,
            edge,
            ["ambiguous_long_and_short"],
            AMBIGUOUS,
            ev=ev,
            ev_adj=ev_adj,
            skew=skew,
            edge_threshold_used=edge_th,
            size_mult=0.0,
            gate_mode=gate_mode.value,
        )

    if edge >= 0:
        reasons = long_reasons or short_reasons
        primary = long_code or short_code or NO_SIGNAL
    else:
        reasons = short_reasons or long_reasons
        primary = short_code or long_code or NO_SIGNAL
    return TradeGateResult(
        "No trade",
        conf,
        edge,
        reasons,
        primary,
        ev=ev,
        ev_adj=ev_adj,
        skew=skew,
        edge_threshold_used=edge_th,
        size_mult=0.0,
        gate_mode=gate_mode.value,
    )
