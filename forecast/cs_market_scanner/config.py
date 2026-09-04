"""Configuration for CS Market deal scanner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PACKAGE_DIR / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ScannerConfig:
    currency: str = "USD"
    min_discount_pct: float = 12.0
    min_price_usd: float = 3.0
    max_price_usd: float = 2000.0
    min_transfer_chance: int = 70
    top_n: int = 40
    max_chunks: int | None = None
    steam_delay_sec: float = 3.5
    tm_chunk_workers: int = 3
    cny_per_usd: float = 7.25
    buff_session_cookie: str | None = None
    tm_api_key: str | None = None
    weapons_only: bool = True
    max_discount_pct: float = 65.0
    require_tm_liquidity: bool = True
    min_bid_ratio: float = 0.20
    max_spread_pct: float = 70.0
    min_buy_order_volume: int = 1

    @classmethod
    def from_env(cls) -> "ScannerConfig":
        return cls(
            currency=os.getenv("CS_SCANNER_CURRENCY", "USD").upper(),
            min_discount_pct=float(os.getenv("CS_SCANNER_MIN_DISCOUNT", "12")),
            min_price_usd=float(os.getenv("CS_SCANNER_MIN_PRICE", "3")),
            max_price_usd=float(os.getenv("CS_SCANNER_MAX_PRICE", "2000")),
            min_transfer_chance=int(os.getenv("CS_SCANNER_MIN_TRANSFER", "70")),
            top_n=int(os.getenv("CS_SCANNER_TOP_N", "40")),
            max_chunks=int(os.getenv("CS_SCANNER_MAX_CHUNKS", "0")) or None,
            steam_delay_sec=float(os.getenv("CS_SCANNER_STEAM_DELAY", "3.5")),
            cny_per_usd=float(os.getenv("CS_SCANNER_CNY_USD", "7.25")),
            buff_session_cookie=os.getenv("BUFF_SESSION_COOKIE"),
            tm_api_key=os.getenv("TM_API_KEY"),
            weapons_only=os.getenv("CS_SCANNER_WEAPONS_ONLY", "1") != "0",
            max_discount_pct=float(os.getenv("CS_SCANNER_MAX_DISCOUNT", "65")),
            require_tm_liquidity=os.getenv("CS_SCANNER_REQUIRE_LIQUIDITY", "1") != "0",
            min_bid_ratio=float(os.getenv("CS_SCANNER_MIN_BID_RATIO", "0.20")),
            max_spread_pct=float(os.getenv("CS_SCANNER_MAX_SPREAD", "70")),
            min_buy_order_volume=int(os.getenv("CS_SCANNER_MIN_BUY_VOLUME", "1")),
        )
