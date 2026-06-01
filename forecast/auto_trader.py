"""
Auto-trade: Binance spot (trend 50 pairs) или USDT-M futures (legacy scanner).
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
MAX_CLOSED_TRADES = 200


@dataclass
class AutoTradeConfig:
    enabled: bool = False
    dry_run: bool = True
    market_type: str = "futures"  # spot | futures
    spot_allow_short: bool = False
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
    stop_loss_roi_usdt: float = 0.0
    allow_level_breakout: bool = True
    allow_triangle: bool = True
    allowed_hours: tuple[int, int] | None = None
    min_atr_pct: float = 0.008


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


def _parse_allowed_hours(raw: str) -> tuple[int, int] | None:
    """Parse '8-20' UTC window; empty/all = no filter."""
    text = raw.strip()
    if not text or text.lower() in ("all", "*", "none", "0-24", "00-24", "24"):
        return None
    if "-" not in text:
        return None
    start_s, end_s = text.split("-", 1)
    start = int(start_s.strip())
    end = int(end_s.strip())
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError(f"allowed_hours out of range 0-23: {raw}")
    return start, end


def _in_allowed_hours(hour: int, window: tuple[int, int] | None) -> bool:
    if window is None:
        return True
    start, end = window
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def load_auto_trade_config(yaml_cfg: dict[str, Any] | None = None) -> AutoTradeConfig:
    load_project_env(force=True)
    y = yaml_cfg or {}
    max_n = float(y.get("max_notional_usdt", y.get("max_quote_usdt", 50.0)))
    market = str(y.get("market_type", y.get("market", "futures"))).strip().lower()
    if market not in ("spot", "futures"):
        market = "futures"
    lev_default = 1 if market == "spot" else 5
    base = AutoTradeConfig(
        enabled=bool(y.get("enabled", False)),
        dry_run=bool(y.get("dry_run", True)),
        market_type=market,
        spot_allow_short=bool(y.get("spot_allow_short", False)),
        min_score=float(y.get("min_score", 55.0)),
        min_probability_pct=float(y.get("min_probability_pct", 55.0)),
        min_risk_reward=float(y.get("min_risk_reward", 1.5)),
        risk_pct_of_balance=float(y.get("risk_pct_of_balance", 0.5)),
        max_notional_usdt=max_n,
        cooldown_minutes=int(y.get("cooldown_minutes", 240)),
        leverage=int(y.get("leverage", lev_default)),
        margin_mode=str(y.get("margin_mode", "isolated")).lower(),
        use_target_2=bool(y.get("use_target_2", True)),
        pick_from_top_n=int(y.get("pick_from_top_n", 4)),
        max_open_positions=int(y.get("max_open_positions", 3)),
        profit_close_pct=float(y.get("profit_close_pct", 10.0)),
        stop_loss_roi_usdt=float(y.get("stop_loss_roi_usdt", 0.0)),
        allow_level_breakout=bool(y.get("allow_level_breakout", True)),
        allow_triangle=bool(y.get("allow_triangle", True)),
        min_atr_pct=float(y.get("min_atr_pct", 0.008)),
    )
    ah_raw = y.get("allowed_hours")
    if ah_raw is not None and str(ah_raw).strip():
        base.allowed_hours = _parse_allowed_hours(str(ah_raw))
    env_map = {
        "AUTO_TRADE_ENABLED": ("enabled", _env_bool),
        "AUTO_TRADE_DRY_RUN": ("dry_run", _env_bool),
        "AUTO_TRADE_MARKET": ("market_type", lambda n, d: (os.environ.get(n, "").strip().lower() or d)),
        "AUTO_TRADE_SPOT_ALLOW_SHORT": ("spot_allow_short", _env_bool),
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
        "AUTO_TRADE_STOP_LOSS_ROI_USDT": ("stop_loss_roi_usdt", _env_float),
        "AUTO_TRADE_ALLOW_LEVEL_BREAKOUT": ("allow_level_breakout", _env_bool),
        "AUTO_TRADE_ALLOW_TRIANGLE": ("allow_triangle", _env_bool),
        "AUTO_TRADE_MIN_ATR_PCT": ("min_atr_pct", _env_float),
    }
    for env_name, (attr, fn) in env_map.items():
        if not os.environ.get(env_name, "").strip():
            continue
        default = getattr(base, attr)
        if fn is _env_float and env_name in (
            "AUTO_TRADE_MIN_SCORE",
            "AUTO_TRADE_MIN_PROB_PCT",
            "AUTO_TRADE_MIN_RR",
            "AUTO_TRADE_RISK_PCT",
            "AUTO_TRADE_MAX_NOTIONAL_USDT",
            "AUTO_TRADE_PROFIT_CLOSE_PCT",
            "AUTO_TRADE_STOP_LOSS_ROI_USDT",
            "AUTO_TRADE_MIN_ATR_PCT",
        ):
            val = _env_float(env_name, float(default) if default else 0.0)
            if val > 0 or env_name in ("AUTO_TRADE_PROFIT_CLOSE_PCT", "AUTO_TRADE_STOP_LOSS_ROI_USDT", "AUTO_TRADE_MIN_ATR_PCT"):
                setattr(base, attr, val)
            continue
        if fn is _env_int and env_name in (
            "AUTO_TRADE_LEVERAGE",
            "AUTO_TRADE_TOP_N",
            "AUTO_TRADE_MAX_POSITIONS",
            "AUTO_TRADE_COOLDOWN_MINUTES",
        ):
            val = _env_int(env_name, int(default) if default else 0)
            if val > 0 or env_name == "AUTO_TRADE_COOLDOWN_MINUTES":
                setattr(base, attr, val)
            continue
        setattr(base, attr, fn(env_name, default))
    ah_env = os.environ.get("AUTO_TRADE_ALLOWED_HOURS", "").strip()
    if ah_env:
        base.allowed_hours = _parse_allowed_hours(ah_env)
    mm = os.environ.get("AUTO_TRADE_MARGIN_MODE", "").strip().lower()
    if mm:
        base.margin_mode = mm
    if base.market_type == "spot" and base.leverage > 1 and not os.environ.get("AUTO_TRADE_LEVERAGE", "").strip():
        base.leverage = 1
    return base


def is_spot_market(cfg: AutoTradeConfig) -> bool:
    return str(cfg.market_type).strip().lower() == "spot"


def exchange_for_config(cfg: AutoTradeConfig) -> Any:
    return create_trading_client(use_futures=not is_spot_market(cfg))


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("open") and not state.get("open_positions"):
        state["open_positions"] = [dict(state.pop("open"))]
    if not isinstance(state.get("open_positions"), list):
        state["open_positions"] = []
    if not isinstance(state.get("closed_trades"), list):
        state["closed_trades"] = []
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


def load_closed_trades(limit: int = 80) -> list[dict[str, Any]]:
    rows = load_trade_state().get("closed_trades") or []
    if not isinstance(rows, list):
        return []
    return rows[: max(1, int(limit))]


def _duration_seconds(opened_at: str | None, closed_at: str | None) -> int | None:
    start = _parse_ts(opened_at)
    end = _parse_ts(closed_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _setup_metadata_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    setup = candidate.get("setup") or {}
    return {
        "pattern": candidate.get("pattern"),
        "trend": candidate.get("trend"),
        "score": candidate.get("score"),
        "probability_pct": setup.get("probability_pct"),
        "risk_reward": setup.get("risk_reward"),
        "why_selected": candidate.get("why_selected"),
        "vol_s_up": candidate.get("vol_s_up"),
        "vol_s_down": candidate.get("vol_s_down"),
        "trade_rank": candidate.get("_trade_rank"),
    }


def record_closed_trade(
    state: dict[str, Any],
    rec: dict[str, Any] | None,
    *,
    close_reason: str,
    realized_pnl: float | None = None,
    closed_at: str | None = None,
) -> None:
    """Append one row to closed_trades journal (deduped by symbol + opened_at)."""
    if not rec or rec.get("dry_run"):
        return
    state = _normalize_state(state)
    closed_iso = closed_at or datetime.now(timezone.utc).isoformat()
    opened_iso = str(rec.get("opened_at") or "")
    fsym = str(rec.get("futures_symbol") or "")
    dedupe_key = (fsym, opened_iso)
    for row in state.get("closed_trades") or []:
        if (str(row.get("futures_symbol") or ""), str(row.get("opened_at") or "")) == dedupe_key:
            return

    dur = _duration_seconds(opened_iso, closed_iso)
    pnl = realized_pnl
    if pnl is None and rec.get("unrealized_pnl") is not None:
        pnl = float(rec.get("unrealized_pnl"))

    journal = {
        "symbol": rec.get("symbol"),
        "futures_symbol": fsym,
        "side": rec.get("side"),
        "pattern": rec.get("pattern"),
        "trend": rec.get("trend"),
        "score": rec.get("score"),
        "probability_pct": rec.get("probability_pct"),
        "risk_reward": rec.get("risk_reward"),
        "why_selected": rec.get("why_selected"),
        "trade_rank": rec.get("trade_rank"),
        "opened_at": opened_iso,
        "closed_at": closed_iso,
        "duration_sec": dur,
        "close_reason": close_reason,
        "notional_usdt": rec.get("notional_usdt"),
        "leverage": rec.get("leverage"),
        "entry_price": rec.get("entry_price") or rec.get("entry"),
        "stop": rec.get("stop"),
        "take_profit": rec.get("take_profit"),
        "realized_pnl": pnl,
    }
    hist = state.get("closed_trades") or []
    hist.insert(0, journal)
    state["closed_trades"] = hist[:MAX_CLOSED_TRADES]


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


def pick_trade_candidates(
    report: dict[str, Any],
    cfg: AutoTradeConfig,
) -> tuple[list[dict[str, Any]], str]:
    """
    All setups in top-N that pass trade filters (in rank order).
    Entry is always market price on the exchange; stop/TP from the plan.
    """
    n = max(1, int(cfg.pick_from_top_n))
    setups = list(report.get("top_setups") or [])[:n]
    if not setups:
        return [], "NO_SETUP"
    qualified: list[dict[str, Any]] = []
    last_reason = "NO_SETUP"
    for rank, cand in enumerate(setups, start=1):
        ok, reason = validate_setup(cand, cfg)
        if ok:
            out = dict(cand)
            out["_trade_rank"] = rank
            qualified.append(out)
        else:
            last_reason = f"#{rank}:{reason}"
            print(
                f"[auto_trade] skip rank {rank} {cand.get('symbol')} score={cand.get('score')} ({reason})",
                flush=True,
            )
    if qualified:
        return qualified, "OK"
    return [], f"NO_QUALIFIED_IN_TOP{n} (last {last_reason})"


def pick_trade_candidate(
    report: dict[str, Any],
    cfg: AutoTradeConfig,
) -> tuple[dict[str, Any] | None, str]:
    """First qualified setup in top-N (legacy / display)."""
    candidates, reason = pick_trade_candidates(report, cfg)
    if candidates:
        return candidates[0], "OK"
    return None, reason


def is_triangle_candidate(candidate: dict[str, Any]) -> bool:
    """Сетап с паттерном triangle (не открывать, если allow_triangle=false)."""
    pat = str(candidate.get("pattern") or "").lower().replace("_", " ").strip()
    return pat == "triangle" or "triangle" in pat.split()


def is_level_breakout_candidate(candidate: dict[str, Any]) -> bool:
    """Сетап на пробитие / ретест уровня (не открывать, если allow_level_breakout=false)."""
    if candidate.get("retest_breakout"):
        return True
    if candidate.get("breakout_consolidation"):
        return True
    pat = str(candidate.get("pattern") or "").lower().replace("_", " ")
    if "breakout" in pat:
        return True
    why = str(candidate.get("why_selected") or "").lower()
    if "ретест пробоя" in why:
        return True
    return False


def _normalize_side(direction: str) -> str:
    d = direction.strip().lower()
    if d in ("long", "buy"):
        return "long"
    if d in ("short", "sell"):
        return "short"
    return d


def validate_setup(candidate: dict[str, Any], cfg: AutoTradeConfig) -> tuple[bool, str]:
    if not cfg.allow_level_breakout and is_level_breakout_candidate(candidate):
        return False, "BREAKOUT_LEVEL_DISABLED"
    if not cfg.allow_triangle and is_triangle_candidate(candidate):
        return False, "TRIANGLE_DISABLED"
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

    vol_up = candidate.get("vol_s_up")
    vol_down = candidate.get("vol_s_down")
    if vol_up is not None and vol_down is not None:
        try:
            vu = float(vol_up)
            vd = float(vol_down)
            if side == "long" and vd > vu + 0.15:
                return False, "VOLUME_AGAINST_LONG"
            if side == "short" and vu > vd + 0.15:
                return False, "VOLUME_AGAINST_SHORT"
        except (TypeError, ValueError):
            pass
    else:
        print(
            f"[auto_trade] validate_setup {candidate.get('symbol')}: vol_s_up/down missing, skipping vol check",
            flush=True,
        )

    if side == "short" and candidate.get("short_near_support"):
        return False, "SHORT_NEAR_SUPPORT"

    if cfg.min_atr_pct > 0:
        atr_pct = candidate.get("atr_pct")
        if atr_pct is not None:
            try:
                ap = float(atr_pct)
                if ap < cfg.min_atr_pct:
                    return False, f"LOW_VOLATILITY:{ap:.4f}<{cfg.min_atr_pct}"
            except (TypeError, ValueError):
                pass

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


def _spot_base_currency(symbol: str) -> str:
    return symbol.split("/")[0].strip()


def _spot_free_base(exchange: Any, symbol: str) -> float:
    base = _spot_base_currency(symbol)
    bal = exchange.fetch_balance()
    free = bal.get("free") or {}
    return float(free.get(base) or 0.0)


def _spot_unrealized_pnl(rec: dict[str, Any], mark: float) -> float:
    entry = float(rec.get("entry_price") or rec.get("entry") or 0.0)
    amount = float(rec.get("amount") or 0.0)
    if entry <= 0 or amount <= 0:
        return 0.0
    side = str(rec.get("side") or "long").lower()
    if side == "long":
        return amount * (mark - entry)
    return amount * (entry - mark)


def _sync_open_positions_spot(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> dict[str, Any]:
    state = _normalize_state(state)
    kept: list[dict[str, Any]] = []
    for rec in state.get("open_positions") or []:
        symbol = str(rec.get("symbol") or "")
        if not symbol:
            continue
        try:
            min_cost, min_amount = _market_limits(exchange, symbol)
        except Exception:
            min_amount = 1e-8
        amount = _spot_free_base(exchange, symbol)
        if amount < min_amount * 0.25:
            if not cfg.dry_run:
                cancel_symbol_open_orders(exchange, symbol, order_ids=_order_ids_from_rec(rec))
                record_closed_trade(state, rec, close_reason="EXCHANGE_CLOSED")
            continue
        try:
            mark = _estimate_entry_price(exchange, symbol, float(rec.get("entry") or 0.0))
        except Exception:
            mark = float(rec.get("entry_price") or rec.get("entry") or 0.0)
        row = dict(rec)
        row["amount"] = amount
        row["mark_price"] = mark
        row["unrealized_pnl"] = _spot_unrealized_pnl(row, mark)
        row["profit_target_usdt"] = _profit_target_usdt(row, cfg)
        row["loss_limit_usdt"] = _loss_limit_usdt(cfg)
        kept.append(row)
    if len(kept) < len(state.get("open_positions") or []):
        state["last_close_reason"] = "position_closed"
        now = datetime.now(timezone.utc).isoformat()
        state["last_close_at"] = now
        state["last_trade_at"] = now
    state["open_positions"] = kept
    state.pop("open", None)
    return state


def _market_limits(exchange: Any, symbol: str) -> tuple[float, float]:
    m = exchange.market(symbol)
    min_cost = float(((m.get("limits") or {}).get("cost") or {}).get("min") or 5.0)
    min_amount = float(((m.get("limits") or {}).get("amount") or {}).get("min") or 0.0)
    return min_cost, min_amount


def _estimate_entry_price(exchange: Any, futures_symbol: str, fallback: float) -> float:
    """Last/mark price for sizing before market fill."""
    try:
        ticker = exchange.fetch_ticker(futures_symbol)
        for key in ("last", "close", "mark"):
            val = ticker.get(key)
            if val is not None:
                px = float(val)
                if px > 0 and px == px:
                    return px
    except Exception as e:
        print(f"[auto_trade] fetch_ticker {futures_symbol} warning: {e}", flush=True)
    return max(float(fallback), 1e-12)


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


def _loss_limit_usdt(cfg: AutoTradeConfig) -> float:
    """Макс. убыток uPnL в USDT (положительное число в конфиге, напр. 5 → закрыть при −5 USDT)."""
    return max(0.0, float(cfg.stop_loss_roi_usdt))


def _sync_open_positions(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> dict[str, Any]:
    if is_spot_market(cfg):
        return _sync_open_positions_spot(exchange, state, cfg)
    return _sync_futures_positions(exchange, state, cfg)


def _sync_futures_positions(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> dict[str, Any]:
    state = _normalize_state(state)
    live = _fetch_live_positions_map(exchange)
    kept: list[dict[str, Any]] = []
    for rec in state.get("open_positions") or []:
        spot = str(rec.get("symbol") or "")
        fsym = str(rec.get("futures_symbol") or _futures_symbol(exchange, spot))
        p = live.get(fsym)
        if p is None:
            if not cfg.dry_run:
                cancel_symbol_open_orders(
                    exchange,
                    fsym,
                    order_ids=_order_ids_from_rec(rec),
                )
                record_closed_trade(state, rec, close_reason="EXCHANGE_CLOSED")
            continue
        upnl = float(p.get("unrealizedPnl") or p.get("unrealisedPnl") or 0.0)
        row = dict(rec)
        row["futures_symbol"] = fsym
        row["symbol"] = spot or fsym.split(":")[0] + "/USDT"
        row["unrealized_pnl"] = upnl
        row["contracts"] = abs(float(p.get("contracts") or 0.0))
        row["profit_target_usdt"] = _profit_target_usdt(row, cfg)
        row["loss_limit_usdt"] = _loss_limit_usdt(cfg)
        row["mark_price"] = float(p.get("markPrice") or p.get("entryPrice") or 0.0)
        kept.append(row)
    if len(kept) < len(state.get("open_positions") or []):
        state["last_close_reason"] = "position_closed"
        now = datetime.now(timezone.utc).isoformat()
        state["last_close_at"] = now
        state["last_trade_at"] = now
    state["open_positions"] = kept
    state.pop("open", None)
    return state


def _open_positions_count(state: dict[str, Any]) -> int:
    return len(state.get("open_positions") or [])


def _has_symbol_open(state: dict[str, Any], trade_key: str) -> bool:
    for rec in state.get("open_positions") or []:
        if str(rec.get("futures_symbol")) == trade_key or str(rec.get("symbol")) == trade_key:
            return True
    return False


def _order_ids_from_rec(rec: dict[str, Any] | None) -> list[str]:
    if not rec:
        return []
    orders = rec.get("orders") or {}
    ids: list[str] = []
    for key in ("stop", "tp", "entry"):
        oid = orders.get(key)
        if oid is not None and str(oid).strip():
            ids.append(str(oid))
    return ids


def cancel_symbol_open_orders(
    exchange: Any,
    trade_symbol: str,
    *,
    order_ids: list[str] | None = None,
) -> None:
    """Cancel regular + conditional (algo) orders on Binance USDT-M."""
    seen_cancel: set[tuple[str, frozenset[tuple[str, str]]]] = set()

    def _try_cancel_all(extra: dict[str, Any] | None) -> None:
        params = extra or {}
        key = (trade_symbol, frozenset(params.items()))
        if key in seen_cancel:
            return
        seen_cancel.add(key)
        exchange.cancel_all_orders(trade_symbol, params)

    for extra in (None, {"conditional": True}, {"trigger": True}):
        try:
            _try_cancel_all(extra)
        except Exception as e:
            label = extra or {}
            print(
                f"[auto_trade] cancel_all_orders {trade_symbol} {label} warning: {e}",
                flush=True,
            )

    for oid in order_ids or []:
        try:
            exchange.cancel_order(oid, trade_symbol)
        except Exception as e:
            print(f"[auto_trade] cancel_order {trade_symbol} id={oid} warning: {e}", flush=True)


def close_spot_position_market(
    exchange: Any,
    symbol: str,
    *,
    reason: str,
    cfg: AutoTradeConfig,
    order_ids: list[str] | None = None,
) -> dict[str, Any]:
    if cfg.dry_run:
        return {"ok": True, "dry_run": True, "symbol": symbol, "reason": reason}

    cancel_symbol_open_orders(exchange, symbol, order_ids=order_ids)
    amount = _spot_free_base(exchange, symbol)
    min_cost, min_amount = _market_limits(exchange, symbol)
    if amount < min_amount:
        return {"ok": False, "reason": "NO_POSITION", "symbol": symbol}

    amount = float(exchange.amount_to_precision(symbol, amount))
    order = exchange.create_order(symbol, "market", "sell", amount)
    cancel_symbol_open_orders(exchange, symbol)
    return {
        "ok": True,
        "symbol": symbol,
        "reason": reason,
        "close_order_id": order.get("id"),
        "amount": amount,
    }


def close_futures_position_market(
    exchange: Any,
    futures_symbol: str,
    *,
    reason: str,
    cfg: AutoTradeConfig,
    order_ids: list[str] | None = None,
) -> dict[str, Any]:
    if cfg.dry_run:
        return {"ok": True, "dry_run": True, "futures_symbol": futures_symbol, "reason": reason}

    pos = _fetch_open_position(exchange, futures_symbol)
    if pos is None:
        return {"ok": False, "reason": "NO_POSITION", "futures_symbol": futures_symbol}

    side = str(pos.get("side") or "").lower()
    close_side = "sell" if side == "long" else "buy"
    amount = float(exchange.amount_to_precision(futures_symbol, abs(float(pos.get("contracts") or 0.0))))
    cancel_symbol_open_orders(exchange, futures_symbol, order_ids=order_ids)

    order = exchange.create_order(
        futures_symbol,
        "market",
        close_side,
        amount,
        None,
        {"reduceOnly": True},
    )
    cancel_symbol_open_orders(exchange, futures_symbol, order_ids=order_ids)
    return {
        "ok": True,
        "futures_symbol": futures_symbol,
        "reason": reason,
        "close_order_id": order.get("id"),
        "unrealized_pnl": float(pos.get("unrealizedPnl") or 0.0),
    }


def _close_position_market(
    exchange: Any,
    rec: dict[str, Any],
    *,
    reason: str,
    cfg: AutoTradeConfig,
) -> dict[str, Any]:
    symbol = str(rec.get("symbol") or "")
    if is_spot_market(cfg):
        return close_spot_position_market(
            exchange,
            symbol,
            reason=reason,
            cfg=cfg,
            order_ids=_order_ids_from_rec(rec),
        )
    fsym = str(rec.get("futures_symbol") or _futures_symbol(exchange, symbol))
    return close_futures_position_market(
        exchange,
        fsym,
        reason=reason,
        cfg=cfg,
        order_ids=_order_ids_from_rec(rec),
    )


def check_profit_closes(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    if cfg.dry_run or float(cfg.profit_close_pct) <= 0:
        return closed
    for rec in list(state.get("open_positions") or []):
        sym = str(rec.get("symbol") or rec.get("futures_symbol") or "")
        if not sym:
            continue
        if is_spot_market(cfg):
            upnl = float(rec.get("unrealized_pnl") or 0.0)
            if float(rec.get("amount") or 0.0) <= 0:
                continue
        else:
            fsym = str(rec.get("futures_symbol") or _futures_symbol(exchange, sym))
            live = _fetch_open_position(exchange, fsym)
            if live is None:
                continue
            upnl = float(live.get("unrealizedPnl") or 0.0)
        target = _profit_target_usdt(rec, cfg)
        if target > 0 and upnl >= target:
            print(
                f"[auto_trade] profit close {sym} uPnL={upnl:.2f} target={target:.2f} "
                f"({cfg.profit_close_pct}% of margin)",
                flush=True,
            )
            res = _close_position_market(
                exchange,
                rec,
                reason=f"PROFIT_{cfg.profit_close_pct}PCT",
                cfg=cfg,
            )
            closed.append(res)
            record_closed_trade(
                state,
                rec,
                close_reason=str(res.get("reason") or f"PROFIT_{cfg.profit_close_pct}PCT"),
                realized_pnl=float(res.get("unrealized_pnl") or 0.0),
            )
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


def check_loss_closes(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> list[dict[str, Any]]:
    """Закрыть позицию, если нереализованный uPnL ≤ −stop_loss_roi_usdt."""
    closed: list[dict[str, Any]] = []
    limit = _loss_limit_usdt(cfg)
    if cfg.dry_run or limit <= 0:
        return closed
    for rec in list(state.get("open_positions") or []):
        sym = str(rec.get("symbol") or rec.get("futures_symbol") or "")
        if not sym:
            continue
        if is_spot_market(cfg):
            upnl = float(rec.get("unrealized_pnl") or 0.0)
            if float(rec.get("amount") or 0.0) <= 0:
                continue
        else:
            fsym = str(rec.get("futures_symbol") or _futures_symbol(exchange, sym))
            live = _fetch_open_position(exchange, fsym)
            if live is None:
                continue
            upnl = float(live.get("unrealizedPnl") or 0.0)
        if upnl <= -limit:
            print(
                f"[auto_trade] stop-loss ROI close {sym} uPnL={upnl:.2f} limit=-{limit:.2f} USDT",
                flush=True,
            )
            res = _close_position_market(
                exchange,
                rec,
                reason=f"STOP_LOSS_ROI_{limit:.0f}USDT",
                cfg=cfg,
            )
            closed.append(res)
            record_closed_trade(
                state,
                rec,
                close_reason=str(res.get("reason") or f"STOP_LOSS_ROI_{limit:.0f}USDT"),
                realized_pnl=float(res.get("unrealized_pnl") or 0.0),
            )
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


def _apply_auto_closes(exchange: Any, state: dict[str, Any], cfg: AutoTradeConfig) -> dict[str, Any]:
    profit = check_profit_closes(exchange, state, cfg)
    loss = check_loss_closes(exchange, state, cfg)
    return {"profit_closed": profit, "loss_closed": loss}


def manage_open_positions(*, yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync positions, apply profit/loss ROI closes. Call each scan cycle."""
    cfg = load_auto_trade_config(yaml_cfg)
    state = load_trade_state()
    exchange = exchange_for_config(cfg)
    state = _sync_open_positions(exchange, state, cfg)
    closed = _apply_auto_closes(exchange, state, cfg)
    state = _sync_open_positions(exchange, state, cfg)
    save_trade_state(state)
    return {
        "open_count": _open_positions_count(state),
        "profit_closed": closed["profit_closed"],
        "loss_closed": closed["loss_closed"],
        "market_type": cfg.market_type,
    }


