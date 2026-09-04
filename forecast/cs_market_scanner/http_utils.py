"""Shared HTTP helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

SKINPORT_HEADERS = {
    "Accept-Encoding": "br",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://skinport.com/",
    "Origin": "https://skinport.com",
}


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    retry_delay: float = 1.5,
) -> Any:
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                encoding = response.headers.get("Content-Encoding", "").lower()
                if encoding == "br":
                    import brotli

                    raw = brotli.decompress(raw)
                return json.loads(raw.decode())
        except urllib.error.HTTPError as exc:
            last_error = exc
            # Back off harder on rate limits / blocks.
            if exc.code in {403, 429, 503} and attempt + 1 < retries:
                time.sleep(retry_delay * (attempt + 2) * 2)
                continue
            if attempt + 1 < retries:
                time.sleep(retry_delay * (attempt + 1))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(retry_delay * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error
