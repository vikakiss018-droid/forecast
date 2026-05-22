from __future__ import annotations

import os
from dataclasses import dataclass

import ccxt
from ccxt.base.errors import AuthenticationError
from .paths import load_project_env


@dataclass
class BinanceConfig:
    symbol: str
    timeframe: str = "1h"
    use_futures: bool = False
    limit: int = 1000


def _load_env() -> None:
    load_project_env()


def _resolve_api_credentials(*, for_trading: bool) -> tuple[str | None, str | None]:
    """Trading uses BINANCE_TRADE_* if set, else falls back to BINANCE_*."""
    if for_trading:
        key = (os.getenv("BINANCE_TRADE_API_KEY") or "").strip()
        secret = (os.getenv("BINANCE_TRADE_API_SECRET") or "").strip()
        if key and secret:
            return key, secret
    key = (os.getenv("BINANCE_API_KEY") or "").strip()
    secret = (os.getenv("BINANCE_API_SECRET") or "").strip()
    return (key or None), (secret or None)


def create_binance_client(
    use_futures: bool = False,
    use_credentials: bool = True,
    *,
    for_trading: bool = False,
) -> ccxt.Exchange:
    _load_env()
    exchange_class = ccxt.binanceusdm if use_futures else ccxt.binance
    params: dict = {
        "enableRateLimit": True,
    }
    if use_credentials:
        api_key, api_secret = _resolve_api_credentials(for_trading=for_trading)
        if api_key and api_secret:
            params.update({"apiKey": api_key, "secret": api_secret})
    if use_futures:
        params.setdefault("options", {})["defaultType"] = "future"

    return exchange_class(params)


def trading_credentials_source() -> str:
    """Which env vars supply keys for auto-trade (no secrets returned)."""
    _load_env()
    key, secret = _resolve_api_credentials(for_trading=True)
    if not key or not secret:
        return "none"
    trade_key = (os.getenv("BINANCE_TRADE_API_KEY") or "").strip()
    trade_secret = (os.getenv("BINANCE_TRADE_API_SECRET") or "").strip()
    if trade_key and trade_secret:
        return "BINANCE_TRADE_*"
    return "BINANCE_*"


def create_trading_client(*, use_futures: bool = True) -> ccxt.Exchange:
    """Futures client with trade-only API keys (BINANCE_TRADE_*)."""
    return create_binance_client(use_futures=use_futures, use_credentials=True, for_trading=True)


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

