from __future__ import annotations

import os
from dataclasses import dataclass

import ccxt
from ccxt.base.errors import AuthenticationError
from dotenv import load_dotenv


@dataclass
class BinanceConfig:
    symbol: str
    timeframe: str = "1h"
    use_futures: bool = False
    limit: int = 1000


def _load_env() -> None:
    load_dotenv(override=False)


def create_binance_client(use_futures: bool = False, use_credentials: bool = True) -> ccxt.Exchange:
    _load_env()
    exchange_class = ccxt.binanceusdm if use_futures else ccxt.binance
    params = {
        "enableRateLimit": True,
    }
    if use_credentials:
        api_key = os.getenv("BINANCE_API_KEY")
        api_secret = os.getenv("BINANCE_API_SECRET")
        if api_key and api_secret:
            params.update({"apiKey": api_key, "secret": api_secret})

    return exchange_class(params)


def fetch_ohlcv(
    cfg: BinanceConfig,
) -> list[list[float]]:
    try:
        client = create_binance_client(use_futures=cfg.use_futures, use_credentials=True)
        return client.fetch_ohlcv(
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            limit=cfg.limit,
        )
    except AuthenticationError:
        # Если ключи неверные, пробуем публичный доступ без авторизации.
        client = create_binance_client(use_futures=cfg.use_futures, use_credentials=False)
        return client.fetch_ohlcv(
            symbol=cfg.symbol,
            timeframe=cfg.timeframe,
            limit=cfg.limit,
        )

