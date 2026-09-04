"""market.csgo.com listing loader."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterator

from .config import ScannerConfig
from .http_utils import fetch_json
from .images import skin_image_url

TM_USD_DIVISOR = 1000
TM_RUB_DIVISOR = 100
TM_EUR_DIVISOR = 1000

DIVISORS = {"USD": TM_USD_DIVISOR, "RUB": TM_RUB_DIVISOR, "EUR": TM_EUR_DIVISOR}


@dataclass
class TmListing:
    listing_id: int
    market_hash_name: str
    price_usd: float
    float_value: float | None
    phase: str | None
    paintseed: int | None
    stickers_raw: str | None
    chance_to_transfer: int | None
    source: str | None
    old_price_usd: float | None
    item_type: str | None
    classid: int
    instanceid: int

    @property
    def image_url(self) -> str:
        return skin_image_url(self.classid, self.instanceid)

    @property
    def sticker_count(self) -> int:
        if not self.stickers_raw:
            return 0
        return len([part for part in str(self.stickers_raw).split("|") if part.strip()])

    @property
    def display_name(self) -> str:
        if self.phase:
            return f"{self.market_hash_name} [{self.phase}]"
        return self.market_hash_name

    @property
    def tm_url(self) -> str:
        return f"https://market.csgo.com/en/item/{self.listing_id}"


def _row_to_listing(row: list[Any], fmt: list[str], divisor: int) -> TmListing:
    data = dict(zip(fmt, row))
    float_raw = data.get("float")
    float_value = float(float_raw) if float_raw is not None else None
    old_price = data.get("old_price")
    return TmListing(
        listing_id=int(data["id"]),
        market_hash_name=str(data["market_hash_name"]),
        price_usd=float(data["price"]) / divisor,
        float_value=float_value,
        phase=(str(data["phase"]).strip() or None) if data.get("phase") else None,
        paintseed=int(data["paintseed"]) if data.get("paintseed") not in (None, "") else None,
        stickers_raw=str(data["stickers"]) if data.get("stickers") not in (None, "") else None,
        chance_to_transfer=int(data["chance_to_transfer"])
        if data.get("chance_to_transfer") is not None
        else None,
        source=str(data.get("source") or ""),
        old_price_usd=float(old_price) / divisor if old_price else None,
        item_type=str(data.get("type") or ""),
        classid=int(data["classid"]),
        instanceid=int(data["real_instance"] or data["instanceid"]),
    )


def load_export_index(currency: str) -> tuple[list[str], list[str]]:
    payload = fetch_json(f"https://market.csgo.com/api/full-export/{currency}.json", timeout=60)
    return payload["items"], payload["format"]


def load_chunk(chunk_name: str, fmt: list[str], divisor: int) -> list[TmListing]:
    rows = fetch_json(f"https://market.csgo.com/api/full-export/{chunk_name}", timeout=120)
    return [_row_to_listing(row, fmt, divisor) for row in rows]


def iter_tm_listings(config: ScannerConfig) -> Iterator[TmListing]:
    currency = config.currency
    divisor = DIVISORS.get(currency, TM_USD_DIVISOR)
    chunks, fmt = load_export_index(currency)
    if config.max_chunks is not None:
        chunks = chunks[: config.max_chunks]

    print(f"Scanning {len(chunks)} TM chunks ({currency})...")

    with ThreadPoolExecutor(max_workers=config.tm_chunk_workers) as pool:
        futures = {pool.submit(load_chunk, chunk, fmt, divisor): chunk for chunk in chunks}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 10 == 0 or done == len(chunks):
                print(f"  loaded chunks: {done}/{len(chunks)}")
            for listing in future.result():
                yield listing
