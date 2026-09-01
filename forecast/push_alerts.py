"""Push / in-app alerts when a scan finds setups with score above the threshold."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .paths import PROCESSED_DATA_DIR, ensure_directories

_log = logging.getLogger(__name__)

DEFAULT_ALERT_MIN_SCORE = 35.0
SUBSCRIPTIONS_PATH = PROCESSED_DATA_DIR / "mobile_push_subscriptions.json"
EXPO_TOKENS_PATH = PROCESSED_DATA_DIR / "mobile_expo_tokens.json"
VAPID_PATH = PROCESSED_DATA_DIR / "mobile_vapid.json"
LAST_PUSH_PATH = PROCESSED_DATA_DIR / "mobile_last_push.json"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def alert_min_score() -> float:
    raw = (os.environ.get("MOBILE_ALERT_MIN_SCORE") or "").strip()
    if not raw:
        return DEFAULT_ALERT_MIN_SCORE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_ALERT_MIN_SCORE


def flatten_setup(row: dict[str, Any], threshold: float | None = None) -> dict[str, Any]:
    """Compact setup for the phone UI."""
    plan = row.get("setup") or {}
    try:
        score = float(row.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score != score:  # NaN
        score = 0.0
    thr = DEFAULT_ALERT_MIN_SCORE if threshold is None else float(threshold)
    direction = str(plan.get("direction") or row.get("direction") or "").strip()
    if direction.lower() == "long":
        direction = "Long"
    elif direction.lower() == "short":
        direction = "Short"
    return {
        "symbol": row.get("symbol"),
        "score": round(score, 1),
        "direction": direction,
        "pattern": row.get("pattern"),
        "trend": row.get("trend") or plan.get("trend"),
        "probability_pct": plan.get("probability_pct"),
        "risk_reward": plan.get("risk_reward"),
        "entry": plan.get("entry"),
        "stop": plan.get("stop"),
        "target_1": plan.get("target_1"),
        "target_2": plan.get("target_2"),
        "why_selected": row.get("why_selected"),
        "regime": row.get("regime"),
        "hot": score > thr,
    }


def _read_json(path, default):
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    return data if data is not None else default


def _write_json(path, payload: Any) -> None:
    ensure_directories()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_subscriptions() -> list[dict[str, Any]]:
    data = _read_json(SUBSCRIPTIONS_PATH, [])
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict) and s.get("endpoint")]


def save_push_subscription(subscription: dict[str, Any]) -> None:
    endpoint = str(subscription.get("endpoint") or "").strip()
    if not endpoint:
        raise ValueError("нет endpoint")
    keys = subscription.get("keys") or {}
    row = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": str((keys or {}).get("p256dh") or ""),
            "auth": str((keys or {}).get("auth") or ""),
        },
    }
    subs = [s for s in _load_subscriptions() if s.get("endpoint") != endpoint]
    subs.append(row)
    _write_json(SUBSCRIPTIONS_PATH, subs)


def delete_push_subscription(endpoint: str) -> None:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return
    subs = [s for s in _load_subscriptions() if s.get("endpoint") != endpoint]
    _write_json(SUBSCRIPTIONS_PATH, subs)


def _load_expo_tokens() -> list[dict[str, Any]]:
    data = _read_json(EXPO_TOKENS_PATH, [])
    if not isinstance(data, list):
        return []
    return [
        row
        for row in data
        if isinstance(row, dict) and str(row.get("token") or "").startswith("ExponentPushToken[")
    ]


def save_expo_push_token(token: str, *, platform: str = "") -> None:
    token = (token or "").strip()
    if not token.startswith("ExponentPushToken["):
        raise ValueError("неверный Expo push token")
    rows = [r for r in _load_expo_tokens() if r.get("token") != token]
    rows.append({"token": token, "platform": (platform or "").strip()})
    _write_json(EXPO_TOKENS_PATH, rows)


def delete_expo_push_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        return
    rows = [r for r in _load_expo_tokens() if r.get("token") != token]
    _write_json(EXPO_TOKENS_PATH, rows)


def _generate_vapid_keys() -> dict[str, str] | None:
    try:
        import base64

        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
    except ImportError:
        return None

    vapid = Vapid()
    vapid.generate_keys()
    raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    pem = vapid.private_pem()
    if isinstance(pem, bytes):
        pem = pem.decode("ascii")
    mailto = (os.environ.get("MOBILE_VAPID_MAILTO") or "mailto:forecast@localhost").strip()
    return {"publicKey": public_key, "privateKey": pem, "mailto": mailto}


def get_vapid_keys() -> dict[str, str] | None:
    ensure_directories()
    existing = _read_json(VAPID_PATH, None)
    if isinstance(existing, dict) and existing.get("publicKey") and existing.get("privateKey"):
        return existing
    generated = _generate_vapid_keys()
    if not generated:
        return None
    _write_json(VAPID_PATH, generated)
    return generated


def get_vapid_public_key() -> str | None:
    keys = get_vapid_keys()
    if not keys:
        return None
    return str(keys.get("publicKey") or "") or None


def _push_payload(hot: list[dict[str, Any]], threshold: float) -> dict[str, str]:
    if len(hot) == 1:
        s = hot[0]
        title = "Выгодная позиция"
        body = f"{s.get('symbol')} {s.get('direction')} · score {s.get('score')}"
    else:
        title = f"{len(hot)} выгодные позиции"
        parts = [f"{s.get('symbol')} {s.get('score')}" for s in hot[:4]]
        body = " · ".join(parts)
        if len(hot) > 4:
            body += f" и ещё {len(hot) - 4}"
    return {
        "title": title,
        "body": body,
        "url": "/scanner?mobile=1",
        "tag": "forecast-hot",
        "threshold": str(threshold),
    }


def _send_expo_push(token: str, payload: dict[str, str]) -> bool:
    """Return False if token should be removed."""
    try:
        import requests
    except ImportError:
        return True
    try:
        resp = requests.post(
            EXPO_PUSH_URL,
            json={
                "to": token,
                "title": payload.get("title"),
                "body": payload.get("body"),
                "sound": "default",
                "priority": "high",
                "data": {"url": payload.get("url"), "tag": payload.get("tag")},
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code >= 400:
            _log.warning("expo push http %s: %s", resp.status_code, resp.text[:200])
            return True
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            status = str(data.get("status") or "")
            if status == "error":
                detail = str((data.get("details") or {}).get("error") or data.get("message") or "")
                if detail in ("DeviceNotRegistered", "InvalidCredentials"):
                    return False
        return True
    except Exception as e:
        _log.warning("expo push error: %s", e)
        return True


def _send_web_push(subscription: dict[str, Any], payload: dict[str, str], vapid: dict[str, str]) -> bool:
    """Return False if the subscription should be dropped."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return True

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.get("endpoint"),
                "keys": subscription.get("keys") or {},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid.get("privateKey"),
            vapid_claims={"sub": vapid.get("mailto") or "mailto:forecast@localhost"},
        )
        return True
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (404, 410):
            return False
        _log.warning("web push failed: %s", e)
        return True
    except Exception as e:
        _log.warning("web push error: %s", e)
        return True