def close_position_from_panel(futures_symbol: str, *, yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = load_auto_trade_config(yaml_cfg)
    state = load_trade_state()
    exchange = exchange_for_config(cfg)
    key = futures_symbol.strip()
    rec = next(
        (
            r
            for r in state.get("open_positions") or []
            if str(r.get("futures_symbol")) == key or str(r.get("symbol")) == key
        ),
        None,
    )
    if rec is None:
        return {"ok": False, "reason": "NOT_FOUND", "symbol": key}
    res = _close_position_market(exchange, rec, reason="MANUAL_PANEL", cfg=cfg)
    sym = str(rec.get("symbol") or key)
    if not res.get("ok") and not cfg.dry_run:
        cancel_symbol_open_orders(exchange, sym, order_ids=_order_ids_from_rec(rec))
    if res.get("ok") and not cfg.dry_run:
        record_closed_trade(
            state,
            rec,
            close_reason="MANUAL_PANEL",
            realized_pnl=float(res.get("unrealized_pnl") or 0.0),
        )
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

    mark_entry = _estimate_entry_price(exchange, fsym, entry)
    amount_raw = notional_usdt / max(mark_entry, 1e-12)
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
    entry_price = float(entry_order.get("average") or entry_order.get("price") or mark_entry)
    amount = float(exchange.amount_to_precision(fsym, filled))
    if amount < min_amount:
        return {"ok": False, "reason": "FILLED_TOO_SMALL", "entry_order": entry_order}

    planned_risk = abs(entry - stop) / max(entry, 1e-12)
    real_risk = abs(entry_price - stop) / max(entry_price, 1e-12)
    if real_risk > planned_risk * 1.25:
        print(
            f"[auto_trade] warn {fsym}: fill risk {real_risk:.2%} vs planned {planned_risk:.2%} "
            f"(entry {entry_price} plan {entry})",
            flush=True,
        )

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


def execute_spot_trade(
    exchange: Any,
    *,
    symbol: str,
    side: str,
    setup: dict[str, Any],
    notional_usdt: float,
    cfg: AutoTradeConfig,
) -> dict[str, Any]:
    if side != "long":
        if cfg.spot_allow_short:
            return {"ok": False, "reason": "SPOT_SHORT_NOT_IMPLEMENTED"}
        return {"ok": False, "reason": "SPOT_LONG_ONLY"}

    entry = float(setup["entry"])
    stop = float(setup["stop"])
    tp = float(setup["target_2"] if cfg.use_target_2 else setup.get("target_1", setup["target_2"]))
    min_cost, min_amount = _market_limits(exchange, symbol)

    if notional_usdt < min_cost:
        return {"ok": False, "reason": f"NOTIONAL_BELOW_MIN:{notional_usdt:.2f}<{min_cost}"}

    mark_entry = _estimate_entry_price(exchange, symbol, entry)
    amount_est = notional_usdt / max(mark_entry, 1e-12)
    if amount_est < min_amount:
        return {"ok": False, "reason": f"AMOUNT_TOO_SMALL:{amount_est}<{min_amount}"}

    if cfg.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "market": "spot",
            "symbol": symbol,
            "side": side,
            "notional_usdt": notional_usdt,
            "amount": amount_est,
            "leverage": 1,
            "entry": entry,
            "stop": stop,
            "take_profit": tp,
        }

    quote_qty = round(float(notional_usdt), 2)
    entry_order = exchange.create_order(
        symbol,
        "market",
        "buy",
        None,
        None,
        {"quoteOrderQty": quote_qty},
    )
    filled = float(entry_order.get("filled") or amount_est)
    entry_price = float(entry_order.get("average") or entry_order.get("price") or mark_entry)
    amount = float(exchange.amount_to_precision(symbol, filled))
    if amount < min_amount:
        return {"ok": False, "reason": "FILLED_TOO_SMALL", "entry_order": entry_order}

    tp_p = exchange.price_to_precision(symbol, tp)
    stop_p = exchange.price_to_precision(symbol, stop)
    stop_limit = float(stop_p) * 0.995
    stop_limit_p = exchange.price_to_precision(symbol, stop_limit)

    oco = exchange.create_order(
        symbol,
        "OCO",
        "sell",
        amount,
        tp_p,
        {
            "stopPrice": stop_p,
            "stopLimitPrice": stop_limit_p,
            "stopLimitTimeInForce": "GTC",
        },
    )
    oco_ids: list[str] = []
    for part in oco.get("orders") or []:
        oid = part.get("id") if isinstance(part, dict) else None
        if oid is not None:
            oco_ids.append(str(oid))
    oco_id = oco.get("id")
    if oco_id is not None:
        oco_ids.append(str(oco_id))

    return {
        "ok": True,
        "dry_run": False,
        "market": "spot",
        "symbol": symbol,
        "side": side,
        "notional_usdt": notional_usdt,
        "amount": amount,
        "leverage": 1,
        "entry_order_id": entry_order.get("id"),
        "oco_order_id": oco_id,
        "entry": entry,
        "entry_price": entry_price,
        "stop": stop,
        "take_profit": tp,
        "orders": {"entry": entry_order.get("id"), "oco": oco_id, "oco_all": oco_ids},
        "profit_target_usdt": _profit_target_usdt(
            {"notional_usdt": notional_usdt, "leverage": 1},
            cfg,
        ),
    }


