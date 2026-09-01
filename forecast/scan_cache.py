from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import PROCESSED_DATA_DIR, ensure_directories

_log = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = PROCESSED_DATA_DIR / "market_scan_latest.json"
SCAN_HISTORY_PATH = PROCESSED_DATA_DIR / "scan_history.jsonl"
SCAN_PROGRESS_PATH = PROCESSED_DATA_DIR / "scan_progress.json"
MAX_SCAN_HISTORY = 96


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
    _append_scan_history(payload)
    try:
        from .push_alerts import notify_high_score_setups

        notify_high_score_setups(report, updated_at=str(payload.get("updated_at") or ""))
    except Exception:
        _log.exception("mobile push after scan failed")
    return out


def _history_summary(report: dict[str, Any]) -> dict[str, Any]:
    setups = report.get("top_setups") or []
    if not setups:
        return {"symbol": None, "score": None, "direction": None}
    top = setups[0]
    plan = top.get("setup") or {}
    return {
        "symbol": top.get("symbol"),
        "score": top.get("score"),
        "direction": plan.get("direction"),
        "pattern": top.get("pattern"),
        "trend": top.get("trend"),
    }


def _append_scan_history(payload: dict[str, Any]) -> None:
    ensure_directories()
    report = payload.get("report") or {}
    entry = {
        "updated_at": payload.get("updated_at"),
        "scan_config": payload.get("scan_config") or {},
        "universe_size": report.get("universe_size"),
        "candidates_found": report.get("candidates_found"),
        "scan_duration_sec": report.get("scan_duration_sec"),
        "symbols_scanned": report.get("symbols_scanned"),
        "top": _history_summary(report),
        "top_count": len(report.get("top_setups") or []),
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with open(SCAN_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    _trim_scan_history()


def _trim_scan_history() -> None:
    if not SCAN_HISTORY_PATH.is_file():
        return
    lines = SCAN_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_SCAN_HISTORY:
        return
    SCAN_HISTORY_PATH.write_text(
        "\n".join(lines[-MAX_SCAN_HISTORY:]) + "\n",
        encoding="utf-8",
    )


def load_scan_history(limit: int = 30) -> list[dict[str, Any]]:
    if not SCAN_HISTORY_PATH.is_file():
        return []
    lines = SCAN_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-max(limit, 1) * 2 :]):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def save_scan_progress(payload: dict[str, Any]) -> None:
    ensure_directories()
    SCAN_PROGRESS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_scan_progress() -> dict[str, Any]:
    if not SCAN_PROGRESS_PATH.is_file():
        return {"status": "idle"}
    try:
        data = json.loads(SCAN_PROGRESS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"status": "idle"}
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": "bad progress file"}


def clear_scan_progress() -> None:
    if SCAN_PROGRESS_PATH.is_file():
        SCAN_PROGRESS_PATH.unlink(missing_ok=True)


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
