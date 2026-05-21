"""Futures account balance and bot statistics for the dashboard."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .binance_client import create_trading_client, trading_credentials_source

_CACHE: dict[str, Any] | None = None
_CACHE_AT: float = 0.0
_CACHE_TTL_SEC = 50.0


def compute_bot_stats(
    trade_history: list[dict[str, Any]],
    scan_history: list[dict[str, Any]],
) -> dict[str, Any]:
    by_action: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in trade_history:
        act = str(row.get("action") or "unknown")
        by_action[act] = by_action.get(act, 0) + 1
        if act == "skipped":
            r = str(row.get("reason") or "unknown").split(":")[0]
            by_reason[r] = by_reason.get(r, 0) + 1

    live_exec = int(by_action.get("executed", 0))
    dry = int(by_action.get("dry_run", 0))
    skipped = int(by_action.get("skipped", 0))
    failed = int(by_action.get("failed", 0))

    return {
        "trade_events_total": len(trade_history),
        "live_executed": live_exec,
        "dry_run": dry,
        "skipped": skipped,
        "failed": failed,
        "scans_in_history": len(scan_history),
        "top_skip_reasons": sorted(by_reason.items(), key=lambda x: -x[1])[:5],
    }


def fetch_futures_account_snapshot(*, force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_AT

    empty: dict[str, Any] = {
        "ok": False,
        "error": None,
        "updated_at": None,
        "usdt": {"free": 0.0, "used": 0.0, "total": 0.0},
        "unrealized_pnl": 0.0,
        "positions": [],
        "positions_count": 0,
    }

    if trading_credentials_source() == "none":
        empty["error"] = "Задайте BINANCE_TRADE_API_KEY и BINANCE_TRADE_API_SECRET в .env"
        return empty

    now = time.time()
    if not force and _CACHE is not None and (now - _CACHE_AT) < _CACHE_TTL_SEC:
        return _CACHE

    try:
        ex = create_trading_client(use_futures=True)
        bal = ex.fetch_balance()
        usdt = bal.get("USDT") or bal.get("total", {}).get("USDT") or {}
        if isinstance(usdt, dict):
            free = float(usdt.get("free") or 0.0)
            used = float(usdt.get("used") or 0.0)
            total = float(usdt.get("total") or (free + used))
        else:
            free = float((bal.get("free") or {}).get("USDT", 0.0) or 0.0)
            used = float((bal.get("used") or {}).get("USDT", 0.0) or 0.0)
            total = float((bal.get("total") or {}).get("USDT", 0.0) or (free + used))

        positions_out: list[dict[str, Any]] = []
        unrealized = 0.0
        for p in ex.fetch_positions():
            contracts = abs(float(p.get("contracts") or 0.0))
            if contracts < 1e-12:
                continue
            side = str(p.get("side") or "").lower()
            u = float(p.get("unrealizedPnl") or p.get("unrealisedPnl") or 0.0)
            unrealized += u
            positions_out.append(
                {
                    "symbol": p.get("symbol"),
                    "side": side,
                    "contracts": contracts,
                    "notional_usdt": abs(float(p.get("notional") or 0.0)),
                    "entry_price": float(p.get("entryPrice") or p.get("entry") or 0.0),
                    "unrealized_pnl": u,
                    "leverage": p.get("leverage"),
                }
            )

        snap = {
            "ok": True,
            "error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "usdt": {"free": free, "used": used, "total": total},
            "unrealized_pnl": unrealized,
            "positions": positions_out,
            "positions_count": len(positions_out),
            "api_source": trading_credentials_source(),
        }
        _CACHE = snap
        _CACHE_AT = now
        return snap
    except Exception as e:
        err = {
            **empty,
            "error": str(e),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _CACHE = err
        _CACHE_AT = now
        return err