def _log_trade_attempt(state: dict[str, Any], cfg: AutoTradeConfig, res: dict[str, Any]) -> None:
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


def _try_open_candidate(
    exchange: Any,
    state: dict[str, Any],
    cfg: AutoTradeConfig,
    candidate: dict[str, Any],
    *,
    free_usdt: float,
) -> dict[str, Any]:
    """Try to open one position; does not check cooldown or global max (caller does)."""
    symbol = str(candidate["symbol"])
    trade_rank = int(candidate.get("_trade_rank", 1))
    setup = candidate["setup"]
    side = _normalize_side(str(setup.get("direction", "")))
    trade_sym = symbol if is_spot_market(cfg) else _futures_symbol(exchange, symbol)

    if is_spot_market(cfg):
        if side != "long" and not cfg.spot_allow_short:
            return {
                "action": "skipped",
                "reason": "SPOT_LONG_ONLY",
                "symbol": symbol,
                "trade_rank": trade_rank,
            }
        if _has_symbol_open(state, symbol):
            return {
                "action": "skipped",
                "reason": "SYMBOL_ALREADY_OPEN",
                "symbol": symbol,
                "trade_rank": trade_rank,
            }
        _min_cost, min_amount = _market_limits(exchange, symbol)
        if _spot_free_base(exchange, symbol) >= min_amount * 0.25:
            return {
                "action": "skipped",
                "reason": "SPOT_BALANCE_OPEN",
                "symbol": symbol,
                "trade_rank": trade_rank,
            }
    elif _has_symbol_open(state, trade_sym) or _fetch_open_position(exchange, trade_sym):
        return {
            "action": "skipped",
            "reason": "SYMBOL_ALREADY_OPEN",
            "symbol": trade_sym,
            "trade_rank": trade_rank,
        }

    price_sym = symbol if is_spot_market(cfg) else trade_sym
    notional = _notional_usdt(
        free_usdt=free_usdt,
        entry=_estimate_entry_price(exchange, price_sym, float(setup["entry"])),
        stop=float(setup["stop"]),
        cfg=cfg,
    )
    if notional <= 0:
        return {
            "action": "skipped",
            "reason": "ZERO_SIZE",
            "symbol": symbol,
            "trade_rank": trade_rank,
            "free_usdt": free_usdt,
        }

    mkt = "SPOT" if is_spot_market(cfg) else "FUTURES"
    print(
        f"[auto_trade] {'DRY_RUN' if cfg.dry_run else 'LIVE'} {mkt} {side.upper()} {symbol} "
        f"rank={trade_rank}/{cfg.pick_from_top_n} score={candidate.get('score')} "
        f"market entry notional≈{notional:.2f} USDT lev={cfg.leverage}x",
        flush=True,
    )
    if is_spot_market(cfg):
        exec_result = execute_spot_trade(
            exchange,
            symbol=symbol,
            side=side,
            setup=setup,
            notional_usdt=notional,
            cfg=cfg,
        )
    else:
        exec_result = execute_futures_trade(
            exchange,
            symbol=symbol,
            side=side,
            setup=setup,
            notional_usdt=notional,
            cfg=cfg,
        )
    if not exec_result.get("ok"):
        reason = str(exec_result.get("reason") or "")
        soft_skip = reason.startswith("NOTIONAL_BELOW_MIN") or reason.startswith("AMOUNT_TOO_SMALL")
        return {
            "action": "skipped" if soft_skip else "failed",
            "symbol": symbol,
            "side": side,
            "trade_rank": trade_rank,
            **exec_result,
        }

    now = datetime.now(timezone.utc).isoformat()
    meta = _setup_metadata_from_candidate(candidate)
    if not cfg.dry_run:
        pos_rec: dict[str, Any] = {
            "symbol": symbol,
            "market": exec_result.get("market", "futures" if not is_spot_market(cfg) else "spot"),
            "side": side,
            "opened_at": now,
            "notional_usdt": notional,
            "leverage": 1 if is_spot_market(cfg) else cfg.leverage,
            "entry": setup["entry"],
            "entry_price": exec_result.get("entry_price", setup["entry"]),
            "stop": setup["stop"],
            "take_profit": exec_result.get("take_profit"),
            "amount": exec_result.get("amount"),
            "profit_target_usdt": exec_result.get("profit_target_usdt")
            or _profit_target_usdt(
                {"notional_usdt": notional, "leverage": cfg.leverage},
                cfg,
            ),
            "loss_limit_usdt": _loss_limit_usdt(cfg),
            **meta,
        }
        if is_spot_market(cfg):
            pos_rec["orders"] = exec_result.get("orders") or {
                "entry": exec_result.get("entry_order_id"),
                "oco": exec_result.get("oco_order_id"),
            }
        else:
            pos_rec["futures_symbol"] = exec_result.get("futures_symbol", trade_sym)
            pos_rec["orders"] = {
                "entry": exec_result.get("entry_order_id"),
                "stop": exec_result.get("stop_order_id"),
                "tp": exec_result.get("tp_order_id"),
            }
        state.setdefault("open_positions", []).append(pos_rec)
    else:
        dry_rec: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "opened_at": now,
            "notional_usdt": notional,
            "dry_run": True,
            "market": "spot" if is_spot_market(cfg) else "futures",
        }
        if not is_spot_market(cfg):
            dry_rec["futures_symbol"] = trade_sym
        state.setdefault("open_positions", []).append(dry_rec)
    state["last_trade_at"] = now
    state["last_trade"] = {
        "symbol": symbol,
        "side": side,
        "dry_run": cfg.dry_run,
        "at": now,
    }
    action = "dry_run" if cfg.dry_run else "executed"
    print(f"[auto_trade] done {action} {side} {symbol}", flush=True)
    return {
        "action": action,
        "symbol": symbol,
        "side": side,
        "trade_rank": trade_rank,
        "notional_usdt": notional,
        **exec_result,
    }


