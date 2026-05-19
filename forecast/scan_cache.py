from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import PROCESSED_DATA_DIR, ensure_directories

DEFAULT_CACHE_PATH = PROCESSED_DATA_DIR / "market_scan_latest.json"


def save_scan_result(
    report: dict[str, Any],
    *,
    scan_config: dict[str, Any] | None = None,
    path: Path | None = None,
) -> Path:
    ensure_directories()
    out = path or DEFAULT_CACHE_PATH
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "scan_config": scan_config or {},
        "report": report,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_scan_result(path: Path | None = None) -> dict[str, Any] | None:
    p = path or DEFAULT_CACHE_PATH
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "report" not in data:
        return None
    return data


def report_from_cache(
    cached: dict[str, Any] | None,
    *,
    top: int | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return (report, updated_at_iso). Optionally slice top_setups."""
    if not cached:
        return {"universe_size": 0, "candidates_found": 0, "top_setups": []}, None
    rep = dict(cached.get("report", {}))
    updated = cached.get("updated_at")
    setups = list(rep.get("top_setups", []))
    if top is not None and top > 0:
        rep["top_setups"] = setups[: int(top)]
    return rep, str(updated) if updated else None
