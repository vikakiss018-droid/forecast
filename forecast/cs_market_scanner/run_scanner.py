#!/usr/bin/env python3
"""Scan market.csgo.com for undervalued listings."""

from __future__ import annotations

import argparse
import json
import math
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cs_market_scanner.analyzer import enrich_top_deals_with_steam, score_listing, WEAR_PATTERN
    from cs_market_scanner.config import CACHE_DIR, ScannerConfig
    from cs_market_scanner.html_report import generate_html_report
    from cs_market_scanner.reference_prices import load_buff_prices, load_reference_prices
    from cs_market_scanner.tm_liquidity import load_tm_buy_orders
    from cs_market_scanner.tm_listings import iter_tm_listings
else:
    from .analyzer import enrich_top_deals_with_steam, score_listing, WEAR_PATTERN
    from .config import CACHE_DIR, ScannerConfig
    from .html_report import generate_html_report
    from .reference_prices import load_buff_prices, load_reference_prices
    from .tm_liquidity import load_tm_buy_orders
    from .tm_listings import iter_tm_listings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find undervalued CS2 listings on market.csgo.com",
    )
    parser.add_argument("--currency", default="USD", choices=["USD", "RUB", "EUR"])
    parser.add_argument("--min-discount", type=float, default=12.0)
    parser.add_argument("--min-price", type=float, default=3.0)
    parser.add_argument("--max-price", type=float, default=2000.0)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--max-chunks", type=int, default=0, help="0 = all chunks")
    parser.add_argument("--with-steam", action="store_true", help="Fetch Steam prices for top deals")
    parser.add_argument("--include-all", action="store_true", help="Include stickers, cases, charms")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--html", action="store_true", help="Save HTML report with skin images")
    parser.add_argument(
        "--html-out",
        default="",
        help="HTML output path (default: data/reports/deals_YYYYMMDD_HHMMSS.html)",
    )
    parser.add_argument("--open", action="store_true", help="Open HTML report in browser")
    parser.add_argument("--no-buff-lookup", action="store_true")
    parser.add_argument(
        "--no-liquidity-filter",
        action="store_true",
        help="Disable TM buy-order liquidity filter (arb-only mode)",
    )
    parser.add_argument(
        "--min-bid-ratio",
        type=float,
        default=0.20,
        help="Min TM buy-order / ask ratio (default 0.20 = 20%%)",
    )
    parser.add_argument(
        "--max-spread",
        type=float,
        default=70.0,
        help="Max spread between TM ask and best buy-order in percent",
    )
    parser.add_argument(
        "--price-weight",
        type=float,
        default=1.0,
        help="How strongly to prioritize higher-priced items (0 = ignore price, 1 = default)",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep duplicate listings of the same item (default: keep only best per item)",
    )
    return parser.parse_args()


def ranking_value(deal, price_weight: float = 1.0) -> float:
    """Composite rank: discount score boosted by item price (log-scaled)."""
    price_factor = 1.0 + price_weight * math.log10(max(deal.listing.price_usd, 1.0))
    return deal.score * price_factor


def dedupe_deals(deals: list, price_weight: float = 1.0) -> list:
    """Keep only the best-ranked deal per (market_hash_name, phase)."""
    best: dict[tuple[str, str], object] = {}
    for deal in deals:
        key = (deal.listing.market_hash_name, deal.listing.phase or "")
        current = best.get(key)
        if current is None or ranking_value(deal, price_weight) > ranking_value(current, price_weight):
            best[key] = deal
    return list(best.values())


