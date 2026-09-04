"""Reference prices from external marketplaces."""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import CACHE_DIR, ScannerConfig
from .http_utils import SKINPORT_HEADERS, fetch_json

CACHE_MAX_AGE_SEC = 30 * 60


def _read_price_tables(cache_path: Path) -> tuple[dict[str, float], dict[str, int]] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(payload, dict) and "prices" in payload:
        prices = {str(k): float(v) for k, v in payload["prices"].items()}
        quantities = {str(k): int(v) for k, v in payload.get("quantities", {}).items()}
        return prices, quantities
    if isinstance(payload, dict):
        prices = {str(k): float(v) for k, v in payload.items()}
        return prices, {}
    return None


def _write_price_tables(
    cache_path: Path,
    prices: dict[str, float],
    quantities: dict[str, int],
) -> None:
    cache_path.write_text(
        json.dumps({"prices": prices, "quantities": quantities}),
        encoding="utf-8",
    )


def _load_cached_or_fetch(
    label: str,
    cache_path: Path,
    fetcher: Callable[[], tuple[dict[str, float], dict[str, int]]],
) -> tuple[dict[str, float], dict[str, int]]:
    import time as time_mod

    cached = _read_price_tables(cache_path)
    if cached and cache_path.exists():
        age = time_mod.time() - cache_path.stat().st_mtime
        if age <= CACHE_MAX_AGE_SEC:
            print(f"  {label}: cache hit ({len(cached[0])} items, {int(age)}s old)")
            return cached

    try:
        return fetcher()
    except Exception as exc:
        if cached:
            print(f"  {label}: fetch failed ({exc}); using cached data ({len(cached[0])} items)")
            return cached
        raise


@dataclass
class ReferencePrices:
    csfloat: dict[str, float] = field(default_factory=dict)
    skinport: dict[str, float] = field(default_factory=dict)
    buff: dict[str, float] = field(default_factory=dict)
    steam: dict[str, float] = field(default_factory=dict)
    csfloat_qty: dict[str, int] = field(default_factory=dict)
    skinport_qty: dict[str, int] = field(default_factory=dict)

    def best_reference(self, market_hash_name: str) -> tuple[float | None, list[str]]:
        refs: list[tuple[str, float]] = []
        for source, table in (
            ("csfloat", self.csfloat),
            ("skinport", self.skinport),
            ("buff", self.buff),
            ("steam", self.steam),
        ):
            price = table.get(market_hash_name)
            if price is not None and price > 0:
                refs.append((source, price))
        if not refs:
            return None, []
        refs.sort(key=lambda item: item[1], reverse=True)
        return refs[0][1], [name for name, _ in refs]

    def conservative_reference(self, market_hash_name: str) -> tuple[float | None, list[str]]:
        refs: list[tuple[str, float]] = []
        for source, table in (
            ("csfloat", self.csfloat),
            ("skinport", self.skinport),
            ("buff", self.buff),
            ("steam", self.steam),
        ):
            price = table.get(market_hash_name)
            if price is not None and price > 0:
                refs.append((source, price))
        if not refs:
            return None, []
        refs.sort(key=lambda item: item[1])
        return refs[0][1], [f"{name}:${price:.2f}" for name, price in refs]

    def reference_median(self, market_hash_name: str) -> float | None:
        values = [
            price
            for price in (
                self.csfloat.get(market_hash_name),
                self.skinport.get(market_hash_name),
                self.buff.get(market_hash_name),
                self.steam.get(market_hash_name),
            )
            if price is not None and price > 0
        ]
        if not values:
            return None
        values.sort()
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2


def load_csfloat_prices() -> tuple[dict[str, float], dict[str, int]]:
    cache_path = CACHE_DIR / "csfloat_prices.json"

    def _fetch() -> tuple[dict[str, float], dict[str, int]]:
        data = fetch_json("https://csfloat.com/api/v1/listings/price-list", timeout=90)
        prices = {row["market_hash_name"]: row["min_price"] / 100 for row in data}
        quantities = {row["market_hash_name"]: int(row.get("quantity") or 0) for row in data}
        _write_price_tables(cache_path, prices, quantities)
        return prices, quantities

    return _load_cached_or_fetch("CSFloat", cache_path, _fetch)


