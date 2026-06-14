"""Read/write allowed keys in project .env for the dashboard."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .paths import ENV_FILE, load_project_env

# Keys the panel may view/edit (no Binance secrets here).
EDITABLE_KEYS: tuple[str, ...] = (
    "AUTO_TRADE_ENABLED",
    "AUTO_TRADE_DRY_RUN",
    "AUTO_TRADE_MIN_SCORE",
    "AUTO_TRADE_MIN_PROB_PCT",
    "AUTO_TRADE_MIN_RR",
    "AUTO_TRADE_RISK_PCT",
    "AUTO_TRADE_MAX_NOTIONAL_USDT",
    "AUTO_TRADE_COOLDOWN_MINUTES",
    "AUTO_TRADE_LEVERAGE",
    "AUTO_TRADE_MARGIN_MODE",
    "AUTO_TRADE_TOP_N",
    "AUTO_TRADE_MAX_POSITIONS",
    "AUTO_TRADE_PROFIT_CLOSE_PCT",
    "AUTO_TRADE_STOP_LOSS_ROI_USDT",
    "AUTO_TRADE_ALLOW_LEVEL_BREAKOUT",
    "AUTO_TRADE_ALLOW_TRIANGLE",
    "AUTO_TRADE_ALLOWED_HOURS",
    "AUTO_TRADE_MIN_ATR_PCT",
    "FORECAST_TOP",
    "FORECAST_BARS",
    "FORECAST_TIMEFRAME",
    "FORECAST_STAGE1_MIN_SCORE",
    "FORECAST_USE_FILTERED",
    "FORECAST_USE_CLOSED_BAR",
    "FORECAST_LONG_ONLY",
    "FORECAST_MIN_PROB_PCT",
    "FORECAST_ALLOW_TREND",
    "FORECAST_ALLOW_RANGE",
    "AUTO_TRADE_MARKET",
    "AUTO_TRADE_SPOT_ALLOW_SHORT",
    "TREND_LOOKBACK",
    "TREND_MIN_MOVE_PCT",
    "TREND_MIN_REL_VOLUME",
    "TREND_MIN_ATR_PCT",
    "RANK_TOP_N",
    "RANK_TARGET_PER_SYMBOL",
    "RANK_STAGE1_RELAX_SCORE",
)

SETTINGS_META: list[dict[str, Any]] = [
    {"key": "AUTO_TRADE_ENABLED", "label": "Автоторговля включена", "type": "bool", "group": "trade"},
    {"key": "AUTO_TRADE_DRY_RUN", "label": "Dry-run (без ордеров)", "type": "bool", "group": "trade"},
    {"key": "AUTO_TRADE_MARKET", "label": "Рынок (spot / futures)", "type": "str", "group": "trade"},
    {
        "key": "AUTO_TRADE_SPOT_ALLOW_SHORT",
        "label": "Spot: разрешить short (не реализовано)",
        "type": "bool",
        "group": "trade",
    },
    {
        "key": "AUTO_TRADE_ALLOW_LEVEL_BREAKOUT",
        "label": "Вход на пробитие уровня (ретест/пробой)",
        "type": "bool",
        "group": "trade",
    },
    {
        "key": "AUTO_TRADE_ALLOW_TRIANGLE",
        "label": "Вход по паттерну triangle",
        "type": "bool",
        "group": "trade",
    },
    {
        "key": "AUTO_TRADE_ALLOWED_HOURS",
        "label": "Часы входа UTC (напр. 8-20, пусто=all)",
        "type": "str",
        "group": "trade",
    },
    {
        "key": "AUTO_TRADE_MIN_ATR_PCT",
        "label": "Min ATR % (0.008=0.8%, 0=выкл)",
        "type": "float",
        "group": "trade",
    },
    {"key": "AUTO_TRADE_TOP_N", "label": "Перебор топ-N сетапов", "type": "int", "group": "trade"},
    {"key": "AUTO_TRADE_MAX_POSITIONS", "label": "Макс. открытых позиций", "type": "int", "group": "trade"},
    {"key": "AUTO_TRADE_PROFIT_CLOSE_PCT", "label": "Закрытие при прибыли % от маржи", "type": "float", "group": "trade"},
    {
        "key": "AUTO_TRADE_STOP_LOSS_ROI_USDT",
        "label": "Стоп-лосс ROI (макс. убыток USDT, напр. 5)",
        "type": "float",
        "group": "trade",
    },
    {"key": "AUTO_TRADE_MIN_SCORE", "label": "Min score", "type": "float", "group": "trade"},
    {"key": "AUTO_TRADE_MIN_PROB_PCT", "label": "Min prob %", "type": "float", "group": "trade"},
    {"key": "AUTO_TRADE_MIN_RR", "label": "Min R:R", "type": "float", "group": "trade"},
    {"key": "AUTO_TRADE_RISK_PCT", "label": "Риск % баланса", "type": "float", "group": "trade"},
    {"key": "AUTO_TRADE_MAX_NOTIONAL_USDT", "label": "Max notional USDT", "type": "float", "group": "trade"},
    {"key": "AUTO_TRADE_COOLDOWN_MINUTES", "label": "Cooldown (мин)", "type": "int", "group": "trade"},
    {"key": "AUTO_TRADE_LEVERAGE", "label": "Плечо", "type": "int", "group": "trade"},
    {"key": "AUTO_TRADE_MARGIN_MODE", "label": "Маржа (isolated/cross)", "type": "str", "group": "trade"},
    {"key": "FORECAST_TOP", "label": "Скан: топ сетапов", "type": "int", "group": "scan"},
    {"key": "FORECAST_BARS", "label": "Скан: bars", "type": "int", "group": "scan"},
    {"key": "FORECAST_TIMEFRAME", "label": "Скан: таймфрейм", "type": "str", "group": "scan"},
    {"key": "FORECAST_STAGE1_MIN_SCORE", "label": "Скан: stage1 min", "type": "float", "group": "scan"},
    {"key": "FORECAST_USE_FILTERED", "label": "Скан: 50 filtered пар", "type": "bool", "group": "scan"},
    {"key": "FORECAST_USE_CLOSED_BAR", "label": "Скан: только закрытая 1h свеча", "type": "bool", "group": "scan"},
    {"key": "FORECAST_LONG_ONLY", "label": "Скан: только long", "type": "bool", "group": "scan"},
    {"key": "FORECAST_MIN_PROB_PCT", "label": "Скан: min prob %", "type": "float", "group": "scan"},
    {"key": "FORECAST_ALLOW_TREND", "label": "Скан: режим trend", "type": "bool", "group": "scan"},
    {"key": "FORECAST_ALLOW_RANGE", "label": "Скан: режим range", "type": "bool", "group": "scan"},
    {"key": "TREND_LOOKBACK", "label": "Тренд: lookback баров", "type": "int", "group": "scan"},
    {"key": "TREND_MIN_MOVE_PCT", "label": "Тренд: min move (0.008)", "type": "float", "group": "scan"},
    {"key": "TREND_MIN_REL_VOLUME", "label": "Тренд: min rel volume", "type": "float", "group": "scan"},
    {"key": "TREND_MIN_ATR_PCT", "label": "Тренд: min ATR %", "type": "float", "group": "scan"},
    {"key": "RANK_TOP_N", "label": "Тест пар: кол-во пар", "type": "int", "group": "rank"},
    {"key": "RANK_TARGET_PER_SYMBOL", "label": "Тест пар: сделок на пару", "type": "int", "group": "rank"},
    {"key": "RANK_STAGE1_RELAX_SCORE", "label": "Тест пар: stage1 relax", "type": "float", "group": "rank"},
]

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def read_env_values() -> dict[str, str]:
    load_project_env()
    out: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _ENV_LINE.match(line)
            if m:
                out[m.group(1)] = m.group(2).strip()
    for key in EDITABLE_KEYS:
        if key not in out and key in os.environ:
            out[key] = os.environ[key]
    return out


def get_settings_for_panel() -> list[dict[str, Any]]:
    values = read_env_values()
    rows: list[dict[str, Any]] = []
    for meta in SETTINGS_META:
        key = meta["key"]
        raw = values.get(key, "")
        rows.append({**meta, "value": raw})
    return rows


def _format_env_value(key: str, value: str, meta_type: str) -> str:
    v = value.strip()
    if meta_type == "bool":
        return "true" if v.lower() in ("1", "true", "yes", "on") else "false"
    if meta_type == "int":
        try:
            return str(int(float(v.replace(",", ".") or "0")))
        except ValueError as e:
            raise ValueError(f"{key}: нужно целое число") from e
    if meta_type == "float":
        try:
            return str(float(v.replace(",", ".") or "0"))
        except ValueError as e:
            raise ValueError(f"{key}: нужно число") from e
    return v


def update_env_values(updates: dict[str, str]) -> dict[str, str]:
    """Merge updates into .env; returns saved key->value."""
    meta_by_key = {m["key"]: m for m in SETTINGS_META}
    allowed = {k: _format_env_value(k, updates[k], meta_by_key[k]["type"]) for k in updates if k in meta_by_key}

    lines: list[str] = []
    seen: set[str] = set()
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = _ENV_LINE.match(raw.strip())
            if m and m.group(1) in allowed:
                key = m.group(1)
                lines.append(f"{key}={allowed[key]}")
                seen.add(key)
            else:
                lines.append(raw.rstrip("\n"))
    else:
        lines.append("# Forecast app environment")

    for key, val in allowed.items():
        if key not in seen:
            lines.append(f"{key}={val}")

    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).rstrip() + "\n"
    tmp = ENV_FILE.with_suffix(".env.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(ENV_FILE)
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass

    load_project_env(force=True)
    return allowed