def notify_high_score_setups(report: dict[str, Any], *, updated_at: str | None = None) -> int:
    """Send one push per new scan if any setup score is above the threshold. Returns sent count."""
    threshold = alert_min_score()
    setups = [flatten_setup(row, threshold) for row in (report.get("top_setups") or [])]
    hot = [s for s in setups if s.get("hot")]
    if not hot:
        return 0

    prev = _read_json(LAST_PUSH_PATH, {})
    if isinstance(prev, dict) and updated_at and prev.get("updated_at") == updated_at:
        return 0

    payload = _push_payload(hot, threshold)
    vapid = get_vapid_keys()
    sent = 0

    if vapid:
        subs = _load_subscriptions()
        kept: list[dict[str, Any]] = []
        for sub in subs:
            ok = _send_web_push(sub, payload, vapid)
            if ok:
                kept.append(sub)
                sent += 1
        if len(kept) != len(subs):
            _write_json(SUBSCRIPTIONS_PATH, kept)
    else:
        _log.info("web push skipped: pywebpush not installed")

    expo_rows = _load_expo_tokens()
    kept_expo: list[dict[str, Any]] = []
    for row in expo_rows:
        token = str(row.get("token") or "")
        if not token:
            continue
        ok = _send_expo_push(token, payload)
        if ok:
            kept_expo.append(row)
            sent += 1
    if len(kept_expo) != len(expo_rows):
        _write_json(EXPO_TOKENS_PATH, kept_expo)

    _write_json(
        LAST_PUSH_PATH,
        {
            "updated_at": updated_at,
            "hot_count": len(hot),
            "symbols": [s.get("symbol") for s in hot],
            "sent": sent,
        },
    )
    return sent
