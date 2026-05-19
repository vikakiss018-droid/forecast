from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import websockets


@dataclass
class OrderBookSide:
    levels: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class OrderBookState:
    symbol: str
    bids: OrderBookSide = field(default_factory=OrderBookSide)
    asks: OrderBookSide = field(default_factory=OrderBookSide)
    last_update_ts: float = 0.0


_ORDERBOOKS: Dict[str, OrderBookState] = {}
_LOCK = asyncio.Lock()


async def _connect_depth_stream(symbol: str) -> None:
    """Connect to Binance Futures USDT-M depth stream for a single symbol (e.g. BTCUSDT)."""
    stream_symbol = symbol.lower()
    url = f"wss://fstream.binance.com/ws/{stream_symbol}@depth80@100ms"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    bids = [(float(p), float(q)) for p, q in data.get("b", [])]
                    asks = [(float(p), float(q)) for p, q in data.get("a", [])]

                    async with _LOCK:
                        ob = _ORDERBOOKS.get(symbol)
                        if ob is None:
                            ob = OrderBookState(symbol=symbol)
                            _ORDERBOOKS[symbol] = ob
                        ob.bids.levels = bids
                        ob.asks.levels = asks
                        ob.last_update_ts = time.time()
        except Exception:
            await asyncio.sleep(2.0)


async def start_orderbook_stream(symbols: List[str]) -> None:
    """Start background tasks for orderbooks for the given list of symbols."""
    tasks = []
    for s in symbols:
        tasks.append(asyncio.create_task(_connect_depth_stream(s)))
    await asyncio.gather(*tasks)


def get_liquidity_snapshot(symbol: str, mid_price: float, pct: float = 0.01) -> Tuple[float, float]:
    """
    Return (liq_up, liq_down) from current orderbook snapshot in ±pct range around mid_price.
    If no data, returns (0, 0).
    """
    ob = _ORDERBOOKS.get(symbol)
    if ob is None or not ob.bids.levels or not ob.asks.levels:
        return 0.0, 0.0

    up_min = mid_price
    up_max = mid_price * (1.0 + pct)
    down_min = mid_price * (1.0 - pct)
    down_max = mid_price

    # Чем ближе к текущей цене, тем больше вес (обратный вес по расстоянию)
    liq_up = 0.0
    for price, vol in ob.asks.levels:
        if up_min <= price <= up_max:
            dist = max(price - mid_price, 1e-9)
            weight = 1.0 / dist
            liq_up += vol * weight

    liq_down = 0.0
    for price, vol in ob.bids.levels:
        if down_min <= price <= down_max:
            dist = max(mid_price - price, 1e-9)
            weight = 1.0 / dist
            liq_down += vol * weight

    return liq_up, liq_down