def maybe_run_auto_trade(
    report: dict[str, Any] | None = None,
    *,
    yaml_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_dotenv(override=False)
    cfg = load_auto_trade_config(yaml_cfg)
    result: dict[str, Any] = {"action": "skipped", "reason": "DISABLED", "opened_count": 0, "attempts": []}
    if not cfg.enabled:
        print("[auto_trade] disabled (set AUTO_TRADE_ENABLED=true)", flush=True)
        return result

    state = load_trade_state()
    exchange = exchange_for_config(cfg)
    state = _sync_open_positions(exchange, state, cfg)
    _apply_auto_closes(exchange, state, cfg)
    state = _sync_open_positions(exchange, state, cfg)
    save_trade_state(state)

    if report is None:
        cached = load_scan_result()
        report = (cached or {}).get("report") or {}

    now_h = datetime.now(timezone.utc).hour
    if cfg.allowed_hours is not None and not _in_allowed_hours(now_h, cfg.allowed_hours):
        start, end = cfg.allowed_hours
        result = {
            "action": "skipped",
            "reason": f"OFF_HOURS:{now_h} not in {start}-{end} UTC",
            "opened_count": 0,
            "attempts": [],
        }
        print(f"[auto_trade] {result['reason']}", flush=True)
        _log_trade_attempt(state, cfg, result)
        save_trade_state(state)
        return result

    candidates, pick_reason = pick_trade_candidates(report, cfg)
    if not candidates:
        result = {"action": "skipped", "reason": pick_reason, "opened_count": 0, "attempts": []}
        print(f"[auto_trade] {result['reason']}", flush=True)
        _log_trade_attempt(state, cfg, result)
        save_trade_state(state)
        return result

    if _cooldown_active(state, cfg):
        result = {"action": "skipped", "reason": "COOLDOWN", "opened_count": 0, "attempts": []}
        print(f"[auto_trade] {result['reason']}", flush=True)
        _log_trade_attempt(state, cfg, result)
        save_trade_state(state)
        return result

    max_pos = int(cfg.max_open_positions)
    attempts: list[dict[str, Any]] = []
    opened: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()

    for candidate in candidates:
        if _open_positions_count(state) >= max_pos:
            attempts.append(
                {
                    "action": "skipped",
                    "reason": f"MAX_OPEN_POSITIONS:{_open_positions_count(state)}/{max_pos}",
                    "symbol": candidate.get("symbol"),
                }
            )
            break

        symbol = str(candidate["symbol"])
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)

        try:
            bal = exchange.fetch_balance()
            free_usdt = float((bal.get("free") or {}).get("USDT", 0.0) or 0.0)
        except Exception as e:
            one = {"action": "failed", "reason": f"BALANCE_FETCH:{e}", "symbol": symbol}
            attempts.append(one)
            _log_trade_attempt(state, cfg, one)
            break

        one = _try_open_candidate(exchange, state, cfg, candidate, free_usdt=free_usdt)
        attempts.append(one)
        _log_trade_attempt(state, cfg, one)

        if one.get("action") in ("executed", "dry_run"):
            opened.append(one)
            continue
        # Skip to next candidate on soft rejects (size, symbol open, notional min, etc.)
        if one.get("action") == "failed":
            break

    opened_count = len(opened)
    if opened_count > 0:
        action = "dry_run" if cfg.dry_run else "executed"
        symbols = [o.get("symbol") for o in opened]
        result = {
            "action": action,
            "reason": f"OPENED_{opened_count}",
            "opened_count": opened_count,
            "symbols": symbols,
            "symbol": symbols[0],
            "side": opened[0].get("side"),
            "trade_rank": opened[0].get("trade_rank"),
            "pick_from_top_n": cfg.pick_from_top_n,
            "attempts": attempts,
            **{k: opened[-1][k] for k in ("notional_usdt",) if k in opened[-1]},
        }
        print(f"[auto_trade] scan batch: opened {opened_count} — {', '.join(str(s) for s in symbols)}", flush=True)
    elif attempts:
        last = attempts[-1]
        result = {
            "action": last.get("action", "skipped"),
            "reason": last.get("reason"),
            "opened_count": 0,
            "symbol": last.get("symbol"),
            "side": last.get("side"),
            "trade_rank": last.get("trade_rank"),
            "attempts": attempts,
        }
        print(f"[auto_trade] {result.get('action')} {result.get('reason')}", flush=True)
    else:
        result = {"action": "skipped", "reason": pick_reason, "opened_count": 0, "attempts": []}

    save_trade_state(state)
    return result


def run_from_cache(yaml_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cached = load_scan_result(DEFAULT_CACHE_PATH)
    report = (cached or {}).get("report") or {}
    return maybe_run_auto_trade(report, yaml_cfg=yaml_cfg)
