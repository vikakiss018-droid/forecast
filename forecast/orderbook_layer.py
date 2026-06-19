"""
Spot order book layer: partial depth @100ms, OBI + microprice for intraday timing.

Uses top-N symbols from live filtered ranking (symbol_ranking_filtered_r05_win50.json).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import websockets

SPOT_WS_BASE = "wss://stream.binance.com:9443/ws"


@dataclass
class OrderBookMetrics:
    symbol: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid: float = 0.0
    microprice: float = 0.0
    spread_bps: float = 0.0
    obi: float = 0.0
    obi_smooth: float = 0.0
    bid_vol: float = 0.0
    ask_vol: float = 0.0
    levels: int = 0
    updated_at: float = 0.0
    stale: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid": round(self.mid, 8),
            "microprice": round(self.microprice, 8),
            "spread_bps": round(self.spread_bps, 2),
            "obi": round(self.obi, 4),
            "obi_smooth": round(self.obi_smooth, 4),
            "bid_vol": round(self.bid_vol, 4),
            "ask_vol": round(self.ask_vol, 4),
            "levels": self.levels,
            "updated_at": self.updated_at,
            "stale": self.stale,
            "age_sec": round(max(0.0, time.time() - self.updated_at), 2) if self.updated_at else None,
        }


@dataclass
class _Ema:
    period: int
    value: float | None = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            alpha = 2.0 / (self.period + 1.0)
            self.value = alpha * x + (1.0 - alpha) * self.value
        return float(self.value)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def spot_stream_symbol(symbol: str) -> str:
    """QNT/USDT -> qntusdt"""
    return symbol.replace("/", "").upper()


def top_live_symbols(n: int | None = None) -> tuple[str, ...]:
    """Top-N pairs for orderbook/scalp (scalp live list or swing filtered list)."""
    from .run_symbol_ranking import load_filtered_symbols, load_symbol_ranking_filtered

    n = n or _env_int("ORDERBOOK_TOP_N", 8)
    if _env_bool("SCALP_USE_DEDICATED_LIST", True):
        from .run_scalp_pair_ranking import load_scalp_live_symbols

        scalp_syms = load_scalp_live_symbols()
        if scalp_syms:
            return scalp_syms[:n]

    data = load_symbol_ranking_filtered()
    ranking = list(data.get("ranking") or [])
    if ranking:
        if all("total_r" in r for r in ranking):
            ranking.sort(key=lambda r: -float(r.get("total_r") or 0))
        return tuple(str(r["symbol"]) for r in ranking[:n])
    syms = load_filtered_symbols()
    return syms[:n]


def compute_obi(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    levels: int,
) -> tuple[float, float, float]:
    """OBI and summed volumes over top `levels`."""
    bv = sum(q for _, q in bids[:levels])
    av = sum(q for _, q in asks[:levels])
    denom = bv + av
    if denom <= 0:
        return 0.0, bv, av
    return (bv - av) / denom, bv, av


def compute_microprice(best_bid: float, best_ask: float, bid_vol: float, ask_vol: float) -> float:
    denom = bid_vol + ask_vol
    if denom <= 0 or best_bid <= 0 or best_ask <= 0:
        return (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    return (best_bid * ask_vol + best_ask * bid_vol) / denom


def _parse_metrics(
    symbol: str,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    obi_levels: int,
    ema: _Ema,
) -> OrderBookMetrics:
    now = time.time()
    if not bids or not asks:
        return OrderBookMetrics(symbol=symbol, updated_at=now, stale=True)

    best_bid, bid_q0 = bids[0]
    best_ask, ask_q0 = asks[0]
    mid = (best_bid + best_ask) / 2.0
    spread_bps = ((best_ask - best_bid) / mid * 10_000.0) if mid > 0 else 0.0
    obi, bid_vol, ask_vol = compute_obi(bids, asks, levels=obi_levels)
    obi_smooth = ema.update(obi)
    microprice = compute_microprice(best_bid, best_ask, bid_q0, ask_q0)

    return OrderBookMetrics(
        symbol=symbol,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        microprice=microprice,
        spread_bps=spread_bps,
        obi=obi,
        obi_smooth=obi_smooth,
        bid_vol=bid_vol,
        ask_vol=ask_vol,
        levels=min(obi_levels, len(bids), len(asks)),
        updated_at=now,
        stale=False,
    )


_METRICS: dict[str, OrderBookMetrics] = {}
_EMA: dict[str, _Ema] = {}
_DEPTH_HANDLERS: list[Callable[[str, OrderBookMetrics], Awaitable[None] | None]] = []
_LOCK = asyncio.Lock()
_RUNNING = False


def register_depth_handler(
    handler: Callable[[str, OrderBookMetrics], Awaitable[None] | None],
) -> None:
    """Subscribe to each depth tick (used by scalp_engine)."""
    if handler not in _DEPTH_HANDLERS:
        _DEPTH_HANDLERS.append(handler)


async def _notify_depth_handlers(symbol: str, metrics: OrderBookMetrics) -> None:
    for handler in _DEPTH_HANDLERS:
        try:
            res = handler(symbol, metrics)
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            print(f"[orderbook] depth handler error {symbol}: {exc}", flush=True)


async def _connect_spot_depth(symbol: str) -> None:
    stream = spot_stream_symbol(symbol).lower()
    url = f"{SPOT_WS_BASE}/{stream}@depth20@100ms"
    obi_levels = _env_int("ORDERBOOK_OBI_LEVELS", 10)
    ema_period = _env_int("ORDERBOOK_OBI_EMA_TICKS", 25)
    stale_sec = _env_float("ORDERBOOK_STALE_SEC", 5.0)

    if symbol not in _EMA:
        _EMA[symbol] = _Ema(period=ema_period)

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    bids = [(float(p), float(q)) for p, q in data.get("bids", data.get("b", []))]
                    asks = [(float(p), float(q)) for p, q in data.get("asks", data.get("a", []))]
                    metrics = _parse_metrics(
                        symbol,
                        bids,
                        asks,
                        obi_levels=obi_levels,
                        ema=_EMA[symbol],
                    )
                    async with _LOCK:
                        _METRICS[symbol] = metrics
                    await _notify_depth_handlers(symbol, metrics)
        except Exception:
            async with _LOCK:
                prev = _METRICS.get(symbol)
                if prev is not None:
                    prev.stale = True
            await asyncio.sleep(2.0)

        await asyncio.sleep(0.05)
        if symbol in _METRICS and time.time() - _METRICS[symbol].updated_at > stale_sec:
            _METRICS[symbol].stale = True


async def _refresh_symbol_list_loop() -> None:
    """Reload top-N from disk every few minutes (after user approves new live list)."""
    interval = _env_int("ORDERBOOK_RELOAD_SEC", 300)
    while True:
        await asyncio.sleep(interval)
        # New symbols picked up on next API restart; optional future: dynamic resubscribe


async def start_spot_orderbook_layer(symbols: list[str] | None = None) -> None:
    """Background: spot depth streams for top-N live pairs."""
    global _RUNNING
    if _RUNNING:
        return
    _RUNNING = True
    syms = list(symbols or top_live_symbols())
    if not syms:
        print("[orderbook] no live symbols — depth layer idle", flush=True)
        return
    print(f"[orderbook] spot depth top-{len(syms)}: {', '.join(syms)}", flush=True)
    tasks = [asyncio.create_task(_connect_spot_depth(s)) for s in syms]
    tasks.append(asyncio.create_task(_refresh_symbol_list_loop()))
    await asyncio.gather(*tasks)


def get_orderbook_metrics(symbol: str) -> OrderBookMetrics | None:
    return _METRICS.get(symbol)


def get_all_orderbook_snapshots() -> list[dict[str, Any]]:
    syms = top_live_symbols()
    out: list[dict[str, Any]] = []
    for sym in syms:
        m = _METRICS.get(sym)
        if m is None:
            out.append({"symbol": sym, "stale": True, "obi_smooth": None})
        else:
            out.append(m.to_dict())
    return out


def orderbook_entry_ok(symbol: str, side: str) -> tuple[bool, str]:
    """
    Timing gate: long needs OBI_smooth >= threshold; short <= -threshold.
    Used as confirmation layer on top of 1h scan bias.
    """
    if not _env_bool("ORDERBOOK_GATE_ENABLED", False):
        return True, "GATE_OFF"

    m = _METRICS.get(symbol)
    if m is None or m.stale:
        return False, "NO_OB_DATA"

    age = time.time() - m.updated_at
    if age > _env_float("ORDERBOOK_STALE_SEC", 5.0):
        return False, "OB_STALE"

    max_spread = _env_float("ORDERBOOK_MAX_SPREAD_BPS", 25.0)
    if m.spread_bps > max_spread:
        return False, f"WIDE_SPREAD:{m.spread_bps:.1f}bps"

    side_n = side.strip().lower()
    long_min = _env_float("ORDERBOOK_OBI_LONG_MIN", 0.12)
    short_max = _env_float("ORDERBOOK_OBI_SHORT_MAX", -0.12)

    if side_n in ("long", "buy"):
        if m.obi_smooth >= long_min:
            return True, f"OBI_OK:{m.obi_smooth:.3f}"
        return False, f"OBI_LOW:{m.obi_smooth:.3f}<{long_min}"

    if side_n in ("short", "sell"):
        if m.obi_smooth <= short_max:
            return True, f"OBI_OK:{m.obi_smooth:.3f}"
        return False, f"OBI_HIGH:{m.obi_smooth:.3f}>{short_max}"

    return False, f"BAD_SIDE:{side}"


def filter_candidates_by_orderbook(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Keep candidates that pass OBI timing gate (when enabled)."""
    if not _env_bool("ORDERBOOK_GATE_ENABLED", False):
        return candidates, "GATE_OFF"

    passed: list[dict[str, Any]] = []
    last_reason = "NO_OB_MATCH"
    for cand in candidates:
        setup = cand.get("setup") or {}
        side = str(setup.get("direction") or "long")
        sym = str(cand.get("symbol") or "")
        ok, reason = orderbook_entry_ok(sym, side)
        if ok:
            out = dict(cand)
            ob = get_orderbook_metrics(sym)
            if ob:
                out["orderbook"] = ob.to_dict()
            passed.append(out)
        else:
            last_reason = f"{sym}:{reason}"
            print(f"[orderbook] skip {sym} ({reason})", flush=True)

    if passed:
        return passed, "OK"
    return [], f"NO_OB_TIMING ({last_reason})"
