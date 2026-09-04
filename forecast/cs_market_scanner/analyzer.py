"""Deal scoring for market.csgo.com listings."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .config import ScannerConfig
from .reference_prices import ReferencePrices, fetch_steam_price
from .tm_liquidity import TmLiquidity, check_tm_liquidity
from .tm_listings import TmListing

WEAR_PATTERN = re.compile(
    r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)$"
)

PHASE_PREMIUM = {
    "phase1": 1.02,
    "phase2": 1.02,
    "phase3": 1.02,
    "phase4": 1.02,
    "ruby": 1.85,
    "sapphire": 1.75,
    "emerald": 1.55,
    "blackpearl": 2.1,
    "black pearl": 2.1,
}

FLOAT_BONUS_PCT = {
    "god": 18.0,
    "low": 8.0,
    "clean": 4.0,
}

SKIP_PREFIXES = (
    "Sticker |",
    "Sealed Graffiti",
    "Charm |",
    "Souvenir Charm",
    "Patch |",
    "Music Kit |",
    "Agent |",
    "Pin |",
    "Case",
    "Capsule",
    "Package",
    "Graffiti",
)


def is_weapon_like_listing(listing: TmListing) -> bool:
    name = listing.market_hash_name
    if any(name.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    if listing.item_type in {"Sticker", "Container", "Graffiti", "Agent", "Patch", "Music Kit"}:
        return False
    return bool(WEAR_PATTERN.search(name) or name.startswith("★") or "Doppler" in name)


def reference_is_trustworthy(
    market_hash_name: str,
    ref_price: float,
    tm_price: float,
    references: ReferencePrices,
    config: ScannerConfig,
) -> bool:
    csf_qty = references.csfloat_qty.get(market_hash_name, 0)
    sp_qty = references.skinport_qty.get(market_hash_name, 0)
    source_count = sum(
        1
        for price in (
            references.csfloat.get(market_hash_name),
            references.skinport.get(market_hash_name),
            references.buff.get(market_hash_name),
            references.steam.get(market_hash_name),
        )
        if price is not None and price > 0
    )
    discount = (ref_price - tm_price) / tm_price * 100
    if discount > config.max_discount_pct and source_count < 2:
        return False
    if discount > 40 and csf_qty < 3 and sp_qty < 2:
        return False
    median = references.reference_median(market_hash_name)
    if median is not None and ref_price > median * 1.8:
        return False
    return True


@dataclass
class Deal:
    listing: TmListing
    reference_price: float
    discount_pct: float
    profit_usd: float
    score: float
    references: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    tm_buy_price: float | None = None
    tm_buy_volume: int = 0
    tm_spread_pct: float | None = None
    tm_bid_ratio: float | None = None

    def to_row(self) -> dict:
        return {
            "score": round(self.score, 2),
            "discount_pct": round(self.discount_pct, 2),
            "tm_price": round(self.listing.price_usd, 2),
            "reference_price": round(self.reference_price, 2),
            "profit_usd": round(self.profit_usd, 2),
            "name": self.listing.display_name,
            "float": self.listing.float_value,
            "phase": self.listing.phase,
            "stickers": self.listing.sticker_count,
            "transfer_chance": self.listing.chance_to_transfer,
            "tm_buy_price": self.tm_buy_price,
            "tm_buy_volume": self.tm_buy_volume,
            "tm_spread_pct": round(self.tm_spread_pct, 1) if self.tm_spread_pct is not None else None,
            "tm_bid_ratio": round(self.tm_bid_ratio * 100, 1) if self.tm_bid_ratio is not None else None,
            "references": ", ".join(self.references),
            "reasons": "; ".join(self.reasons),
            "url": self.listing.tm_url,
            "image_url": self.listing.image_url,
        }


def extract_wear(market_hash_name: str) -> str | None:
    match = WEAR_PATTERN.search(market_hash_name)
    return match.group(1) if match else None


def classify_float(wear: str | None, float_value: float | None) -> str | None:
    if wear is None or float_value is None:
        return None
    if wear == "Factory New":
        if float_value < 0.01:
            return "god"
        if float_value < 0.03:
            return "low"
        if float_value < 0.05:
            return "clean"
    if wear == "Minimal Wear" and float_value < 0.08:
        return "low"
    if wear == "Field-Tested" and float_value < 0.18:
        return "clean"
    return None


def phase_reference_names(market_hash_name: str, phase: str | None) -> list[str]:
    if not phase:
        return [market_hash_name]
    phase_key = phase.strip().lower()
    suffix = phase if phase_key not in {"blackpearl", "black pearl"} else "Black Pearl"
    return [
        f"{market_hash_name} {suffix}",
        f"{market_hash_name} ({suffix})",
        market_hash_name,
    ]


def lookup_reference_price(
    market_hash_name: str,
    phase: str | None,
    references: ReferencePrices,
) -> tuple[float | None, list[str], list[str]]:
    reasons: list[str] = []
    for candidate in phase_reference_names(market_hash_name, phase):
        price, sources = references.conservative_reference(candidate)
        if price is not None:
            if candidate != market_hash_name:
                reasons.append(f"matched:{candidate}")
            return price, sources, reasons

    if phase and "Doppler" in market_hash_name:
        base_price, sources = references.conservative_reference(market_hash_name)
        if base_price is not None:
            mult = PHASE_PREMIUM.get(phase.lower(), 1.12)
            reasons.append(f"phase_estimated:{phase}x{mult:.2f}")
            return base_price * mult, sources, reasons
    return None, [], reasons


def score_listing(
    listing: TmListing,
    references: ReferencePrices,
    config: ScannerConfig,
    liquidity: TmLiquidity | None = None,
) -> Deal | None:
    if config.weapons_only and not is_weapon_like_listing(listing):
        return None
    if listing.price_usd < config.min_price_usd or listing.price_usd > config.max_price_usd:
        return None
    if listing.chance_to_transfer is not None and listing.chance_to_transfer < config.min_transfer_chance:
        return None

    tm_buy_price: float | None = None
    tm_buy_volume = 0
    tm_spread_pct: float | None = None
    tm_bid_ratio: float | None = None

    if liquidity is not None:
        ok, buy, liquidity_note = check_tm_liquidity(
            listing.price_usd,
            listing.market_hash_name,
            listing.phase,
            liquidity,
            config,
        )
        if not ok:
            return None
        if buy is not None:
            tm_buy_price = buy.price
            tm_buy_volume = buy.volume
            tm_bid_ratio = buy.price / listing.price_usd if listing.price_usd > 0 else None
            tm_spread_pct = (
                (listing.price_usd - buy.price) / listing.price_usd * 100
                if listing.price_usd > 0
                else None
            )
    else:
        liquidity_note = ""

    ref_price, ref_sources, ref_reasons = lookup_reference_price(
        listing.market_hash_name,
        listing.phase,
        references,
    )
    if ref_price is None or ref_price <= 0:
        return None
    if not reference_is_trustworthy(
        listing.market_hash_name,
        ref_price,
        listing.price_usd,
        references,
        config,
    ):
        return None

    reasons = list(ref_reasons)
    bonus_pct = 0.0

    wear = extract_wear(listing.market_hash_name)
    float_tier = classify_float(wear, listing.float_value)
    if float_tier:
        bonus = FLOAT_BONUS_PCT[float_tier]
        bonus_pct += bonus
        reasons.append(f"float:{float_tier}({listing.float_value:.5f})")

    if listing.sticker_count > 0:
        reasons.append(f"stickers:{listing.sticker_count}")
        if listing.sticker_count >= 4:
            bonus_pct += 3.0

    if listing.phase:
        reasons.append(f"phase:{listing.phase}")

    if liquidity_note:
        reasons.append(liquidity_note)

    adjusted_reference = ref_price * (1 + bonus_pct / 100)
    profit = adjusted_reference - listing.price_usd
    discount_pct = profit / listing.price_usd * 100
    if discount_pct < config.min_discount_pct:
        return None

    score = discount_pct + (bonus_pct * 0.35)
    if listing.old_price_usd and listing.old_price_usd > listing.price_usd:
        drop_pct = (listing.old_price_usd - listing.price_usd) / listing.old_price_usd * 100
        if drop_pct >= 8:
            score += 2
            reasons.append(f"recent_drop:{drop_pct:.0f}%")

    return Deal(
        listing=listing,
        reference_price=adjusted_reference,
        discount_pct=discount_pct,
        profit_usd=profit,
        score=score,
        references=ref_sources,
        reasons=reasons,
        tm_buy_price=tm_buy_price,
        tm_buy_volume=tm_buy_volume,
        tm_spread_pct=tm_spread_pct,
        tm_bid_ratio=tm_bid_ratio,
    )


def enrich_top_deals_with_steam(
    deals: list[Deal],
    references: ReferencePrices,
    config: ScannerConfig,
    liquidity: TmLiquidity | None = None,
    price_weight: float = 1.0,
) -> None:
    unique_names = sorted({deal.listing.market_hash_name for deal in deals})
    print(f"Fetching Steam prices for {len(unique_names)} shortlisted items...")
    for name in unique_names:
        if name in references.steam:
            continue
        price = fetch_steam_price(name, delay_sec=config.steam_delay_sec)
        if price is not None:
            references.steam[name] = price

    rescored: list[Deal] = []
    for deal in deals:
        updated = score_listing(deal.listing, references, config, liquidity)
        if updated is not None:
            rescored.append(updated)

    def _rank(deal: Deal) -> float:
        price_factor = 1.0 + price_weight * math.log10(max(deal.listing.price_usd, 1.0))
        return deal.score * price_factor

    deals[:] = sorted(rescored, key=_rank, reverse=True)
