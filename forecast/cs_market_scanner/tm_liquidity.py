"""TM buy-order liquidity from public orders API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .config import CACHE_DIR, ScannerConfig
from .http_utils import fetch_json
from .reference_prices import CACHE_MAX_AGE_SEC


def _phase_reference_names(market_hash_name: str, phase: str | None) -> list[str]:
    if not phase:
        return [market_hash_name]
    phase_key = phase.strip().lower()
    suffix = phase if phase_key not in {"blackpearl", "black pearl"} else "Black Pearl"
    return [
        f"{market_hash_name} {suffix}",
        f"{market_hash_name} ({suffix})",
        market_hash_name,
    ]


@dataclass(frozen=True)
class TmBuyOrder:
    price: float
    volume: int


@dataclass
class TmLiquidity:
    orders: dict[str, TmBuyOrder]

    def lookup(self, market_hash_name: str, phase: str | None = None) -> TmBuyOrder | None:
        for candidate in _phase_reference_names(market_hash_name, phase):
            hit = self.orders.get(candidate)
            if hit is not None and hit.price > 0:
                return hit
        return None


def load_tm_buy_orders(currency: str) -> TmLiquidity:
    cache_path = CACHE_DIR / f"tm_buy_orders_{currency.lower()}.json"

    def _read_cache() -> TmLiquidity | None:
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        orders = {
            str(name): TmBuyOrder(price=float(data["price"]), volume=int(data["volume"]))
            for name, data in payload.items()
        }
        return TmLiquidity(orders=orders)

    cached = _read_cache()
    if cached and (time.time() - cache_path.stat().st_mtime) <= CACHE_MAX_AGE_SEC:
        print(f"  TM buy orders: cache hit ({len(cached.orders)} items)")
        return cached

    try:
        payload = fetch_json(
            f"https://market.csgo.com/api/v2/prices/orders/{currency}.json",
            timeout=120,
        )
        orders: dict[str, TmBuyOrder] = {}
        for row in payload.get("items", []):
            name = row.get("market_hash_name")
            price = row.get("price")
            if not name or price is None:
                continue
            orders[str(name)] = TmBuyOrder(
                price=float(price),
                volume=int(row.get("volume") or 0),
            )
        cache_path.write_text(
            json.dumps({k: {"price": v.price, "volume": v.volume} for k, v in orders.items()}),
            encoding="utf-8",
        )
        return TmLiquidity(orders=orders)
    except Exception as exc:
        if cached is not None:
            print(f"  TM buy orders: fetch failed ({exc}); using cache")
            return cached
        raise


def check_tm_liquidity(
    ask_price: float,
    market_hash_name: str,
    phase: str | None,
    liquidity: TmLiquidity,
    config: ScannerConfig,
) -> tuple[bool, TmBuyOrder | None, str]:
    if not config.require_tm_liquidity:
        return True, liquidity.lookup(market_hash_name, phase), ""

    buy = liquidity.lookup(market_hash_name, phase)
    if buy is None or buy.price <= 0:
        return False, buy, "no_tm_buyers"

    if buy.volume < config.min_buy_order_volume:
        return False, buy, f"buy_volume:{buy.volume}"

    bid_ratio = buy.price / ask_price if ask_price > 0 else 0.0
    spread_pct = (ask_price - buy.price) / ask_price * 100 if ask_price > 0 else 100.0

    if bid_ratio < config.min_bid_ratio:
        return False, buy, f"bid_ratio:{bid_ratio:.1%}"

    if spread_pct > config.max_spread_pct:
        return False, buy, f"spread:{spread_pct:.0f}%"

    return (
        True,
        buy,
        f"tm_bid:{buy.price:.2f}×{buy.volume},spread:{spread_pct:.0f}%",
    )