def load_skinport_prices(currency: str = "USD") -> tuple[dict[str, float], dict[str, int]]:
    cache_path = CACHE_DIR / f"skinport_{currency.lower()}.json"
    url = (
        "https://api.skinport.com/v1/items"
        f"?app_id=730&currency={currency}&tradable=1"
    )

    def _fetch() -> tuple[dict[str, float], dict[str, int]]:
        data = fetch_json(url, headers=SKINPORT_HEADERS, timeout=120, retries=5, retry_delay=2.0)
        prices: dict[str, float] = {}
        quantities: dict[str, int] = {}
        for row in data:
            min_price = row.get("min_price")
            if min_price is not None and min_price > 0:
                name = row["market_hash_name"]
                prices[name] = float(min_price)
                quantities[name] = int(row.get("quantity") or 0)
        _write_price_tables(cache_path, prices, quantities)
        return prices, quantities

    return _load_cached_or_fetch("Skinport", cache_path, _fetch)


def load_buff_prices(config: ScannerConfig, names: set[str] | None = None) -> dict[str, float]:
    if not config.buff_session_cookie:
        return {}

    prices: dict[str, float] = {}
    headers = {
        "Cookie": config.buff_session_cookie,
        "Referer": "https://buff.163.com/market/csgo",
    }

    if names:
        for name in sorted(names):
            url = (
                "https://buff.163.com/api/market/goods"
                f"?game=csgo&search={urllib.parse.quote(name)}&page_num=1&page_size=20"
            )
            payload = fetch_json(url, headers=headers, timeout=30)
            if payload.get("code") != "OK":
                continue
            for item in payload.get("data", {}).get("items", []):
                if item.get("market_hash_name") != name:
                    continue
                cny = float(item.get("sell_min_price") or 0)
                if cny > 0:
                    prices[name] = cny / config.cny_per_usd
            time.sleep(0.35)
        return prices

    page = 1
    while page <= 40:
        url = (
            "https://buff.163.com/api/market/goods"
            f"?game=csgo&page_num={page}&page_size=80&use_suggestion=0"
        )
        payload = fetch_json(url, headers=headers, timeout=30)
        if payload.get("code") != "OK":
            break
        items = payload.get("data", {}).get("items", [])
        if not items:
            break
        for item in items:
            cny = float(item.get("sell_min_price") or 0)
            if cny > 0:
                prices[item["market_hash_name"]] = cny / config.cny_per_usd
        page += 1
        time.sleep(0.35)
    return prices


def parse_steam_price(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[^\d.,]", "", value).replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_steam_price(market_hash_name: str, delay_sec: float = 3.5) -> float | None:
    encoded = urllib.parse.quote(market_hash_name)
    url = (
        "https://steamcommunity.com/market/priceoverview/"
        f"?appid=730&currency=1&market_hash_name={encoded}"
    )
    payload = fetch_json(url, timeout=30, retries=2)
    time.sleep(delay_sec)
    if not payload.get("success"):
        return None
    return parse_steam_price(payload.get("lowest_price") or payload.get("median_price"))


def load_reference_prices(config: ScannerConfig) -> ReferencePrices:
    print("Loading CSFloat prices...")
    csfloat, csfloat_qty = load_csfloat_prices()
    print(f"  CSFloat: {len(csfloat)} items")

    print("Loading Skinport prices...")
    skinport, skinport_qty = load_skinport_prices(config.currency)
    print(f"  Skinport: {len(skinport)} items")

    buff: dict[str, float] = {}
    if config.buff_session_cookie:
        print("Loading Buff prices (session cookie)...")
        buff = load_buff_prices(config)
        print(f"  Buff: {len(buff)} items")
    else:
        print("Buff skipped (set BUFF_SESSION_COOKIE to enable)")

    return ReferencePrices(
        csfloat=csfloat,
        skinport=skinport,
        buff=buff,
        csfloat_qty=csfloat_qty,
        skinport_qty=skinport_qty,
    )