def print_table(deals: list) -> None:
    if not deals:
        print("No deals matched the filters.")
        return

    headers = [
        ("score", 6),
        ("disc%", 7),
        ("tm$", 8),
        ("ref$", 8),
        ("profit", 8),
        ("float", 8),
        ("name", 42),
    ]
    line = " ".join(title.ljust(width) for title, width in headers)
    print(line)
    print("-" * len(line))
    for deal in deals:
        row = deal.to_row()
        float_text = "-"
        if WEAR_PATTERN.search(row["name"]) and row["float"] is not None:
            float_text = f"{row['float']:.4f}"
        print(
            f"{row['score']:<6.1f} "
            f"{row['discount_pct']:<7.1f} "
            f"{row['tm_price']:<8.2f} "
            f"{row['reference_price']:<8.2f} "
            f"{row['profit_usd']:<8.2f} "
            f"{float_text:<8} "
            f"{row['name'][:42]:<42}"
        )
        print(f"       refs: {row['references']}")
        if row.get("tm_buy_price") is not None:
            print(
                f"       tm bid: {row['tm_buy_price']:.2f} "
                f"(vol {row['tm_buy_volume']}, "
                f"spread {row['tm_spread_pct']:.1f}%, "
                f"bid/ask {row['tm_bid_ratio']:.1f}%)"
            )
        print(f"       why:  {row['reasons']}")
        print(f"       url:  {row['url']}")
        print()


def main() -> int:
    args = parse_args()
    config = ScannerConfig.from_env()
    config.currency = args.currency.upper()
    config.min_discount_pct = args.min_discount
    config.min_price_usd = args.min_price
    config.max_price_usd = args.max_price
    config.top_n = args.top
    config.max_chunks = args.max_chunks or None
    config.weapons_only = not args.include_all
    config.require_tm_liquidity = not args.no_liquidity_filter
    config.min_bid_ratio = args.min_bid_ratio
    config.max_spread_pct = args.max_spread

    price_weight = args.price_weight
    dedupe = not args.no_dedupe

    references = load_reference_prices(config)

    liquidity = None
    if config.require_tm_liquidity:
        print("Loading TM buy orders (liquidity)...")
        liquidity = load_tm_buy_orders(config.currency)
        print(f"  TM buy orders: {len(liquidity.orders)} items")
    else:
        print("TM liquidity filter disabled")

    candidates: list = []
    scanned = 0
    for listing in iter_tm_listings(config):
        scanned += 1
        deal = score_listing(listing, references, config, liquidity)
        if deal is None:
            continue
        candidates.append(deal)
        if len(candidates) > config.top_n * 20:
            if dedupe:
                candidates = dedupe_deals(candidates, price_weight)
            candidates.sort(key=lambda item: ranking_value(item, price_weight), reverse=True)
            candidates = candidates[: config.top_n * 8]

    if dedupe:
        candidates = dedupe_deals(candidates, price_weight)
    candidates.sort(key=lambda item: ranking_value(item, price_weight), reverse=True)
    top = candidates[: config.top_n]

    if (
        not args.no_buff_lookup
        and config.buff_session_cookie
        and top
    ):
        names = {deal.listing.market_hash_name for deal in top}
        buff_prices = load_buff_prices(config, names)
        references.buff.update(buff_prices)
        rescored = []
        for listing in (deal.listing for deal in top):
            updated = score_listing(listing, references, config, liquidity)
            if updated is not None:
                rescored.append(updated)
        if dedupe:
            rescored = dedupe_deals(rescored, price_weight)
        top = sorted(
            rescored,
            key=lambda item: ranking_value(item, price_weight),
            reverse=True,
        )[: config.top_n]

    if args.with_steam and top:
        enrich_top_deals_with_steam(top, references, config, liquidity, price_weight)

    print(f"\nScanned listings: {scanned}")
    print(f"Matches: {len(top)}\n")

    if args.html or args.open:
        if args.html_out:
            html_path = Path(args.html_out)
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            html_path = CACHE_DIR.parent / "reports" / f"deals_{stamp}.html"
        generate_html_report(
            top,
            html_path,
            currency=config.currency,
            scanned=scanned,
        )
        print(f"HTML report: {html_path.resolve()}")
        if args.open:
            webbrowser.open(html_path.resolve().as_uri())

    if args.as_json:
        print(json.dumps([deal.to_row() for deal in top], ensure_ascii=False, indent=2))
    elif not args.html:
        print_table(top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
