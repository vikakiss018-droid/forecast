"""Scalp signal logic: persistent OBI (+ optional aggTrade confirmation)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .orderbook_layer import OrderBookMetrics
from .scalp_config import ScalpConfig
from .scalp_flow import AggFlowState

Side = Literal["long", "short"]


@dataclass
class PersistState:
    long_since: float | None = None
    short_since: float | None = None


@dataclass
class ScalpSignal:
    symbol: str
    side: Side
    entry: float
    obi_smooth: float
    persist_sec: float
    spread_bps: float
    microprice: float
    mid: float
    flow: dict[str, Any] | None = None
    reason: str = "OBI_PERSIST"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry": self.entry,
            "obi_smooth": round(self.obi_smooth, 4),
            "persist_sec": round(self.persist_sec, 2),
            "spread_bps": round(self.spread_bps, 2),
            "microprice": self.microprice,
            "mid": self.mid,
            "flow": self.flow,
            "reason": self.reason,
        }


@dataclass
class SymbolSignalState:
    persist: PersistState = field(default_factory=PersistState)
    last_skip_reason: str = ""


def _update_persist(
    state: PersistState,
    obi_smooth: float,
    *,
    long_min: float,
    short_max: float,
    now: float,
) -> None:
    if obi_smooth >= long_min:
        if state.long_since is None:
            state.long_since = now
        state.short_since = None
    elif obi_smooth <= short_max:
        if state.short_since is None:
            state.short_since = now
        state.long_since = None
    else:
        state.long_since = None
        state.short_since = None


def _flow_confirms(side: Side, flow: AggFlowState, cfg: ScalpConfig) -> tuple[bool, dict[str, Any]]:
    snap = flow.to_dict(cfg.agg_flow_window_sec)
    buy_v = float(snap["buy_vol"])
    sell_v = float(snap["sell_vol"])
    ratio = float(snap["flow_ratio"])
    min_r = cfg.agg_flow_min_ratio

    if side == "long":
        ok = buy_v > 0 and ratio >= min_r
        snap["confirms"] = ok
        return ok, snap
    ok = sell_v > 0 and (sell_v / buy_v if buy_v > 0 else 99.0) >= min_r
    snap["confirms"] = ok
    snap["flow_ratio"] = round(sell_v / buy_v if buy_v > 0 else (99.0 if sell_v > 0 else 1.0), 3)
    return ok, snap


def evaluate_scalp_signal(
    symbol: str,
    metrics: OrderBookMetrics,
    flow: AggFlowState | None,
    sym_state: SymbolSignalState,
    cfg: ScalpConfig,
    *,
    now: float | None = None,
) -> ScalpSignal | None:
    """Return a signal when OBI has been above/below threshold for persist_sec."""
    now = now or time.time()
    sym_state.last_skip_reason = ""

    if metrics.stale or metrics.mid <= 0:
        sym_state.last_skip_reason = "STALE"
        return None

    age = now - metrics.updated_at
    if age > cfg.stale_sec:
        sym_state.last_skip_reason = "OB_STALE"
        return None

    if metrics.spread_bps > cfg.max_spread_bps:
        sym_state.last_skip_reason = f"WIDE_SPREAD:{metrics.spread_bps:.1f}"
        return None

    obi = metrics.obi_smooth
    _update_persist(
        sym_state.persist,
        obi,
        long_min=cfg.obi_long_min,
        short_max=cfg.obi_short_max,
        now=now,
    )

    side: Side | None = None
    persist_sec = 0.0
    ps = sym_state.persist

    if ps.long_since is not None:
        elapsed = now - ps.long_since
        if elapsed >= cfg.obi_persist_sec:
            side = "long"
            persist_sec = elapsed
    elif ps.short_since is not None:
        elapsed = now - ps.short_since
        if elapsed >= cfg.obi_persist_sec:
            if not cfg.spot_allow_short:
                sym_state.last_skip_reason = "SHORT_DISABLED"
                return None
            side = "short"
            persist_sec = elapsed

    if side is None:
        if ps.long_since is not None:
            sym_state.last_skip_reason = f"OBI_BUILDING_LONG:{now - ps.long_since:.1f}s"
        elif ps.short_since is not None:
            sym_state.last_skip_reason = f"OBI_BUILDING_SHORT:{now - ps.short_since:.1f}s"
        else:
            sym_state.last_skip_reason = "OBI_NEUTRAL"
        return None

    flow_snap: dict[str, Any] | None = None
    if flow is not None:
        flow_ok, flow_snap = _flow_confirms(side, flow, cfg)
        if cfg.require_agg_flow and not flow_ok:
            sym_state.last_skip_reason = f"FLOW_WEAK:{flow_snap.get('flow_ratio')}"
            return None

    reason = "OBI_PERSIST"
    if flow_snap and flow_snap.get("confirms"):
        reason = "OBI_PERSIST+FLOW"

    return ScalpSignal(
        symbol=symbol,
        side=side,
        entry=metrics.mid,
        obi_smooth=obi,
        persist_sec=persist_sec,
        spread_bps=metrics.spread_bps,
        microprice=metrics.microprice,
        mid=metrics.mid,
        flow=flow_snap,
        reason=reason,
    )
