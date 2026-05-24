"""
Auto-trade top scanner setup on Binance USDT-M Futures (long or short from scan).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv

from .binance_client import create_trading_client
from .paths import PROCESSED_DATA_DIR, ensure_directories, load_project_env
from .scan_cache import DEFAULT_CACHE_PATH, load_scan_result

STATE_PATH = PROCESSED_DATA_DIR / "auto_trade_state.json"
MAX_TRADE_HISTORY = 80


@dataclass
class AutoTradeConfig:
    enabled: bool = False
    dry_run: bool = True
    min_score: float = 55.0
    min_probability_pct: float = 55.0
    min_risk_reward: float = 1.5
    risk_pct_of_balance: float = 0.5
    max_notional_usdt: float = 50.0
    cooldown_minutes: int = 240
    leverage: int = 5
    margin_mode: str = "isolated"
    use_target_2: bool = True
    pick_from_top_n: int = 4
    max_open_positions: int = 3
    profit_close_pct: float = 10.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def load_auto_trade_config(yaml_cfg: dict[str, Any] | None = None) -> AutoTradeConfig:
    load_project_env(force=True)
    y = yaml_cfg or {}
    max_n = float(y.get("max_notional_usdt", y.get("max_quote_usdt", 50.0)))
    base = AutoTradeConfig(
        enabled=bool(y.get("enabled", False)),
        dry_run=bool(y.get("dry_run", True)),
        min_score=float(y.get("min_score", 55.0)),
        min_probability_pct=float(y.get("min_probability_pct", 55.0)),
        min_risk_reward=float(y.get("min_risk_reward", 1.5)),
        risk_pct_of_balance=float(y.get("risk_pct_of_balance", 0.5)),
        max_notional_usdt=max_n,
        cooldown_minutes=int(y.get("cooldown_minutes", 240)),
        leverage=int(y.get("leverage", 5)),
        margin_mode=str(y.get("margin_mode", "isolated")).lower(),
        use_target_2=bool(y.get("use_target_2", True)),
        pick_from_top_n=int(y.get("pick_from_top_n", 4)),
        max_open_positions=int(y.get("max_open_positions", 3)),
        profit_close_pct=float(y.get("profit_close_pct", 10.0)),
    )
    env_map = {
        "AUTO_TRADE_ENABLED": ("enabled", _env_bool),
        "AUTO_TRADE_DRY_RUN": ("dry_run", _env_bool),
        "AUTO_TRADE_MIN_SCORE": ("min_score", _env_float),
        "AUTO_TRADE_MIN_PROB_PCT": ("min_probability_pct", _env_float),
        "AUTO_TRADE_MIN_RR": ("min_risk_reward", _env_float),
        "AUTO_TRADE_RISK_PCT": ("risk_pct_of_balance", _env_float),
        "AUTO_TRADE_MAX_NOTIONAL_USDT": ("max_notional_usdt", _env_float),
        "AUTO_TRADE_MAX_QUOTE_USDT": ("max_notional_usdt", _env_float),
        "AUTO_TRADE_COOLDOWN_MINUTES": ("cooldown_minutes", _env_int),
        "AUTO_TRADE_LEVERAGE": ("leverage", _env_int),
        "AUTO_TRADE_TOP_N": ("pick_from_top_n", _env_int),
        "AUTO_TRADE_MAX_POSITIONS": ("max_open_positions", _env_int),
        "AUTO_TRADE_PROFIT_CLOSE_PCT": ("profit_close_pct", _env_float),
    }
    for env_name, (attr, fn) in env_map.items():
        if os.environ.get(env_name, "").strip():
            setattr(base, attr, fn(env_name, getattr(base, attr)))
    mm = os.environ.get("AUTO_TRADE_MARGIN_MODE", "").strip().lower()
    if mm:
        base.margin_mode = mm
    return base


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("open") and not state.get("open_positions"):
        state["open_positions"] = [dict(state.pop("open"))]
    if not isinstance(state.get("open_positions"), list):
        state["open_positions"] = []
    return state


def load_trade_state() -> dict[str, Any]:
    ensure_directories()
    if not STATE_PATH.is_file():
        return _normalize_state({})
    try:
        return _normalize_state(json.loads(STATE_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return _normalize_state({})


def save_trade_state(state: dict[str, Any]) -> None:
    ensure_directories()
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def append_trade_history(state: dict[str, Any], event: dict[str, Any]) -> None:
    hist = state.get("history")
    if not isinstance(hist, list):
        hist = []
    row = dict(event)
    row.setdefault("at", datetime.now(timezone.utc).isoformat())
    hist.insert(0, row)
    state["history"] = hist[:MAX_TRADE_HISTORY]


def load_trade_history(limit: int = 40) -> list[dict[str, Any]]:
    hist = load_trade_state().get("history") or []
    if not isinstance(hist, list):
        return []
    return hist[: max(1, int(limit))]


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _cooldown_active(state: dict[str, Any], cfg: AutoTradeConfig) -> bool:
    last = _parse_ts(state.get("last_trade_at"))
    if not last:
        return False
    return datetime.now(timezone.utc) < last + timedelta(minutes=int(cfg.cooldown_minutes))


def pick_best_setup(report: dict[str, Any]) -> dict[str, Any] | None:
    """Top-1 for display; auto-trade uses pick_trade_candidate."""
    setups = report.get("top_setups") or []
    return setups[0] if setups else None


def pick_trade_candidate(
    report: dict[str, Any],
    cfg: AutoTradeConfig,
) -> tuple[dict[str, Any] | None, str]:
    """
    First setup in top-N that passes trade filters (not only rank #1).
    Entry is always market price on the exchange; stop/TP from the plan.
    """
    n = max(1, int(cfg.pick_from_top_n))
    setups = list(report.get("top_setups") or [])[:n]
    if not setups:
        return None, "NO_SETUP"
    last_reason = "NO_SETUP"
    for rank, cand in enumerate(setups, start=1):
        ok, reason = validate_setup(cand, cfg)
        if ok:
            out = dict(cand)
            out["_trade_rank"] = rank
            return out, "OK"
        last_reason = f"#{rank}:{reason}"
        print(
            f"[auto_trade] skip rank {rank} {cand.get('symbol')} score={cand.get('score')} ({reason})",
            flush=True,
        )
    return None, f"NO_QUALIFIED_IN_TOP{n} (last {last_reason})"


def _normalize_side(direction: str) -> str:
    d = direction.strip().lower()
    if d in ("long", "buy"):
        return "long"
    if d in ("short", "sell"):
        return "short"
    return d


def validate_setup(candidate: dict[str, Any], cfg: AutoTradeConfig) -> tuple[bool, str]:
    setup = candidate.get("setup") or {}
    side = _normalize_side(str(setup.get("direction", "")))
    if side not in ("long", "short"):
        return False, f"BAD_DIRECTION:{setup.get('direction')}"
    score = float(candidate.get("score", 0.0))
    if score < cfg.min_score:
        return False, f"LOW_SCORE:{score:.1f}"
    prob = float(setup.get("probability_pct", 0.0))
    if prob < cfg.min_probability_pct:
        return False, f"LOW_PROB:{prob:.1f}"
    rr = float(setup.get("risk_reward", 0.0))
    if rr < cfg.min_risk_reward:
        return False, f"LOW_RR:{rr:.2f}"
    for key in ("entry", "stop", "target_2"):
        v = float(setup.get(key, 0.0))
        if not (v > 0 and v == v):
            return False, f"BAD_PLAN:{key}"
    entry = float(setup["entry"])
    stop = float(setup["stop"])
    if side == "long" and stop >= entry:
        return False, "BAD_STOP_LONG"
    if side == "short" and stop <= entry:
        return False, "BAD_STOP_SHORT"
    return True, "OK"


def _futures_symbol(exchange: Any, spot_symbol: str) -> str:
    exchange.load_markets()
    if spot_symbol in exchange.markets and exchange.markets[spot_symbol].get("swap"):
        return spot_symbol
    base = spot_symbol.split("/")[0]
    candidates = [
        f"{base}/USDT:USDT",
        f"{base}/USDT",
    ]
    for sym in candidates:
        if sym in exchange.markets:
            return sym
    for sym, m in exchange.markets.items():
        if (
            m.get("base") == base
            and str(m.get("quote", "")).upper() == "USDT"
            and m.get("linear")
            and m.get("active", True)
        ):
            return sym
    return spot_symbol


def _market_limits(exchange: Any, symbol: str) -> tuple[float, float]:
    m = exchange.market(symbol)
    min_cost = float(((m.get("limits") or {}).get("cost") or {}).get("min") or 5.0)
    min_amount = float(((m.get("limits") or {}).get("amount") or {}).get("min") or 0.0)
    return min_cost, min_amount


def _notional_usdt(
    *,
    free_usdt: float,
    entry: float,
    stop: float,
    cfg: AutoTradeConfig,
) -> float:
    risk_frac = abs(entry - stop) / max(entry, 1e-12)
    risk_budget = free_usdt * (cfg.risk_pct_of_balance / 100.0)
    if risk_frac <= 0:
        return 0.0
    notional = risk_budget / risk_frac
    max_margin = free_usdt * 0.98
    max_notional_by_margin = max_margin * max(1, int(cfg.leverage))
    notional = min(notional, cfg.max_notional_usdt, max_notional_by_margin)
    return max(0.0, notional)


def _fetch_open_position(exchange: Any, symbol: str) -> dict[str, Any] | None:
    try:
        positions = exchange.fetch_positions([symbol])
    except Exception:
        positions = exchange.fetch_positions()
    for p in positions or []:
        sym = str(p.get("symbol") or "")
        if sym != symbol and not sym.startswith(symbol.split(":")[0]):
            continue
        contracts = float(p.get("contracts") or p.get("contractSize") or 0.0)
        if abs(contracts) > 0:
            return p
    return None


def _fetch_live_positions_map(exchange: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in exchange.fetch_positions() or []:
        if abs(float(p.get("contracts") or 0.0)) > 0:
            out[str(p.get("symbol") or "")] = p
    return out


def _position_margin_usdt(rec: dict[str, Any]) -> float:
    """USDT margin ≈ notional / leverage (собственные средства в сделке)."""
    notional = float(rec.get("notional_usdt") or 0.0)
    lev = max(1, int(rec.get("leverage") or 1))
    return notional / lev if notional > 0 else 0.0


def _profit_target_usdt(rec: dict[str, Any], cfg: AutoTradeConfig) -> float:
    """Цель uPnL: profit_close_pct % от маржи позиции, не от полного notional."""
    margin = _position_margin_usdt(rec)
    return margin * (float(cfg.profit_close_pct) / 100.0)


def _sync_open_positions(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> dict[str, Any]:
    state = _normalize_state(state)
    live = _fetch_live_positions_map(exchange)
    kept: list[dict[str, Any]] = []
    for rec in state.get("open_positions") or []:
        spot = str(rec.get("symbol") or "")
        fsym = str(rec.get("futures_symbol") or _futures_symbol(exchange, spot))
        p = live.get(fsym)
        if p is None:
            continue
        upnl = float(p.get("unrealizedPnl") or p.get("unrealisedPnl") or 0.0)
        row = dict(rec)
        row["futures_symbol"] = fsym
        row["symbol"] = spot or fsym.split(":")[0] + "/USDT"
        row["unrealized_pnl"] = upnl
        row["contracts"] = abs(float(p.get("contracts") or 0.0))
        row["profit_target_usdt"] = _profit_target_usdt(row, cfg)
        row["mark_price"] = float(p.get("markPrice") or p.get("entryPrice") or 0.0)
        kept.append(row)
    if len(kept) < len(state.get("open_positions") or []):
        state["last_close_reason"] = "position_closed"
        state["last_close_at"] = datetime.now(timezone.utc).isoformat()
    state["open_positions"] = kept
    state.pop("open", None)
    return state


def _open_positions_count(state: dict[str, Any]) -> int:
    return len(state.get("open_positions") or [])


def _has_symbol_open(state: dict[str, Any], futures_symbol: str) -> bool:
    for rec in state.get("open_positions") or []:
        if str(rec.get("futures_symbol")) == futures_symbol:
            return True
    return False


def close_futures_position_market(
    exchange: Any,
    futures_symbol: str,
    *,
    reason: str,
    cfg: AutoTradeConfig,
) -> dict[str, Any]:
    if cfg.dry_run:
        return {"ok": True, "dry_run": True, "futures_symbol": futures_symbol, "reason": reason}

    pos = _fetch_open_position(exchange, futures_symbol)
    if pos is None:
        return {"ok": False, "reason": "NO_POSITION", "futures_symbol": futures_symbol}

    side = str(pos.get("side") or "").lower()
    close_side = "sell" if side == "long" else "buy"
    amount = float(exchange.amount_to_precision(futures_symbol, abs(float(pos.get("contracts") or 0.0))))
    try:
        exchange.cancel_all_orders(futures_symbol)
    except Exception as e:
        print(f"[auto_trade] cancel_all_orders warning: {e}", flush=True)

    order = exchange.create_order(
        futures_symbol,
        "market",
        close_side,
        amount,
        None,
        {"reduceOnly": True},
    )
    return {
        "ok": True,
        "futures_symbol": futures_symbol,
        "reason": reason,
        "close_order_id": order.get("id"),
        "unrealized_pnl": float(pos.get("unrealizedPnl") or 0.0),
    }


def check_profit_closes(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    if cfg.dry_run or float(cfg.profit_close_pct) <= 0:
        return closed
    for rec in list(state.get("open_positions") or []):
        fsym = str(rec.get("futures_symbol") or "")
        if not fsym:
            continue
        live = _fetch_open_position(exchange, fsym)
        if live is None:
            continue
        upnl = float(live.get("unrealizedPnl") or 0.0)
        target = _profit_target_usdt(rec, cfg)
        if target > 0 and upnl >= target:
            print(
                f"[auto_trade] profit close {fsym} uPnL={upnl:.2f} target={target:.2f} "
                f"({cfg.profit_close_pct}% of margin)",
                flush=True,
            )
            res = close_futures_position_market(exchange, fsym, reason=f"PROFIT_{cfg.profit_close_pct}PCT", cfg=cfg)
            closed.append(res)
            append_trade_history(
                state,
                {
                    "action": "closed",
                    "reason": res.get("reason"),
                    "symbol": rec.get("symbol"),
                    "side": rec.get("side"),
                    "dry_run": False,
                    "notional_usdt": rec.get("notional_usdt"),
                },
            )
    return closed


def manage_open_positions(*, yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync positions, apply +profit% closes. Call each scan cycle."""
    cfg = load_auto_trade_config(yaml_cfg)
    state = load_trade_state()
    exchange = create_trading_client(use_futures=True)
    state = _sync_open_positions(exchange, state, cfg)
    closed = check_profit_closes(exchange, state, cfg)
    state = _sync_open_positions(exchange, state, cfg)
    save_trade_state(state)
    return {"open_count": _open_positions_count(state), "profit_closed": closed}


def close_position_from_panel(futures_symbol: str, *, yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_auto_trade_config(yaml_cfg)
    state = load_trade_state()
    exchange = create_trading_client(use_futures=True)
    fsym = futures_symbol.strip()
    rec = next(
        (r for r in state.get("open_positions") or [] if str(r.get("futures_symbol")) == fsym),
        None,
    )
    res = close_futures_position_market(exchange, fsym, reason="MANUAL_PANEL", cfg=cfg)
    if res.get("ok") and not cfg.dry_run:
        append_trade_history(
            state,
            {
                "action": "closed",
                "reason": "MANUAL_PANEL",
                "symbol": (rec or {}).get("symbol"),
                "side": (rec or {}).get("side"),
                "dry_run": False,
            },
        )
    state = _sync_open_positions(exchange, state, cfg)
    save_trade_state(state)
    return res


def _configure_symbol(exchange: Any, symbol: str, cfg: AutoTradeConfig) -> None:
    lev = max(1, min(int(cfg.leverage), 125))
    try:
        exchange.set_leverage(lev, symbol)
    except Exception as e:
        print(f"[auto_trade] set_leverage warning: {e}", flush=True)
    mode = "ISOLATED" if cfg.margin_mode == "isolated" else "CROSSED"
    try:
        exchange.set_margin_mode(mode, symbol)
    except Exception as e:
        print(f"[auto_trade] set_margin_mode warning: {e}", flush=True)


def execute_futures_trade(
    exchange: Any,
    *,
    symbol: str,
    side: str,
    setup: dict[str, Any],
    notional_usdt: float,
    cfg: AutoTradeConfig,
) -> dict[str, Any]:
    fsym = _futures_symbol(exchange, symbol)
    entry = float(setup["entry"])
    stop = float(setup["stop"])
    tp = float(setup["target_2"] if cfg.use_target_2 else setup.get("target_1", setup["target_2"]))
    min_cost, min_amount = _market_limits(exchange, fsym)

    if notional_usdt < min_cost:
        return {"ok": False, "reason": f"NOTIONAL_BELOW_MIN:{notional_usdt:.2f}<{min_cost}"}

    amount_raw = notional_usdt / max(entry, 1e-12)
    amount = float(exchange.amount_to_precision(fsym, amount_raw))
    if amount < min_amount:
        return {"ok": False, "reason": f"AMOUNT_TOO_SMALL:{amount}<{min_amount}"}

    open_side = "buy" if side == "long" else "sell"
    close_side = "sell" if side == "long" else "buy"

    if cfg.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "market": "futures",
            "spot_symbol": symbol,
            "futures_symbol": fsym,
            "side": side,
            "notional_usdt": notional_usdt,
            "amount": amount,
            "leverage": cfg.leverage,
            "entry": entry,
            "stop": stop,
            "take_profit": tp,
        }

    _configure_symbol(exchange, fsym, cfg)

    entry_order = exchange.create_order(fsym, "market", open_side, amount)
    filled = float(entry_order.get("filled") or amount)
    entry_price = float(entry_order.get("average") or entry_order.get("price") or entry)
    amount = float(exchange.amount_to_precision(fsym, filled))
    if amount < min_amount:
        return {"ok": False, "reason": "FILLED_TOO_SMALL", "entry_order": entry_order}

    stop_p = exchange.price_to_precision(fsym, stop)
    tp_p = exchange.price_to_precision(fsym, tp)
    common = {"reduceOnly": True, "closePosition": False}

    stop_order = exchange.create_order(
        fsym,
        "STOP_MARKET",
        close_side,
        amount,
        None,
        {**common, "stopPrice": stop_p},
    )
    tp_order = exchange.create_order(
        fsym,
        "TAKE_PROFIT_MARKET",
        close_side,
        amount,
        None,
        {**common, "stopPrice": tp_p},
    )

    return {
        "ok": True,
        "dry_run": False,
        "market": "futures",
        "spot_symbol": symbol,
        "futures_symbol": fsym,
        "side": side,
        "notional_usdt": notional_usdt,
        "amount": amount,
        "leverage": cfg.leverage,
        "entry_order_id": entry_order.get("id"),
        "stop_order_id": stop_order.get("id"),
        "tp_order_id": tp_order.get("id"),
        "entry": entry,
        "entry_price": entry_price,
        "stop": stop,
        "take_profit": tp,
        "profit_target_usdt": _profit_target_usdt(
            {"notional_usdt": notional_usdt, "leverage": cfg.leverage},
            cfg,
        ),
    }


def maybe_run_auto_trade(
    report: dict[str, Any] | None = None,
    *,
    yaml_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_dotenv(override=False)
    cfg = load_auto_trade_config(yaml_cfg)
    result: dict[str, Any] = {"action": "skipped", "reason": "DISABLED"}
    if not cfg.enabled:
        print("[auto_trade] disabled (set AUTO_TRADE_ENABLED=true)", flush=True)
        return result

    state = load_trade_state()
    exchange = create_trading_client(use_futures=True)
    state = _sync_open_positions(exchange, state, cfg)
    check_profit_closes(exchange, state, cfg)
    state = _sync_open_positions(exchange, state, cfg)
    save_trade_state(state)

    def _finish(res: dict[str, Any]) -> dict[str, Any]:
        append_trade_history(
            state,
            {
                "action": res.get("action"),
                "reason": res.get("reason"),
                "symbol": res.get("symbol"),
                "side": res.get("side"),
                "dry_run": cfg.dry_run,
                "notional_usdt": res.get("notional_usdt"),
                "rank": res.get("trade_rank"),
            },
        )
        save_trade_state(state)
        return res

    if report is None:
        cached = load_scan_result()
        report = (cached or {}).get("report") or {}

    candidate, pick_reason = pick_trade_candidate(report, cfg)
    if not candidate:
        result = {"action": "skipped", "reason": pick_reason}
        print(f"[auto_trade] {result['reason']}", flush=True)
        return _finish(result)

    symbol = str(candidate["symbol"])
    trade_rank = int(candidate.get("_trade_rank", 1))
    setup = candidate["setup"]
    side = _normalize_side(str(setup.get("direction", "")))

    if _open_positions_count(state) >= int(cfg.max_open_positions):
        result = {
            "action": "skipped",
            "reason": f"MAX_OPEN_POSITIONS:{_open_positions_count(state)}/{cfg.max_open_positions}",
        }
        print(f"[auto_trade] {result['reason']}", flush=True)
        return _finish(result)
    if _cooldown_active(state, cfg):
        result = {"action": "skipped", "reason": "COOLDOWN"}
        print(f"[auto_trade] {result['reason']}", flush=True)
        return _finish(result)

    fsym = _futures_symbol(exchange, symbol)
    if _has_symbol_open(state, fsym) or _fetch_open_position(exchange, fsym):
        result = {"action": "skipped", "reason": "SYMBOL_ALREADY_OPEN", "symbol": fsym}
        print(f"[auto_trade] {result['reason']}", flush=True)
        return _finish(result)

    bal = exchange.fetch_balance()
    free_usdt = float((bal.get("free") or {}).get("USDT", 0.0) or 0.0)
    notional = _notional_usdt(
        free_usdt=free_usdt,
        entry=float(setup["entry"]),
        stop=float(setup["stop"]),
        cfg=cfg,
    )
    if notional <= 0:
        result = {"action": "skipped", "reason": "ZERO_SIZE", "free_usdt": free_usdt}
        print(f"[auto_trade] {result}", flush=True)
        return _finish(result)

    print(
        f"[auto_trade] {'DRY_RUN' if cfg.dry_run else 'LIVE'} FUTURES {side.upper()} {symbol} "
        f"rank={trade_rank}/{cfg.pick_from_top_n} score={candidate.get('score')} "
        f"market entry notional≈{notional:.2f} USDT lev={cfg.leverage}x",
        flush=True,
    )
    exec_result = execute_futures_trade(
        exchange,
        symbol=symbol,
        side=side,
        setup=setup,
        notional_usdt=notional,
        cfg=cfg,
    )
    if not exec_result.get("ok"):
        result = {"action": "failed", **exec_result}
        print(f"[auto_trade] {result}", flush=True)
        return _finish(result)

    now = datetime.now(timezone.utc).isoformat()
    if not cfg.dry_run:
        state.setdefault("open_positions", []).append(
            {
                "symbol": symbol,
                "futures_symbol": exec_result.get("futures_symbol", fsym),
                "side": side,
                "opened_at": now,
                "notional_usdt": notional,
                "leverage": cfg.leverage,
                "entry": setup["entry"],
                "entry_price": exec_result.get("entry_price", setup["entry"]),
                "stop": setup["stop"],
                "take_profit": exec_result.get("take_profit"),
                "profit_target_usdt": _profit_target_usdt(
                    {"notional_usdt": notional, "leverage": cfg.leverage},
                    cfg,
                ),
                "orders": {
                    "entry": exec_result.get("entry_order_id"),
                    "stop": exec_result.get("stop_order_id"),
                    "tp": exec_result.get("tp_order_id"),
                },
            }
        )
    elif cfg.dry_run:
        state.setdefault("open_positions", []).append(
            {
                "symbol": symbol,
                "futures_symbol": fsym,
                "side": side,
                "opened_at": now,
                "notional_usdt": notional,
                "dry_run": True,
            }
        )
    state["last_trade_at"] = now
    state["last_trade"] = {
        "symbol": symbol,
        "side": side,
        "dry_run": cfg.dry_run,
        "at": now,
    }

    result = {
        "action": "dry_run" if cfg.dry_run else "executed",
        "symbol": symbol,
        "side": side,
        "trade_rank": trade_rank,
        "pick_from_top_n": cfg.pick_from_top_n,
        **exec_result,
    }
    print(f"[auto_trade] done {result.get('action')} {side} {symbol}", flush=True)
    result["notional_usdt"] = notional
    return _finish(result)


def run_from_cache(yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cached = load_scan_result(DEFAULT_CACHE_PATH)
    report = (cached or {}).get("report") or {}
    return maybe_run_auto_trade(report, yaml_cfg=yaml_cfg)
