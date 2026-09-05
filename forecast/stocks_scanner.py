"""Скан лучших входов по токенизированным акциям Binance (bStocks).

Инструменты: spot-пары *B/USDT (TSLAB, NVDAB, QQQB, …), отсортированные
по объёму 24h. Для акций отключён BTC-regime фильтр — корреляция с BTC
не должна блокировать long/short по equity-токенам.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import ccxt

from .auto_trader import load_auto_trade_config
from .paths import CONFIGS_DIR, PROCESSED_DATA_DIR, ensure_directories
from .trend_scanner import TrendScanConfig, scan_combined_setups, trend_params_from_yaml

_log = logging.getLogger(__name__)

STOCKS_CACHE_PATH = PROCESSED_DATA_DIR / "stocks_scan_latest.json"
STOCKS_PROGRESS_PATH = PROCESSED_DATA_DIR / "stocks_scan_progress.json"

# Известные крипто-базы / не-акции, которые заканчиваются на B
_CRYPTO_FALSE_POSITIVES = frozenset(
    {
        "SHIB",
        "BNB",
        "ARB",
        "WBTC",
        "HBTC",
        "OBTC",
        "CBETH",
        "TBTC",
        "CKB",
        "DGB",
        "TRB",
        "QNTB",  # Quant (крипто), не акция
        "BB",  # BounceBit
        "BEB",
        "GSB",
        "YB",
        "SMHB",
        "STXB",
    }
)

# Человекочитаемые имена (базовый тикер без суффикса B)
_STOCK_NAMES: dict[str, str] = {
    "TSLA": "Tesla",
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "COIN": "Coinbase",
    "CRCL": "Circle",
    "MU": "Micron",
    "SNDK": "Sandisk",
    "SPCX": "SpaceX",
    "QQQ": "Invesco QQQ",
    "SPY": "SPDR S&P 500",
    "MSTR": "MicroStrategy",
    "GME": "GameStop",
    "HOOD": "Robinhood",
    "IBM": "IBM",
    "INTC": "Intel",
    "AMD": "AMD",
    "ARM": "Arm",
    "ORCL": "Oracle",
    "DELL": "Dell",
    "AVGO": "Broadcom",
    "MRVL": "Marvell",
    "SOXL": "Direxion Semi Bull",
    "SOXS": "Direxion Semi Bear",
    "DJT": "Trump Media",
    "BABA": "Alibaba",
    "ASML": "ASML",
    "NFLX": "Netflix",
    "PLTR": "Palantir",
    "PYPL": "PayPal",
    "TQQQ": "ProShares UltraPro QQQ",
    "SQQQ": "ProShares UltraPro Short QQQ",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def stock_display_name(symbol: str) -> str:
    """TSLAB/USDT → Tesla · TSLA."""
    base = symbol.split("/")[0].upper()
    if base.endswith("B") and len(base) > 1:
        ticker = base[:-1]
    else:
        ticker = base
    name = _STOCK_NAMES.get(ticker)
    if name:
        return f"{name} · {ticker}"
    return ticker


def is_bstock_base(base: str) -> bool:
    b = (base or "").upper()
    if b in _CRYPTO_FALSE_POSITIVES or not b.endswith("B"):
        return False
    ticker = b[:-1]
    # Тикеры акций обычно 2–5 букв
    if not (2 <= len(ticker) <= 5 and ticker.isalpha()):
        return False
    if ticker in _CRYPTO_FALSE_POSITIVES:
        return False
    return True


def discover_bstock_symbols(
    *,
    exchange: Any | None = None,
    limit: int | None = None,
    min_quote_volume: float | None = None,
) -> list[dict[str, Any]]:
    """Живые spot bStocks на Binance, по убыванию quote volume 24h."""
    lim = limit if limit is not None else _env_int("STOCKS_TOP_N", 40)
    min_vol = min_quote_volume if min_quote_volume is not None else _env_float(
        "STOCKS_MIN_QUOTE_VOL", 50_000.0
    )
    ex = exchange or ccxt.binance({"enableRateLimit": True})
    markets = ex.load_markets()
    candidates: list[str] = []
    for sym, m in markets.items():
        if not (m.get("spot") and m.get("active") and m.get("quote") == "USDT"):
            continue
        if not is_bstock_base(str(m.get("base") or "")):
            continue
        candidates.append(sym)
    if not candidates:
        return []

    tickers = ex.fetch_tickers(candidates)
    rows: list[dict[str, Any]] = []
    for sym in candidates:
        t = tickers.get(sym) or {}
        qv = float(t.get("quoteVolume") or 0.0)
        if qv < min_vol:
            continue
        base = sym.split("/")[0]
        ticker = base[:-1] if base.endswith("B") else base
        rows.append(
            {
                "symbol": sym,
                "base": base,
                "ticker": ticker,
                "name": stock_display_name(sym),
                "last": t.get("last"),
                "quote_volume": round(qv, 2),
                "change_pct": t.get("percentage"),
            }
        )
    rows.sort(key=lambda r: -float(r.get("quote_volume") or 0))
    return rows[: max(1, lim)]


def load_stocks_scan() -> dict[str, Any] | None:
    if not STOCKS_CACHE_PATH.is_file():
        return None
    try:
        data = json.loads(STOCKS_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def save_stocks_scan(payload: dict[str, Any]) -> None:
    ensure_directories()
    tmp = STOCKS_CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STOCKS_CACHE_PATH)


def save_stocks_progress(payload: dict[str, Any]) -> None:
    ensure_directories()
    STOCKS_PROGRESS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_stocks_progress() -> dict[str, Any]:
    if not STOCKS_PROGRESS_PATH.is_file():
        return {"status": "idle"}
    try:
        data = json.loads(STOCKS_PROGRESS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"status": "idle"}
    except (json.JSONDecodeError, OSError):
        return {"status": "error", "error": "bad progress file"}


def _auto_trade_yaml() -> dict:
    import yaml

    path = CONFIGS_DIR / "config.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("auto_trade") or {}


def run_stocks_scan(
    *,
    top: int | None = None,
    timeframe: str | None = None,
    stage1_min_score: float | None = None,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Скан лучших входов по топ-N bStocks."""
    tf = (timeframe or os.environ.get("STOCKS_TIMEFRAME") or "15m").strip() or "15m"
    top_n = top if top is not None else _env_int("STOCKS_RESULT_TOP", 15)
    s1 = stage1_min_score if stage1_min_score is not None else _env_float(
        "STOCKS_STAGE1_MIN", 16.0
    )

    universe = discover_bstock_symbols()
    symbols = tuple(r["symbol"] for r in universe)
    if not symbols:
        return {
            "status": "error",
            "error": "На Binance не найдены активные bStocks (*B/USDT)",
            "universe": [],
            "top_setups": [],
        }

    from dataclasses import replace

    from .trend_rules import trend_params_for_timeframe

    # Параметры под TF акций (не под основной крипто-скан)
    params = trend_params_for_timeframe(tf, base=trend_params_from_yaml())
    params = replace(
        params,
        min_rel_volume=min(float(params.min_rel_volume), 1.05),
        min_rel_volume_range=min(float(params.min_rel_volume_range), 0.9),
        require_htf_align=False,
        min_trend_move_pct=min(float(params.min_trend_move_pct), 0.012),
        # У акций ATR-стоп шире → фиксированный 4% TP даёт низкий R:R; целимся в R-кратный TP
        tp_target_pct=0.0,
        rr_target=max(float(params.rr_target), 1.5),
    )

    scan_cfg = TrendScanConfig(
        timeframe=tf,
        bars=0,
        top_n=max(1, top_n),
        stage1_min_score=s1,
        trend_params=params,
        use_filtered_symbols=False,
        symbols=symbols,
        long_only=False,
        use_closed_bar_only=True,
        allow_trend=True,
        allow_range=True,
        btc_regime_filter=False,  # не фильтруем акции по BTC
        min_bars=120,  # bStocks молодые — истории меньше, чем у крипты
    )
    auto_cfg = load_auto_trade_config(_auto_trade_yaml())
    auto_cfg = replace(auto_cfg, min_risk_reward=min(float(auto_cfg.min_risk_reward), 1.2))
    report = scan_combined_setups(
        symbols,
        scan_cfg=scan_cfg,
        auto_cfg=auto_cfg,
        progress_cb=progress_cb,
    )

    # Обогатить имя компании
    name_by_sym = {r["symbol"]: r for r in universe}
    for row in report.get("top_setups") or []:
        meta = name_by_sym.get(str(row.get("symbol")) or "")
        if meta:
            row["stock_name"] = meta.get("name")
            row["ticker"] = meta.get("ticker")
            row["quote_volume"] = meta.get("quote_volume")

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "universe": universe,
        "universe_count": len(universe),
        "scan_config": {
            "mode": "bstocks",
            "timeframe": tf,
            "stage1_min_score": s1,
            "top_n": top_n,
            "btc_regime_filter": False,
            "symbols_count": len(symbols),
        },
        "report": report,
    }
    save_stocks_scan(payload)
    return payload


