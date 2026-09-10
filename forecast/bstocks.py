"""Детектор токенизированных акций Binance (bStocks, пары *B/USDT)."""

from __future__ import annotations

from typing import Any, Iterable

# Известные крипто-базы / не-акции, которые заканчиваются на B
CRYPTO_FALSE_POSITIVES = frozenset(
    {
        "SHIB",
        "BNB",
        "ARB",
        "WBTC",
        "HBTC",
        "OBTC",
        "CBETH",
        "TBTC",
        "CKB",
        "DGB",
        "TRB",
        "QNTB",  # Quant (крипто), не акция
        "BB",  # BounceBit
        "BEB",
        "GSB",
        "YB",
        "SMHB",
        "STXB",
    }
)


def is_bstock_base(base: str) -> bool:
    b = (base or "").upper()
    if b in CRYPTO_FALSE_POSITIVES or not b.endswith("B"):
        return False
    ticker = b[:-1]
    # Тикеры акций обычно 2–5 букв
    if not (2 <= len(ticker) <= 5 and ticker.isalpha()):
        return False
    if ticker in CRYPTO_FALSE_POSITIVES:
        return False
    return True


def is_bstock_symbol(symbol: str) -> bool:
    return is_bstock_base((symbol or "").split("/")[0])


def without_bstocks(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(s for s in symbols if not is_bstock_symbol(str(s)))


def drop_bstock_setups(setups: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Убрать bStocks из списка сетапов крипто-скана."""
    return [s for s in (setups or []) if not is_bstock_symbol(str(s.get("symbol") or ""))]
