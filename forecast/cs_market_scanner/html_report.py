"""HTML report with skin preview images."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .analyzer import Deal
from .images import skin_image_url


def _badge(text: str, css_class: str = "") -> str:
    cls = f"badge {css_class}".strip()
    return f'<span class="{cls}">{escape(text)}</span>'


def render_deal_card(deal: Deal, currency: str = "USD") -> str:
    listing = deal.listing
    row = deal.to_row()
    image_url = row.get("image_url") or skin_image_url(listing.classid, listing.instanceid)
    currency_symbol = "$" if currency == "USD" else ("₽" if currency == "RUB" else "€")

    tags: list[str] = []
    if listing.float_value is not None and "(" in listing.market_hash_name:
        tags.append(_badge(f"float {listing.float_value:.4f}", "float"))
    if listing.phase:
        tags.append(_badge(listing.phase, "phase"))
    if listing.sticker_count:
        tags.append(_badge(f"{listing.sticker_count} stickers", "stickers"))
    if listing.chance_to_transfer is not None:
        tags.append(_badge(f"transfer {listing.chance_to_transfer}%", "transfer"))
    if row.get("tm_buy_price") is not None:
        tags.append(
            _badge(
                f"TM bid {currency_symbol}{row['tm_buy_price']:.2f} · spread {row['tm_spread_pct']:.0f}%",
                "liquidity",
            )
        )

    return f"""
    <article class="card">
      <div class="image-wrap">
        <img src="{escape(image_url)}" alt="{escape(row['name'])}" loading="lazy" />
      </div>
      <div class="body">
        <h2><a href="{escape(row['url'])}" target="_blank" rel="noopener">{escape(row['name'])}</a></h2>
        <div class="tags">{''.join(tags)}</div>
        <div class="prices">
          <div><span class="label">TM</span><strong class="tm">{currency_symbol}{row['tm_price']:.2f}</strong></div>
          <div><span class="label">Ref</span><strong>{currency_symbol}{row['reference_price']:.2f}</strong></div>
          <div><span class="label">Profit</span><strong class="profit">+{currency_symbol}{row['profit_usd']:.2f}</strong></div>
          <div><span class="label">Discount</span><strong class="discount">-{row['discount_pct']:.1f}%</strong></div>
        </div>
        {f'<p class="meta"><b>TM buy:</b> {currency_symbol}{row["tm_buy_price"]:.2f} (vol {row["tm_buy_volume"]}, spread {row["tm_spread_pct"]:.1f}%)</p>' if row.get("tm_buy_price") is not None else ''}
        <p class="meta"><b>Refs:</b> {escape(row['references'])}</p>
        <p class="meta"><b>Why:</b> {escape(row['reasons'] or '—')}</p>
        <p class="score">Score {row['score']:.1f}</p>
      </div>
    </article>
    """


def generate_html_report(
    deals: list[Deal],
    output_path: Path,
    *,
    currency: str = "USD",
    scanned: int = 0,
) -> Path:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = "\n".join(render_deal_card(deal, currency) for deal in deals)
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>CS Market Deals — {len(deals)} items</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8eaed;
      --muted: #9aa0a6;
      --accent: #66b3ff;
      --profit: #57d657;
      --tm: #f5a623;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 24px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 1.6rem; }}
    .sub {{ color: var(--muted); margin-bottom: 24px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid #2a2f3a;
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .image-wrap {{
      background: linear-gradient(180deg, #1f2430 0%, #141820 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 220px;
      padding: 16px;
    }}
    .image-wrap img {{
      max-width: 100%;
      max-height: 200px;
      object-fit: contain;
      filter: drop-shadow(0 8px 24px rgba(0,0,0,.45));
    }}
    .body {{ padding: 16px; }}
    .body h2 {{
      margin: 0 0 10px;
      font-size: 1rem;
      line-height: 1.35;
    }}
    .body h2 a {{ color: var(--text); text-decoration: none; }}
    .body h2 a:hover {{ color: var(--accent); }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
    .badge {{
      font-size: 0.75rem;
      padding: 3px 8px;
      border-radius: 999px;
      background: #252b36;
      color: var(--muted);
    }}
    .badge.float {{ color: #8ec8ff; }}
    .badge.phase {{ color: #d9a6ff; }}
    .badge.stickers {{ color: #ffd27f; }}
    .badge.transfer {{ color: #9fe8a8; }}
    .badge.liquidity {{ color: #7ee0c3; }}
    .prices {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px 12px;
      margin-bottom: 12px;
    }}
    .label {{ display: block; color: var(--muted); font-size: 0.75rem; }}
    .tm {{ color: var(--tm); }}
    .profit {{ color: var(--profit); }}
    .discount {{ color: var(--accent); }}
    .meta {{ margin: 0 0 6px; font-size: 0.85rem; color: var(--muted); line-height: 1.4; }}
    .score {{ margin: 8px 0 0; font-size: 0.8rem; color: var(--muted); }}
  </style>
</head>
<body>
  <h1>Top deals on market.csgo.com</h1>
  <p class="sub">Generated {generated} · scanned {scanned:,} listings · currency {currency}</p>
  <div class="grid">
    {cards}
  </div>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
