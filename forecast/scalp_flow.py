"""Rolling aggTrade flow (buy vs sell taker volume)."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AggTrade:
    ts: float
    side: str  # "buy" | "sell" (taker side)
    qty: float
    price: float


@dataclass
class AggFlowState:
    trades: deque[AggTrade] = field(default_factory=deque)

    def add(self, *, ts: float, is_buyer_maker: bool, qty: float, price: float) -> None:
        # Binance: m=true → buyer is maker → sell aggressor (taker sell)
        side = "sell" if is_buyer_maker else "buy"
        self.trades.append(AggTrade(ts=ts, side=side, qty=qty, price=price))
        self._trim(ts)

    def _trim(self, now: float, window_sec: float = 30.0) -> None:
        cutoff = now - window_sec
        while self.trades and self.trades[0].ts < cutoff:
            self.trades.popleft()

    def volumes(self, window_sec: float, now: float | None = None) -> tuple[float, float]:
        now = now or time.time()
        cutoff = now - window_sec
        buy_v = 0.0
        sell_v = 0.0
        for t in self.trades:
            if t.ts < cutoff:
                continue
            if t.side == "buy":
                buy_v += t.qty
            else:
                sell_v += t.qty
        return buy_v, sell_v

    def to_dict(self, window_sec: float) -> dict[str, Any]:
        now = time.time()
        buy_v, sell_v = self.volumes(window_sec, now)
        ratio = (buy_v / sell_v) if sell_v > 0 else (99.0 if buy_v > 0 else 1.0)
        return {
            "buy_vol": round(buy_v, 6),
            "sell_vol": round(sell_v, 6),
            "flow_ratio": round(ratio, 3),
            "window_sec": window_sec,
        }