def run_stocks_scan_background(
    *,
    top: int | None = None,
    timeframe: str | None = None,
    stage1_min_score: float | None = None,
) -> None:
    started = datetime.now(timezone.utc).isoformat()
    save_stocks_progress(
        {
            "status": "running",
            "kind": "stocks_scan",
            "started_at": started,
            "progress": {"current": 0, "total": 0, "symbol": None},
        }
    )

    def on_progress(p: dict[str, Any]) -> None:
        save_stocks_progress(
            {
                "status": "running",
                "kind": "stocks_scan",
                "started_at": started,
                "progress": p,
            }
        )

    try:
        payload = run_stocks_scan(
            top=top,
            timeframe=timeframe,
            stage1_min_score=stage1_min_score,
            progress_cb=on_progress,
        )
        if payload.get("status") == "error":
            save_stocks_progress(
                {
                    "status": "error",
                    "kind": "stocks_scan",
                    "error": payload.get("error"),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return
        rep = payload.get("report") or {}
        save_stocks_progress(
            {
                "status": "done",
                "kind": "stocks_scan",
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "candidates_found": rep.get("candidates_found"),
                "progress": {
                    "current": payload.get("universe_count") or 0,
                    "total": payload.get("universe_count") or 0,
                    "symbol": None,
                },
            }
        )
    except Exception as e:
        _log.exception("stocks scan failed")
        save_stocks_progress(
            {
                "status": "error",
                "kind": "stocks_scan",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
