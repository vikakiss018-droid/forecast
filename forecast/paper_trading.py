"""Бумажная симуляция: сетапы со score выше порога открываются как виртуальные
сделки и отслеживаются по реальным ценам (TP / стоп / таймаут).

Файл состояния: data/processed/paper_trades.json
Открытие: record_setups_from_report() — вызывается после каждого планового скана.
Обновление: update_open_trades() — по свежим OHLCV с Binance.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt

from .paths import PROCESSED_DATA_DIR, ensure_directories

PAPER_TRADES_PATH = PROCESSED_DATA_DIR / "paper_trades.json"

# Порог рейтинга сетапа для открытия симуляции
DEFAULT_MIN_SCORE = 30.0
# Виртуальный риск на сделку, USDT (0.5% от $1000)
DEFAULT_RISK_USDT = 5.0
# Максимум одновременно открытых симуляций
MAX_OPEN_TRADES = 40
# Не открывать новую сделку по паре, если прошлая закрыта менее N минут назад
REOPEN_COOLDOWN_MIN = 90
# Сколько закрытых сделок хранить
MAX_CLOSED_KEEP = 400

TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
# Таймаут удержания в барах — как в бэктестах (MAX_HOLD_BARS_BY_TF)
MAX_HOLD_BARS = {"5m": 120, "15m": 96, "30m": 72, "1h": 48, "2h": 36, "4h": 24, "1d": 14}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def paper_min_score() -> float:
    return _env_float("PAPER_MIN_SCORE", DEFAULT_MIN_SCORE)


def paper_risk_usdt() -> float:
    return _env_float("PAPER_RISK_USDT", DEFAULT_RISK_USDT)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_paper_state() -> dict[str, Any]:
    if not PAPER_TRADES_PATH.is_file():
        return {"trades": [], "updated_at": None}
    try:
        data = json.loads(PAPER_TRADES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("trades"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"trades": [], "updated_at": None}


def _save_paper_state(state: dict[str, Any]) -> None:
    ensure_directories()
    state["updated_at"] = _now_iso()
    tmp = PAPER_TRADES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PAPER_TRADES_PATH)


def _trim_closed(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") != "open"]
    closed.sort(key=lambda t: str(t.get("closed_at") or ""), reverse=True)
    return open_t + closed[:MAX_CLOSED_KEEP]


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def record_setups_from_report(
    report: dict[str, Any],
    *,
    min_score: float | None = None,
    opened_at: str | None = None,
) -> int:
    """Открыть симуляции для сетапов с score >= порога. Возвращает число открытых."""
    thr = paper_min_score() if min_score is None else float(min_score)
    setups = report.get("top_setups") or []
    if not setups:
        return 0
    timeframe = str(report.get("timeframe") or "15m")
    now = datetime.now(timezone.utc)
    opened_iso = opened_at or _now_iso()

    state = load_paper_state()
    trades = state["trades"]
    open_symbols = {str(t.get("symbol")) for t in trades if t.get("status") == "open"}
    n_open = len(open_symbols)

    recently_closed: set[str] = set()
    for t in trades:
        if t.get("status") == "open":
            continue
        closed_dt = _parse_ts(t.get("closed_at"))
        if closed_dt and (now - closed_dt).total_seconds() < REOPEN_COOLDOWN_MIN * 60:
            recently_closed.add(str(t.get("symbol")))

    opened = 0
    for row in setups:
        try:
            score = float(row.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if score < thr:
            continue
        symbol = str(row.get("symbol") or "").strip()
        plan = row.get("setup") or {}
        direction = str(plan.get("direction") or "").strip().lower()
        if not symbol or direction not in ("long", "short"):
            continue
        if symbol in open_symbols or symbol in recently_closed:
            continue
        if n_open + opened >= MAX_OPEN_TRADES:
            break
        try:
            entry = float(plan["entry"])
            stop = float(plan["stop"])
            tp = float(plan.get("target_2") or plan.get("target_1"))
        except (KeyError, TypeError, ValueError):
            continue
        if entry <= 0 or abs(entry - stop) <= 0:
            continue
        trades.append(
            {
                "id": uuid.uuid4().hex[:10],
                "symbol": symbol,
                "side": direction,
                "regime": row.get("regime"),
                "pattern": row.get("pattern"),
                "score": round(score, 1),
                "timeframe": timeframe,
                "entry": entry,
                "stop": stop,
                "tp": tp,
                "risk_reward": plan.get("risk_reward"),
                "opened_at": opened_iso,
                "status": "open",
                "last_price": entry,
                "unrealized_r": 0.0,
                "exit": None,
                "exit_reason": None,
                "closed_at": None,
                "r_multiple": None,
                "win": None,
            }
        )
        open_symbols.add(symbol)
        opened += 1

    if opened:
        state["trades"] = _trim_closed(trades)
        _save_paper_state(state)
    return opened


def _unrealized_r(side: str, entry: float, price: float, stop: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    if side == "long":
        return (price - entry) / risk
    return (entry - price) / risk


def _check_exit_on_bars(
    trade: dict[str, Any],
    bars: list[list[float]],
    opened_ms: int,
) -> tuple[str | None, float, float]:
    """Идём по закрытым барам после открытия. Возврат: (reason|None, exit_px, last_close).

    Консервативно: если в одном баре и стоп, и TP — считаем стоп.
    """
    side = str(trade["side"])
    stop = float(trade["stop"])
    tp = float(trade["tp"])
    tf_min = TF_MINUTES.get(str(trade.get("timeframe") or "15m"), 15)
    max_hold = MAX_HOLD_BARS.get(str(trade.get("timeframe") or "15m"), 96)
    last_close = float(trade.get("last_price") or trade["entry"])
    held = 0
    for row in bars:
        ts, _o, hi, lo, close = int(row[0]), row[1], float(row[2]), float(row[3]), float(row[4])
        if ts <= opened_ms:
            continue
        # только закрытые бары
        if ts + tf_min * 60_000 > int(time.time() * 1000):
            break
        held += 1
        last_close = close
        if side == "long":
            if lo <= stop:
                return "stop", stop, last_close
            if hi >= tp:
                return "tp", tp, last_close
        else:
            if hi >= stop:
                return "stop", stop, last_close
            if lo <= tp:
                return "tp", tp, last_close
        if held >= max_hold:
            return "time", close, last_close
    return None, 0.0, last_close


def update_open_trades(*, exchange: Any | None = None) -> dict[str, int]:
    """Обновить открытые симуляции по свежим ценам. Возвращает {'checked': n, 'closed': m}."""
    state = load_paper_state()
    open_trades = [t for t in state["trades"] if t.get("status") == "open"]
    if not open_trades:
        # фиксируем момент проверки, чтобы страница показывала свежесть
        _save_paper_state(state)
        return {"checked": 0, "closed": 0}

    ex = exchange or ccxt.binance({"enableRateLimit": True})
    closed = 0
    for trade in open_trades:
        opened_dt = _parse_ts(trade.get("opened_at"))
        if opened_dt is None:
            continue
        opened_ms = int(opened_dt.timestamp() * 1000)
        tf = str(trade.get("timeframe") or "15m")
        max_hold = MAX_HOLD_BARS.get(tf, 96)
        try:
            bars = ex.fetch_ohlcv(
                str(trade["symbol"]),
                timeframe=tf,
                since=opened_ms,
                limit=max_hold + 10,
            )
        except Exception:
            continue
        if not bars:
            continue
        reason, exit_px, last_close = _check_exit_on_bars(trade, bars, opened_ms)
        trade["last_price"] = last_close
        trade["unrealized_r"] = round(
            _unrealized_r(str(trade["side"]), float(trade["entry"]), last_close, float(trade["stop"])), 3
        )
        if reason is None:
            continue
        r = _unrealized_r(str(trade["side"]), float(trade["entry"]), exit_px, float(trade["stop"]))
        trade["status"] = "closed"
        trade["exit"] = exit_px
        trade["exit_reason"] = reason
        trade["closed_at"] = _now_iso()
        trade["r_multiple"] = round(r, 3)
        trade["win"] = r > 0
        trade["unrealized_r"] = None
        closed += 1

    state["trades"] = _trim_closed(state["trades"])
    _save_paper_state(state)
    return {"checked": len(open_trades), "closed": closed}


def paper_summary(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or load_paper_state()
    trades = state.get("trades") or []
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t.get("win")]
    losses = [t for t in closed if not t.get("win")]
    rs = [float(t.get("r_multiple") or 0) for t in closed]
    total_r = sum(rs)
    risk = paper_risk_usdt()
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
    return {
        "updated_at": state.get("updated_at"),
        "min_score": paper_min_score(),
        "risk_usdt": risk,
        "open": len(open_t),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
        "total_r": round(total_r, 2),
        "profit_factor": pf,
        "pnl_usdt": round(total_r * risk, 2),
        "unrealized_r": round(sum(float(t.get("unrealized_r") or 0) for t in open_t), 2),
    }
