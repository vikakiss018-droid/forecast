"""
Event-driven scalp engine: depth ticks + aggTrade → persistent OBI signals (paper).

Runs inside API asyncio loop; does not use hourly scan as entry trigger.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import websockets

from .orderbook_layer import (
    OrderBookMetrics,
    SPOT_WS_BASE,
    get_orderbook_metrics,
    register_depth_handler,
    spot_stream_symbol,
    top_live_symbols,
)
from .scalp_config import ScalpConfig, load_scalp_config
from .scalp_flow import AggFlowState
from .scalp_signals import SymbolSignalState, evaluate_scalp_signal
from .scalp_trader import append_tick_log, emit_paper_signal, get_scalp_trader_status

_FLOWS: dict[str, AggFlowState] = {}
_SYM_STATES: dict[str, SymbolSignalState] = {}
_LOCK = asyncio.Lock()
_RUNNING = False
_CFG: ScalpConfig | None = None
_LAST_TICK_LOG: dict[str, float] = {}
_ENGINE_STATS = {"depth_ticks": 0, "agg_trades": 0, "signals_checked": 0}


def _sym_state(symbol: str) -> SymbolSignalState:
    if symbol not in _SYM_STATES:
        _SYM_STATES[symbol] = SymbolSignalState()
    return _SYM_STATES[symbol]


def _flow(symbol: str) -> AggFlowState:
    if symbol not in _FLOWS:
        _FLOWS[symbol] = AggFlowState()
    return _FLOWS[symbol]


async def _on_depth_tick(symbol: str, metrics: OrderBookMetrics) -> None:
    cfg = _CFG
    if cfg is None or not cfg.enabled:
        return

    async with _LOCK:
        _ENGINE_STATS["depth_ticks"] += 1
        _ENGINE_STATS["signals_checked"] += 1
        sym_state = _sym_state(symbol)
        flow = _FLOWS.get(symbol)
        signal = evaluate_scalp_signal(symbol, metrics, flow, sym_state, cfg)
        if signal is not None:
            emit_paper_signal(signal, cfg)
            # Reset persist after emit to avoid duplicate fires on same wave
            sym_state.persist.long_since = None
            sym_state.persist.short_since = None

        if cfg.tick_log:
            now = time.time()
            last = _LAST_TICK_LOG.get(symbol, 0.0)
            if now - last >= 10.0:
                _LAST_TICK_LOG[symbol] = now
                snap = metrics.to_dict()
                if flow is not None:
                    snap["flow"] = flow.to_dict(cfg.agg_flow_window_sec)
                snap["skip_reason"] = sym_state.last_skip_reason
                append_tick_log(symbol, snap)


async def _connect_agg_trade(symbol: str) -> None:
    stream = spot_stream_symbol(symbol).lower()
    url = f"{SPOT_WS_BASE}/{stream}@aggTrade"
    flow = _flow(symbol)

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    ts = float(data.get("T", 0)) / 1000.0 or time.time()
                    qty = float(data.get("q", 0))
                    price = float(data.get("p", 0))
                    is_buyer_maker = bool(data.get("m", False))
                    flow.add(ts=ts, is_buyer_maker=is_buyer_maker, qty=qty, price=price)
                    _ENGINE_STATS["agg_trades"] += 1
        except Exception:
            await asyncio.sleep(2.0)


async def start_scalp_engine(symbols: list[str] | None = None) -> None:
    """Subscribe to depth callbacks + aggTrade; evaluate signals on every depth tick."""
    global _RUNNING, _CFG
    if _RUNNING:
        return

    _CFG = load_scalp_config()
    if not _CFG.enabled:
        print("[scalp] SCALP_ENABLED=0 — engine idle", flush=True)
        return

    _RUNNING = True
    syms = list(symbols or top_live_symbols())
    if not syms:
        print("[scalp] no live symbols — engine idle", flush=True)
        return

    register_depth_handler(_on_depth_tick)
    mode = "paper" if _CFG.dry_run else "live"
    print(
        f"[scalp] engine start ({mode}) top-{len(syms)}: {', '.join(syms)} "
        f"obi>={_CFG.obi_long_min} persist={_CFG.obi_persist_sec}s "
        f"TP={_CFG.min_tp_pct}% SL={_CFG.sl_pct}%",
        flush=True,
    )

    tasks = [asyncio.create_task(_connect_agg_trade(s)) for s in syms]
    await asyncio.gather(*tasks)


def get_scalp_engine_status() -> dict[str, Any]:
    cfg = _CFG or load_scalp_config()
    syms = top_live_symbols()
    rows: list[dict[str, Any]] = []
    for sym in syms:
        m = get_orderbook_metrics(sym)
        st = _sym_state(sym)
        row: dict[str, Any] = {
            "symbol": sym,
            "skip_reason": st.last_skip_reason,
        }
        if m is not None:
            row.update(m.to_dict())
        fl = _FLOWS.get(sym)
        if fl is not None:
            row["flow"] = fl.to_dict(cfg.agg_flow_window_sec)
        ps = st.persist
        row["obi_persist_long_sec"] = (
            round(time.time() - ps.long_since, 1) if ps.long_since else None
        )
        row["obi_persist_short_sec"] = (
            round(time.time() - ps.short_since, 1) if ps.short_since else None
        )
        rows.append(row)

    return {
        "enabled": cfg.enabled,
        "running": _RUNNING,
        "config": {
            "dry_run": cfg.dry_run,
            "obi_long_min": cfg.obi_long_min,
            "obi_short_max": cfg.obi_short_max,
            "obi_persist_sec": cfg.obi_persist_sec,
            "max_spread_bps": cfg.max_spread_bps,
            "cooldown_sec": cfg.cooldown_sec,
            "min_tp_pct": cfg.min_tp_pct,
            "sl_pct": cfg.sl_pct,
            "time_stop_sec": cfg.time_stop_sec,
            "require_agg_flow": cfg.require_agg_flow,
        },
        "engine_stats": dict(_ENGINE_STATS),
        "trader": get_scalp_trader_status(cfg),
        "rows": rows,
    }
