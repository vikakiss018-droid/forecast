"""Steam CDN image URLs for CS2 items."""

from __future__ import annotations

STEAM_CDN = "https://community.cloudflare.steamstatic.com/economy/image"


def skin_image_url(
    classid: int | str,
    instanceid: int | str,
    *,
    size: str = "360fx360f",
) -> str:
    return f"{STEAM_CDN}/class/730/{classid}/{instanceid}/{size}"
