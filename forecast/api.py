from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .main import load_config, run_pipeline
from .paths import CONFIGS_DIR, load_project_env
from .data_loader import download_ohlcv_to_csv, load_ohlcv_from_csv
from .indicators import add_basic_indicators
from .features import SIMILARITY_FEATURE_COLS, add_basic_features
from .backtest import BacktestConfig, run_simple_backtest
from .liquidity_model import hours_to_bars, liquidity_zone_and_volume, minutes_to_bars
from .similarity import SimilarityConfig, forecast_direction, forecast_neighbor_stats
from .signal_combiner import (
    detect_regime,
    detect_trend_direction,
    compute_liquidity_distance_score,
    compute_volume_scores,
    combine_probabilities,
)
from .orderbook_layer import (
    get_all_orderbook_snapshots,
    start_spot_orderbook_layer,
    top_live_symbols,
)
from .orderflow_stream import start_orderbook_stream, get_liquidity_snapshot
from .backtest_analytics import ev_bucket_label
from .ev_calibration import load_ev_calibration
from .trend_scanner import (
    SCAN_MODE,
    TrendScanConfig,
    _resolve_scan_symbols,
    scan_combined_setups,
    scan_trend_filtered_setups,
    trend_scan_config_from_env,
)
from .auto_trader import load_auto_trade_config
from .scan_cache import (
    load_scan_history,
    load_scan_progress,
    load_scan_result,
    report_from_cache,
    save_scan_progress,
    save_scan_result,
)
from .run_symbol_ranking import (
    approve_live_symbols,
    load_filtered_symbols,
    load_symbol_ranking_filtered,
    load_symbol_ranking_result,
    ranking_config_from_env,
    run_symbol_ranking_background,
)
from .env_config import SETTINGS_META, update_env_values
from .panel_auth import PANEL_AUTH_DEPS
from .paper_panel import render_paper_dashboard
from .paper_trading import (
    load_paper_state,
    paper_summary,
    record_setups_from_report,
    update_open_trades,
)
from .stocks_panel import render_stocks_dashboard
from .stocks_scanner import (
    load_stocks_progress,
    load_stocks_scan,
    run_stocks_scan_background,
)
from .scanner_panel import (
    render_pair_ranking_dashboard,
    render_scanner_dashboard,
)
from .trade_gate import GateMode, TradeGateConfig, evaluate_trade_gate
from .mobile_app import (
    mobile_icon_file,
    mobile_manifest_json,
    render_mobile_app,
    setups_payload,
)
from .push_alerts import (
    delete_expo_push_token,
    delete_push_subscription,
    get_vapid_public_key,
    save_expo_push_token,
    save_push_subscription,
)


load_project_env()

app = FastAPI(title="Forecast App")
_log = logging.getLogger(__name__)


def _form_field_values(form: Any, key: str) -> list[str]:
    if hasattr(form, "getlist"):
        return [str(v) for v in form.getlist(key)]
    if hasattr(form, "multi_items"):
        return [str(v) for k, v in form.multi_items() if k == key]
    raw = form.get(key)
    return [str(raw)] if raw is not None else []


def _trade_gate_bundle(tg: TradeGateConfig | None = None) -> tuple[TradeGateConfig, dict[str, float]]:
    tg = tg or TradeGateConfig()
    curve = load_ev_calibration(tg.ev_calibration_json_path) if tg.use_auto_ev_calibration else {}
    return tg, curve


def _snapshot_bar_trade_gate_series(df: pd.DataFrame) -> tuple[float, float, float, float, float, float, float]:
    """Last-bar inputs aligned with enriched backtest: volume, volume EMA20, RSI, close, EMA20/50, ATR."""
    vl = float(df["volume"].iloc[-1]) if "volume" in df.columns else float("nan")
    vma = float(df["volume_ema_20"].iloc[-1]) if "volume_ema_20" in df.columns else float("nan")
    rsi_b = float(df["rsi_14"].iloc[-1]) if "rsi_14" in df.columns else float("nan")
    cl = float(df["close"].iloc[-1]) if "close" in df.columns else float("nan")
    em20 = float(df["ema_20"].iloc[-1]) if "ema_20" in df.columns else float("nan")
    em50 = float(df["ema_50"].iloc[-1]) if "ema_50" in df.columns else float("nan")
    atr_abs = float(df["atr_14"].iloc[-1]) if "atr_14" in df.columns else float("nan")
    return vl, vma, rsi_b, cl, em20, em50, atr_abs


def _simple_symbol_snapshot(
    symbol: str,
    timeframe: str,
    limit: int,
    use_futures: bool,
    sim_cfg: SimilarityConfig,
    bt_cfg: BacktestConfig,
    trade_gate: TradeGateConfig | None = None,
):
    """Compute full forecast metrics for a given symbol (similar to main card)."""
    # 1–3. Download and load OHLCV
    csv_path = download_ohlcv_to_csv(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        use_futures=use_futures,
    )
    df = load_ohlcv_from_csv(csv_path)

    # 4–5. Indicators and features
    df = add_basic_indicators(df)
    df = add_basic_features(df)

    if df.empty:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "last_price": float("nan"),
            "last_time": "N/A",
            "prob_up": 0.5,
            "prob_down": 0.5,
            "change_1": 0.0,
            "change_24": 0.0,
            "trend_regime": "N/A",
            "vol_regime": "N/A",
            "prob_up_10m": 0.5,
            "prob_down_10m": 0.5,
            "low_price_10m": float("nan"),
            "high_price_10m": float("nan"),
            "prob_up_1h": 0.5,
            "prob_down_1h": 0.5,
            "low_price_1h": float("nan"),
            "high_price_1h": float("nan"),
            "prob_up_6": 0.5,
            "prob_down_6": 0.5,
            "low_price_6": float("nan"),
            "high_price_6": float("nan"),
            "bars_10m": 0,
            "bars_1h": 0,
            "bars_6h": 0,
            "trades": 0,
            "equity": float("nan"),
            "liq_low": float("nan"),
            "liq_high": float("nan"),
            "vol_24h": 0.0,
            "sb_hist_pct": 25.0,
            "sb_liq_pct": 25.0,
            "sb_vol_pct": 25.0,
            "sb_dist_pct": 25.0,
            "trade_gate_reason": "N/A",
            "trade_gate_reason_code": "NO_SIGNAL",
            "trade_idea_1h": "No trade",
            "trade_conf_1h": 0.0,
            "size_mult_1h": 0.0,
            "ev_1h": 0.0,
            "ev_adj_1h": 0.0,
            "effective_neighbors_1h": 0.0,
            "skew_1h": 0.0,
            "avg_up_move_1h": 0.0,
            "avg_down_move_1h": 0.0,
            "knn_regime": "N/A",
        }

    last_price = float(df["close"].iloc[-1])
    last_time = str(df.index[-1])

    # Liquidity zone & 24h volume approximation
    liq_low, liq_high, vol_24h = liquidity_zone_and_volume(df, timeframe)

    # 1-bar change
    if len(df) > 1:
        change_1 = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1.0) * 100.0
    else:
        change_1 = 0.0

    # 24-bar change
    if "ret_24" in df.columns:
        change_24 = float(df["ret_24"].iloc[-1] * 100.0)
    else:
        change_24 = change_1

    # Market regime
    if "ema_20" in df.columns and "ema_50" in df.columns:
        ema20 = float(df["ema_20"].iloc[-1])
        ema50 = float(df["ema_50"].iloc[-1])
        drift = 0.001
        if ema20 > ema50 * (1 + drift):
            trend_regime = "Uptrend"
        elif ema20 < ema50 * (1 - drift):
            trend_regime = "Downtrend"
        else:
            trend_regime = "Range"
    else:
        trend_regime = "N/A"

    if "volatility_24" in df.columns:
        vol = float(df["volatility_24"].iloc[-1])
        if vol < 0.5 / 100:
            vol_regime = "Low vol"
        elif vol < 1.5 / 100:
            vol_regime = "Normal vol"
        else:
            vol_regime = "High vol"
    else:
        vol_regime = "N/A"

    feature_cols = list(SIMILARITY_FEATURE_COLS)

    bars_10m = minutes_to_bars(10, timeframe)
    bars_1h = hours_to_bars(1, timeframe)
    bars_6h = hours_to_bars(6, timeframe)
    sim10 = replace(sim_cfg, forecast_horizon_bars=bars_10m)
    sim1h = replace(sim_cfg, forecast_horizon_bars=bars_1h)
    sim6 = replace(sim_cfg, forecast_horizon_bars=bars_6h)

    st10 = forecast_neighbor_stats(df, feature_cols, sim10)
    prob_up_10m = st10.directional_prob_up()
    prob_down_10m = 1.0 - prob_up_10m
    low_price_10m = last_price * (1.0 + st10.low_ret)
    high_price_10m = last_price * (1.0 + st10.high_ret)

    st1h = forecast_neighbor_stats(df, feature_cols, sim1h)
    prob_up_1h = st1h.directional_prob_up()
    prob_down_1h = 1.0 - prob_up_1h
    low_ret_1h, high_ret_1h = st1h.low_ret, st1h.high_ret
    low_price_1h = last_price * (1.0 + low_ret_1h)
    high_price_1h = last_price * (1.0 + high_ret_1h)

    st6 = forecast_neighbor_stats(df, feature_cols, sim6)
    prob_up_6 = st6.directional_prob_up()
    prob_down_6 = 1.0 - prob_up_6
    low_ret_6, high_ret_6 = st6.low_ret, st6.high_ret
    low_price_6 = last_price * (1.0 + low_ret_6)
    high_price_6 = last_price * (1.0 + high_ret_6)

    st_base = forecast_neighbor_stats(df, feature_cols, sim_cfg)
    prob_up = st_base.directional_prob_up()
    prob_down = 1.0 - prob_up

    gate_cfg, ev_curve = _trade_gate_bundle(trade_gate)

    # Signal breakdown components (use historical liquidity only for multi)
    regime = detect_regime(df)
    trend_direction = detect_trend_direction(df, drift=gate_cfg.trend_ema_drift)
    S_liq_up_hist, S_liq_down_hist, S_distance = compute_liquidity_distance_score(
        last_price, liq_low, liq_high
    )
    S_vol_up, S_vol_down = compute_volume_scores(df)

    comb_up_1h, comb_down_1h = combine_probabilities(
        prob_up_1h,
        prob_down_1h,
        S_liq_up_hist,
        S_liq_down_hist,
        S_distance,
        S_vol_up,
        S_vol_down,
        regime,
    )
    natr_last = float(df["natr_14"].iloc[-1]) if "natr_14" in df.columns else 0.0
    knn_gate = str(df["knn_regime"].iloc[-1]) if "knn_regime" in df.columns else regime
    ev_b = ev_bucket_label(st1h.ev)
    vol_last, vma, rsi_bar, _, em20_bar, em50_bar, atr_abs = _snapshot_bar_trade_gate_series(df)
    tg = evaluate_trade_gate(
        comb_up_1h=comb_up_1h,
        comb_down_1h=comb_down_1h,
        low_ret_1h=low_ret_1h,
        high_ret_1h=high_ret_1h,
        S_distance=S_distance,
        vol_regime=vol_regime,
        natr_14=natr_last,
        ev=st1h.ev,
        skew=st1h.skew,
        knn_regime=knn_gate,
        ev_bucket=ev_b,
        effective_neighbors=float(st1h.effective_neighbors),
        trend_direction=trend_direction,
        last_price=last_price,
        support_level=float(liq_low),
        resistance_level=float(liq_high),
        volume_last=vol_last,
        volume_sma20=vma,
        rsi_bar=rsi_bar,
        close_bar=last_price,
        ema_20_bar=em20_bar,
        ema_50_bar=em50_bar,
        atr_14_abs=atr_abs,
        gate_mode=GateMode.C,
        ev_calibration_curve=ev_curve,
        cfg=gate_cfg,
    )
    direction = tg.direction
    confidence = tg.confidence
    trade_gate_reason = f"{tg.reason_code}: " + "; ".join(tg.reasons)

    sb_hist = prob_up_1h
    sb_liq = S_liq_up_hist
    sb_vol = S_vol_up
    sb_dist = S_distance
    sb_total = sb_hist + sb_liq + sb_vol + sb_dist
    if sb_total > 0:
        sb_hist_pct = sb_hist / sb_total * 100.0
        sb_liq_pct = sb_liq / sb_total * 100.0
        sb_vol_pct = sb_vol / sb_total * 100.0
        sb_dist_pct = sb_dist / sb_total * 100.0
    else:
        sb_hist_pct = sb_liq_pct = sb_vol_pct = sb_dist_pct = 25.0

    # Simple backtest
    bt_df = run_simple_backtest(df, feature_cols, sim_cfg, bt_cfg)
    if not bt_df.empty:
        equity = float(bt_df["equity"].iloc[-1])
        trades = int((bt_df["position"] != 0).sum())
    else:
        equity = float("nan")
        trades = 0

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "last_price": last_price,
        "last_time": last_time,
        "prob_up": prob_up,
        "prob_down": prob_down,
        "change_1": change_1,
        "change_24": change_24,
        "trend_regime": trend_regime,
        "vol_regime": vol_regime,
        "prob_up_10m": prob_up_10m,
        "prob_down_10m": prob_down_10m,
        "low_price_10m": low_price_10m,
        "high_price_10m": high_price_10m,
        "bars_10m": bars_10m,
        "prob_up_1h": prob_up_1h,
        "prob_down_1h": prob_down_1h,
        "low_price_1h": low_price_1h,
        "high_price_1h": high_price_1h,
        "ev_1h": st1h.ev,
        "skew_1h": st1h.skew,
        "avg_up_move_1h": st1h.avg_up_move,
        "avg_down_move_1h": st1h.avg_down_move,
        "knn_regime": knn_gate,
        "effective_neighbors_1h": st1h.effective_neighbors,
        "ev_adj_1h": tg.ev_adj,
        "prob_up_6": prob_up_6,
        "prob_down_6": prob_down_6,
        "low_price_6": low_price_6,
        "high_price_6": high_price_6,
        "bars_1h": bars_1h,
        "bars_6h": bars_6h,
        "trades": trades,
        "equity": equity,
        "liq_low": liq_low,
        "liq_high": liq_high,
        "vol_24h": vol_24h,
        "sb_hist_pct": sb_hist_pct,
        "sb_liq_pct": sb_liq_pct,
        "sb_vol_pct": sb_vol_pct,
        "sb_dist_pct": sb_dist_pct,
        "trade_idea_1h": direction,
        "trade_conf_1h": confidence,
        "trade_gate_reason": trade_gate_reason,
        "trade_gate_reason_code": tg.reason_code,
        "size_mult_1h": tg.size_mult,
    }


@app.on_event("startup")
def _on_startup() -> None:
    loop = asyncio.get_event_loop()
    # Spot depth: top-N from live filtered list (OBI / microprice for scanner)
    loop.create_task(start_spot_orderbook_layer())
    # Legacy /legacy UI: futures liquidity snapshot (hardcoded majors)
    legacy_symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
        "XRPUSDT", "PEPEUSDT", "DOGEUSDT", "LINKUSDT",
    ]
    loop.create_task(start_orderbook_stream(legacy_symbols))


def _is_phone_request(
    request: Request,
    *,
    mobile: str | None = None,
    desktop: str | None = None,
) -> bool:
    if str(desktop or "") == "1":
        return False
    if str(mobile or "") == "1":
        return True
    ua = (request.headers.get("user-agent") or "").lower()
    return any(tok in ua for tok in ("iphone", "ipad", "android", "ipod", "windows phone", "mobile"))


@app.get("/", dependencies=PANEL_AUTH_DEPS)
def index_redirect(request: Request):
    """Phone opens the mobile app; desktop goes to the scanner."""
    if _is_phone_request(request):
        return HTMLResponse(render_mobile_app())
    return RedirectResponse(url="/scanner", status_code=302)


def _mobile_icon_response(name: str) -> FileResponse:
    path = mobile_icon_file(name)
    if path is None:
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(path, media_type="image/png")


def _service_worker_response(*, allowed: str) -> Response:
    path = mobile_icon_file("sw.js")
    if path is None:
        raise HTTPException(status_code=404, detail="sw not found")
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": allowed, "Cache-Control": "no-cache"},
    )


@app.get("/m", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
@app.get("/m/", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
@app.get("/mobile", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
@app.get("/app", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
def mobile_app_page() -> str:
    """Телефон: выгодные позиции и уведомления при score > порога."""
    return render_mobile_app()


@app.get("/sw.js")
def mobile_service_worker_root() -> Response:
    return _service_worker_response(allowed="/")


@app.get("/manifest.webmanifest")
def mobile_manifest_root() -> Response:
    return Response(content=mobile_manifest_json(), media_type="application/manifest+json")


@app.get("/apple-touch-icon.png")
def apple_touch_icon_root() -> FileResponse:
    return _mobile_icon_response("apple-touch-icon.png")


@app.get("/favicon.ico")
def favicon_root() -> FileResponse:
    return _mobile_icon_response("icon-192.png")


@app.get("/m/manifest.webmanifest")
def mobile_manifest() -> Response:
    return Response(content=mobile_manifest_json(), media_type="application/manifest+json")


@app.get("/m/sw.js")
def mobile_service_worker() -> Response:
    return _service_worker_response(allowed="/")


@app.get("/m/icon-192.png")
def mobile_icon_192() -> FileResponse:
    return _mobile_icon_response("icon-192.png")


@app.get("/m/icon-512.png")
def mobile_icon_512() -> FileResponse:
    return _mobile_icon_response("icon-512.png")


@app.get("/m/icon-maskable-512.png")
def mobile_icon_maskable() -> FileResponse:
    return _mobile_icon_response("icon-maskable-512.png")


@app.get("/m/apple-touch-icon.png")
def mobile_apple_touch_icon() -> FileResponse:
    return _mobile_icon_response("apple-touch-icon.png")


@app.get("/m/api/setups", dependencies=PANEL_AUTH_DEPS)
def mobile_setups() -> dict:
    return setups_payload()


@app.get("/m/api/vapid", dependencies=PANEL_AUTH_DEPS)
def mobile_vapid() -> dict:
    return {"publicKey": get_vapid_public_key()}


@app.post("/m/api/push/subscribe", dependencies=PANEL_AUTH_DEPS)
async def mobile_push_subscribe(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="ожидался JSON подписки")
    save_push_subscription(body)
    return {"ok": True}


@app.post("/m/api/push/unsubscribe", dependencies=PANEL_AUTH_DEPS)
async def mobile_push_unsubscribe(request: Request) -> dict:
    body = await request.json()
    endpoint = ""
    if isinstance(body, dict):
        endpoint = str(body.get("endpoint") or "")
    delete_push_subscription(endpoint)
    return {"ok": True}


@app.post("/m/api/expo/register", dependencies=PANEL_AUTH_DEPS)
async def mobile_expo_register(request: Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="ожидался JSON")
    token = str(body.get("token") or "").strip()
    platform = str(body.get("platform") or "").strip()
    try:
        save_expo_push_token(token, platform=platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/m/api/expo/unregister", dependencies=PANEL_AUTH_DEPS)
async def mobile_expo_unregister(request: Request) -> dict:
    body = await request.json()
    token = ""
    if isinstance(body, dict):
        token = str(body.get("token") or "")
    delete_expo_push_token(token)
    return {"ok": True}


@app.get("/legacy", response_class=HTMLResponse)
def index_legacy() -> str:
    """Legacy browser screen with current forecast and backtest summary."""
    config_path = "configs/config.yaml"
    cfg = load_config(config_path)

    df, bt_df, prob_up, prob_down = run_pipeline(config_path)

    symbol = cfg.symbol
    timeframe = cfg.timeframe

    if not df.empty:
        last_price = float(df["close"].iloc[-1])
        last_time = str(df.index[-1])

        liq_low, liq_high, vol_24h = liquidity_zone_and_volume(df, timeframe)

        # 1-bar change
        if len(df) > 1:
            change_1 = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1.0) * 100.0
        else:
            change_1 = 0.0

        # 24-bar change (we already compute ret_24 feature)
        if "ret_24" in df.columns:
            change_24 = float(df["ret_24"].iloc[-1] * 100.0)
        else:
            change_24 = change_1

        # Режим для UI/API: локально по EMA 20/50 и volatility_24 (см. README: целевая спецификация — EMA 50/200 на 4H).
        if "ema_20" in df.columns and "ema_50" in df.columns:
            ema20 = float(df["ema_20"].iloc[-1])
            ema50 = float(df["ema_50"].iloc[-1])
            drift = 0.001
            if ema20 > ema50 * (1 + drift):
                trend_regime = "Uptrend"
            elif ema20 < ema50 * (1 - drift):
                trend_regime = "Downtrend"
            else:
                trend_regime = "Range"
        else:
            trend_regime = "N/A"

        if "volatility_24" in df.columns:
            vol = float(df["volatility_24"].iloc[-1])
            if vol < 0.5 / 100:
                vol_regime = "Low vol"
            elif vol < 1.5 / 100:
                vol_regime = "Normal vol"
            else:
                vol_regime = "High vol"
        else:
            vol_regime = "N/A"
    else:
        last_price = float("nan")
        last_time = "N/A"
        liq_low, liq_high, vol_24h = float("nan"), float("nan"), 0.0
        change_1 = 0.0
        change_24 = 0.0
        trend_regime = "N/A"
        vol_regime = "N/A"

    # Price range forecast based on historical future returns distribution
    feature_cols = list(SIMILARITY_FEATURE_COLS)

    comb_up_10m = comb_down_10m = 0.5
    comb_up_1h = comb_down_1h = 0.5
    comb_up_6 = comb_down_6 = 0.5
    bars_10m = bars_1h = bars_6h = 0
    low_price_10m = high_price_10m = float("nan")
    low_price_1h = high_price_1h = float("nan")
    low_price_6 = high_price_6 = float("nan")
    liq_up_ob = liq_down_ob = 0.0
    trade_idea_1h = "No trade"
    trade_gate_note = "Insufficient data"
    trade_gate_code = "NO_SIGNAL"
    size_mult_1h = 0.0
    ev_1h = 0.0
    ev_adj_display = 0.0
    eff_neighbors_display = 0.0
    skew_1h = 0.0
    avg_up_1h = 0.0
    avg_down_1h = 0.0
    knn_regime_label = "N/A"

    if not df.empty:
        gate_cfg, ev_curve = _trade_gate_bundle(cfg.trade_gate)
        # Forecast on 10m, 1h and 6h horizons (converted to bars from timeframe)
        bars_10m = minutes_to_bars(10, timeframe)
        bars_1h = hours_to_bars(1, timeframe)
        bars_6h = hours_to_bars(6, timeframe)

        sim10 = replace(cfg.similarity, forecast_horizon_bars=bars_10m)
        sim1h = replace(cfg.similarity, forecast_horizon_bars=bars_1h)
        sim6 = replace(cfg.similarity, forecast_horizon_bars=bars_6h)

        st10 = forecast_neighbor_stats(df, feature_cols, sim10)
        prob_up_10m = st10.directional_prob_up()
        prob_down_10m = 1.0 - prob_up_10m
        low_price_10m = last_price * (1.0 + st10.low_ret)
        high_price_10m = last_price * (1.0 + st10.high_ret)

        st1h = forecast_neighbor_stats(df, feature_cols, sim1h)
        prob_up_1h = st1h.directional_prob_up()
        prob_down_1h = 1.0 - prob_up_1h
        low_ret_1h, high_ret_1h = st1h.low_ret, st1h.high_ret
        low_price_1h = last_price * (1.0 + low_ret_1h)
        high_price_1h = last_price * (1.0 + high_ret_1h)
        ev_1h = st1h.ev
        skew_1h = st1h.skew
        avg_up_1h = st1h.avg_up_move
        avg_down_1h = st1h.avg_down_move

        st6 = forecast_neighbor_stats(df, feature_cols, sim6)
        prob_up_6 = st6.directional_prob_up()
        prob_down_6 = 1.0 - prob_up_6
        low_ret_6, high_ret_6 = st6.low_ret, st6.high_ret
        low_price_6 = last_price * (1.0 + low_ret_6)
        high_price_6 = last_price * (1.0 + high_ret_6)

        # Combined probabilities using orderbook liquidity, historical liquidity zone,
        # distance, volume and historical signal
        regime = detect_regime(df)
        trend_direction = detect_trend_direction(df, drift=gate_cfg.trend_ema_drift)

        # Live orderbook liquidity from Binance Futures
        ob_symbol = cfg.symbol.replace("/", "").upper()
        liq_up_ob, liq_down_ob = get_liquidity_snapshot(ob_symbol, last_price, pct=0.01)
        total_liq_ob = liq_up_ob + liq_down_ob
        if total_liq_ob > 0:
            S_liq_up_ob = liq_up_ob / total_liq_ob
            S_liq_down_ob = liq_down_ob / total_liq_ob
        else:
            S_liq_up_ob = S_liq_down_ob = 0.5

        # Historical liquidity zone
        S_liq_up_hist, S_liq_down_hist, S_distance = compute_liquidity_distance_score(
            last_price, liq_low, liq_high
        )

        # Mix live and historical liquidity (weights can be tuned)
        S_liq_up = 0.5 * S_liq_up_hist + 0.5 * S_liq_up_ob
        S_liq_down = 0.5 * S_liq_down_hist + 0.5 * S_liq_down_ob
        S_vol_up, S_vol_down = compute_volume_scores(df)

        comb_up_10m, comb_down_10m = combine_probabilities(
            prob_up_10m,
            prob_down_10m,
            S_liq_up,
            S_liq_down,
            S_distance,
            S_vol_up,
            S_vol_down,
            regime,
        )
        comb_up_1h, comb_down_1h = combine_probabilities(
            prob_up_1h,
            prob_down_1h,
            S_liq_up,
            S_liq_down,
            S_distance,
            S_vol_up,
            S_vol_down,
            regime,
        )
        comb_up_6, comb_down_6 = combine_probabilities(
            prob_up_6,
            prob_down_6,
            S_liq_up,
            S_liq_down,
            S_distance,
            S_vol_up,
            S_vol_down,
            regime,
        )

        natr_last = float(df["natr_14"].iloc[-1]) if "natr_14" in df.columns else 0.0
        knn_regime_label = str(df["knn_regime"].iloc[-1]) if "knn_regime" in df.columns else regime
        ev_b = ev_bucket_label(st1h.ev)
        vol_last, vma, rsi_bar, _, em20_bar, em50_bar, atr_abs = _snapshot_bar_trade_gate_series(df)
        tg = evaluate_trade_gate(
            comb_up_1h=comb_up_1h,
            comb_down_1h=comb_down_1h,
            low_ret_1h=low_ret_1h,
            high_ret_1h=high_ret_1h,
            S_distance=S_distance,
            vol_regime=vol_regime,
            natr_14=natr_last,
            ev=st1h.ev,
            skew=st1h.skew,
            knn_regime=knn_regime_label,
            ev_bucket=ev_b,
            effective_neighbors=float(st1h.effective_neighbors),
            trend_direction=trend_direction,
            last_price=last_price,
            support_level=float(liq_low),
            resistance_level=float(liq_high),
            volume_last=vol_last,
            volume_sma20=vma,
            rsi_bar=rsi_bar,
            close_bar=last_price,
            ema_20_bar=em20_bar,
            ema_50_bar=em50_bar,
            atr_14_abs=atr_abs,
            gate_mode=GateMode.C,
            ev_calibration_curve=ev_curve,
            cfg=gate_cfg,
        )
        trade_idea_1h = tg.direction
        trade_gate_note = f"{tg.reason_code}: " + "; ".join(tg.reasons)
        trade_gate_code = tg.reason_code
        size_mult_1h = tg.size_mult
        ev_adj_display = tg.ev_adj
        eff_neighbors_display = st1h.effective_neighbors

        # Signal breakdown (use 1h horizon as reference)
        sb_hist = prob_up_1h
        sb_liq = S_liq_up
        sb_vol = S_vol_up
        sb_dist = S_distance
        sb_total = sb_hist + sb_liq + sb_vol + sb_dist
        if sb_total > 0:
            sb_hist_pct = sb_hist / sb_total * 100.0
            sb_liq_pct = sb_liq / sb_total * 100.0
            sb_vol_pct = sb_vol / sb_total * 100.0
            sb_dist_pct = sb_dist / sb_total * 100.0
        else:
            sb_hist_pct = sb_liq_pct = sb_vol_pct = sb_dist_pct = 25.0
    else:
        prob_up_1h = prob_down_1h = 0.5
        prob_up_6 = prob_down_6 = 0.5
        sb_hist_pct = sb_liq_pct = sb_vol_pct = sb_dist_pct = 25.0

    if not bt_df.empty:
        final_equity = float(bt_df["equity"].iloc[-1])
        trades = int((bt_df["position"] != 0).sum())
    else:
        final_equity = float("nan")
        trades = 0

    # Use combined probabilities for UI display
    prob_up_pct_10m = comb_up_10m * 100.0
    prob_down_pct_10m = comb_down_10m * 100.0
    prob_up_pct_1h = comb_up_1h * 100.0
    prob_down_pct_1h = comb_down_1h * 100.0
    prob_up_pct_6 = comb_up_6 * 100.0
    prob_down_pct_6 = comb_down_6 * 100.0

    if final_equity != final_equity:  # NaN check
        equity_pct_str = "N/A"
    else:
        equity_pct = (final_equity - 1.0) * 100.0
        sign = "+" if equity_pct >= 0 else ""
        equity_pct_str = f"{sign}{equity_pct:.1f}%"

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>Forecast App</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0b1020;
                color: #f4f4f4;
                margin: 0;
                padding: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
            }}
            .card {{
                background: #161b2e;
                border-radius: 16px;
                padding: 24px 32px;
                box-shadow: 0 18px 45px rgba(0,0,0,0.35);
                max-width: 520px;
                width: 100%;
            }}
            h1 {{
                margin-top: 0;
                margin-bottom: 12px;
                font-size: 24px;
            }}
            .subtitle {{
                color: #9aa4c6;
                font-size: 14px;
                margin-bottom: 20px;
            }}
            .section-title {{
                margin-top: 18px;
                margin-bottom: 6px;
                font-size: 13px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #707aa3;
            }}
            .metric-row {{
                display: flex;
                justify-content: space-between;
                margin-bottom: 8px;
                font-size: 15px;
            }}
            .label {{
                color: #9aa4c6;
            }}
            .value-strong {{
                font-weight: 600;
            }}
            .badge-up {{
                color: #27e58b;
            }}
            .badge-down {{
                color: #ff5c7a;
            }}
            .footer {{
                margin-top: 18px;
                font-size: 12px;
                color: #737da0;
            }}
            .btn-refresh {{
                margin-top: 18px;
                background: linear-gradient(135deg, #2563eb, #38bdf8);
                border: none;
                border-radius: 999px;
                padding: 8px 18px;
                color: white;
                font-size: 13px;
                cursor: pointer;
            }}
            .btn-refresh:hover {{
                opacity: 0.92;
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Forecast snapshot</h1>
            <div class="subtitle">{symbol} · {timeframe} · similarity-based direction forecast (reload page to update).</div>

            <div class="section-title">Market snapshot</div>
            <div class="metric-row">
                <span class="label">Last close</span>
                <span class="value-strong">{last_price:.2f}</span>
            </div>
            <div class="metric-row">
                <span class="label">Change (last bar)</span>
                <span class="value-strong">{change_1:.2f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Change (~24 bars)</span>
                <span class="value-strong">{change_24:.2f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Last update</span>
                <span class="value-strong">{last_time}</span>
            </div>

            <div class="section-title">Forecast</div>
            <div class="metric-row">
                <span class="label">10m: ↑ Up</span>
                <span class="value-strong badge-up">{prob_up_pct_10m:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">10m: ↓ Down</span>
                <span class="value-strong badge-down">{prob_down_pct_10m:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">10m range (~{bars_10m} bars)</span>
                <span class="value-strong">{low_price_10m:.2f} – {high_price_10m:.2f}</span>
            </div>

            <div class="metric-row">
                <span class="label">1h: ↑ Up</span>
                <span class="value-strong badge-up">{prob_up_pct_1h:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">1h: ↓ Down</span>
                <span class="value-strong badge-down">{prob_down_pct_1h:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">1h range (~{bars_1h} bars)</span>
                <span class="value-strong">{low_price_1h:.2f} – {high_price_1h:.2f}</span>
            </div>
            <div class="metric-row">
                <span class="label">1h EV (weighted neighbors)</span>
                <span class="value-strong">{ev_1h * 100:.3f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">1h EV adj (after bucket calib)</span>
                <span class="value-strong">{ev_adj_display * 100:.3f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">1h effective neighbor count (Kish)</span>
                <span class="value-strong">{eff_neighbors_display:.1f}</span>
            </div>
            <div class="metric-row">
                <span class="label">1h skew (avg up / avg down)</span>
                <span class="value-strong">{skew_1h:.2f}</span>
            </div>
            <div class="metric-row">
                <span class="label">1h avg up / avg down move</span>
                <span class="value-strong">{avg_up_1h * 100:.3f}% / {avg_down_1h * 100:.3f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">kNN regime (model bucket)</span>
                <span class="value-strong">{html.escape(str(knn_regime_label))}</span>
            </div>

            <div class="metric-row">
                <span class="label">6h: ↑ Up</span>
                <span class="value-strong badge-up">{prob_up_pct_6:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">6h: ↓ Down</span>
                <span class="value-strong badge-down">{prob_down_pct_6:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">6h range (~{bars_6h} bars)</span>
                <span class="value-strong">{low_price_6:.2f} – {high_price_6:.2f}</span>
            </div>

            <div class="section-title">Backtest</div>
            <div class="metric-row">
                <span class="label">Trades</span>
                <span class="value-strong">{trades}</span>
            </div>
            <div class="metric-row">
                <span class="label">Equity</span>
                <span class="value-strong">{equity_pct_str}</span>
            </div>

            <div class="section-title">Liquidity & volume</div>
            <div class="metric-row">
                <span class="label">24h volume (approx)</span>
                <span class="value-strong">{vol_24h:.0f}</span>
            </div>

            <div class="section-title">Signal breakdown (1h up)</div>
            <div class="metric-row">
                <span class="label">Hist</span>
                <span class="value-strong">{sb_hist_pct:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Liquidity</span>
                <span class="value-strong">{sb_liq_pct:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Volume</span>
                <span class="value-strong">{sb_vol_pct:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Distance</span>
                <span class="value-strong">{sb_dist_pct:.0f}%</span>
            </div>
            <div class="metric-row">
                <span class="label">Liquidity zone</span>
                <span class="value-strong">{liq_low:.2f} – {liq_high:.2f}</span>
            </div>
            <div class="metric-row">
                <span class="label">Orderbook liq up (w)</span>
                <span class="value-strong">{liq_up_ob:.0f}</span>
            </div>
            <div class="metric-row">
                <span class="label">Orderbook liq down (w)</span>
                <span class="value-strong">{liq_down_ob:.0f}</span>
            </div>

            <div class="section-title">Market regime</div>
            <div class="metric-row">
                <span class="label">Trend</span>
                <span class="value-strong">{trend_regime}</span>
            </div>
            <div class="metric-row">
                <span class="label">Volatility</span>
                <span class="value-strong">{vol_regime}</span>
            </div>

            <div class="section-title">Trade gate (1h combined)</div>
            <div class="metric-row">
                <span class="label">Direction</span>
                <span class="value-strong">{trade_idea_1h}</span>
            </div>
            <div class="metric-row">
                <span class="label">Gate detail</span>
                <span class="value-strong" style="max-width: 65%; text-align: right; font-size: 12px;">{html.escape(trade_gate_note)}</span>
            </div>
            <div class="metric-row">
                <span class="label">Gate code</span>
                <span class="value-strong">{html.escape(str(trade_gate_code))}</span>
            </div>
            <div class="metric-row">
                <span class="label">Size mult (1h)</span>
                <span class="value-strong">{size_mult_1h:.2f}</span>
            </div>

            <button class="btn-refresh" onclick="window.location.reload()">Run forecast again</button>

            <div class="footer">
                This is a demo view. Logic is based on historical similarity windows and a simple walk-forward backtest.
            </div>
        </div>
    </body>
    </html>
    """
    return html


@app.get("/multi", response_class=HTMLResponse)
def multi() -> str:
    """Show simple forecasts for multiple symbols on one screen."""
    config_path = "configs/config.yaml"
    cfg = load_config(config_path)

    symbols = [
        cfg.symbol,
        "ETH/USDT",
        "SOL/USDT",
        "BNB/USDT",
        "XRP/USDT",
        "PEPE/USDT",
        "DOGE/USDT",
        "LINK/USDT",
    ]
    # Remove duplicates while preserving order
    seen = set()
    unique_symbols = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique_symbols.append(s)

    sim_cfg = cfg.similarity
    snapshots = [
        _simple_symbol_snapshot(
            symbol, cfg.timeframe, cfg.limit, cfg.use_futures, sim_cfg, cfg.backtest, trade_gate=cfg.trade_gate
        )
        for symbol in unique_symbols
    ]

    # Build cards HTML
    cards_html_parts = []
    for snap in snapshots:
        prob_up_pct = snap["prob_up"] * 100.0
        prob_down_pct = snap["prob_down"] * 100.0
        prob_up_pct_1h = snap["prob_up_1h"] * 100.0
        prob_down_pct_1h = snap["prob_down_1h"] * 100.0
        prob_up_pct_6 = snap["prob_up_6"] * 100.0
        prob_down_pct_6 = snap["prob_down_6"] * 100.0

        sb_hist_pct = snap["sb_hist_pct"]
        sb_liq_pct = snap["sb_liq_pct"]
        sb_vol_pct = snap["sb_vol_pct"]
        sb_dist_pct = snap["sb_dist_pct"]
        trade_idea_1h = snap["trade_idea_1h"]
        trade_conf_1h = snap["trade_conf_1h"]
        trade_gate_reason_esc = html.escape(snap.get("trade_gate_reason", "N/A"))

        equity = snap["equity"]
        if equity != equity:
            equity_pct_str = "N/A"
        else:
            equity_pct = (equity - 1.0) * 100.0
            sign = "+" if equity_pct >= 0 else ""
            equity_pct_str = f"{sign}{equity_pct:.1f}%"
        cards_html_parts.append(
            f"""
            <div class="card">
                <h1>{snap['symbol']}</h1>
                <div class="subtitle">{snap['timeframe']} · similarity forecast</div>

                <div class="section-title">Market snapshot</div>
                <div class="metric-row">
                    <span class="label">Last close</span>
                    <span class="value-strong">{snap['last_price']:.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Change (last bar)</span>
                    <span class="value-strong">{snap['change_1']:.2f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Change (~24 bars)</span>
                    <span class="value-strong">{snap['change_24']:.2f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Last update</span>
                    <span class="value-strong">{snap['last_time']}</span>
                </div>

                <div class="section-title">Forecast</div>

                <div class="section-title">Trade idea (1h)</div>
                <div class="metric-row">
                    <span class="label">Direction</span>
                    <span class="value-strong">{trade_idea_1h}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Confidence</span>
                    <span class="value-strong">{trade_conf_1h:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Gate detail</span>
                    <span class="value-strong" style="max-width: 60%; text-align: right; font-size: 12px;">{trade_gate_reason_esc}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Gate code</span>
                    <span class="value-strong">{html.escape(str(snap.get("trade_gate_reason_code", "N/A")))}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Size mult</span>
                    <span class="value-strong">{snap.get("size_mult_1h", 0.0):.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="label">1h EV</span>
                    <span class="value-strong">{snap.get("ev_1h", 0.0) * 100:.3f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">1h skew</span>
                    <span class="value-strong">{snap.get("skew_1h", 0.0):.2f}</span>
                </div>
                <div class="metric-row">
                    <span class="label">kNN regime</span>
                    <span class="value-strong">{html.escape(str(snap.get("knn_regime", "N/A")))}</span>
                </div>

                <div class="metric-row">
                    <span class="label">1h: ↑ Up</span>
                    <span class="value-strong badge-up">{prob_up_pct_1h:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">1h: ↓ Down</span>
                    <span class="value-strong badge-down">{prob_down_pct_1h:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">1h range (~{snap['bars_1h']} bars)</span>
                    <span class="value-strong">{snap['low_price_1h']:.2f} – {snap['high_price_1h']:.2f}</span>
                </div>

                <div class="metric-row">
                    <span class="label">6h: ↑ Up</span>
                    <span class="value-strong badge-up">{prob_up_pct_6:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">6h: ↓ Down</span>
                    <span class="value-strong badge-down">{prob_down_pct_6:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">6h range (~{snap['bars_6h']} bars)</span>
                    <span class="value-strong">{snap['low_price_6']:.2f} – {snap['high_price_6']:.2f}</span>
                </div>

                <div class="section-title">Backtest</div>
                <div class="metric-row">
                    <span class="label">Trades</span>
                    <span class="value-strong">{snap['trades']}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Equity</span>
                    <span class="value-strong">{equity_pct_str}</span>
                </div>

                <div class="section-title">Liquidity & volume</div>
                <div class="metric-row">
                    <span class="label">24h volume (approx)</span>
                    <span class="value-strong">{snap['vol_24h']:.0f}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Liquidity zone</span>
                    <span class="value-strong">{snap['liq_low']:.2f} – {snap['liq_high']:.2f}</span>
                </div>

                <div class="section-title">Signal breakdown (1h up)</div>
                <div class="metric-row">
                    <span class="label">Hist</span>
                    <span class="value-strong">{sb_hist_pct:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Liquidity</span>
                    <span class="value-strong">{sb_liq_pct:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Volume</span>
                    <span class="value-strong">{sb_vol_pct:.0f}%</span>
                </div>
                <div class="metric-row">
                    <span class="label">Distance</span>
                    <span class="value-strong">{sb_dist_pct:.0f}%</span>
                </div>

                <div class="section-title">Market regime</div>
                <div class="metric-row">
                    <span class="label">Trend</span>
                    <span class="value-strong">{snap['trend_regime']}</span>
                </div>
                <div class="metric-row">
                    <span class="label">Volatility</span>
                    <span class="value-strong">{snap['vol_regime']}</span>
                </div>
            </div>
            """
        )

    cards_html = "\n".join(cards_html_parts)

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8" />
        <title>Forecast App - Multi</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0b1020;
                color: #f4f4f4;
                margin: 0;
                padding: 24px;
            }}
            .cards-container {{
                display: flex;
                flex-wrap: wrap;
                gap: 20px;
                justify-content: center;
                align-items: flex-start;
            }}
            .card {{
                background: #161b2e;
                border-radius: 16px;
                padding: 18px 22px;
                box-shadow: 0 18px 45px rgba(0,0,0,0.35);
                width: 280px;
            }}
            h1 {{
                margin-top: 0;
                margin-bottom: 8px;
                font-size: 20px;
            }}
            .subtitle {{
                color: #9aa4c6;
                font-size: 13px;
                margin-bottom: 14px;
            }}
            .section-title {{
                margin-top: 12px;
                margin-bottom: 4px;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.08em;
                color: #707aa3;
            }}
            .metric-row {{
                display: flex;
                justify-content: space-between;
                margin-bottom: 6px;
                font-size: 13px;
            }}
            .label {{
                color: #9aa4c6;
            }}
            .value-strong {{
                font-weight: 600;
            }}
            .badge-up {{
                color: #27e58b;
            }}
            .badge-down {{
                color: #ff5c7a;
            }}
        </style>
    </head>
    <body>
        <div class="cards-container">
            {cards_html}
        </div>
    </body>
    </html>
    """
    return html


def _trend_scan_cfg_from_request(
    *,
    top: int,
    bars: int,
    timeframe: str,
    stage1_min_score: float,
) -> TrendScanConfig:
    """Параметры live-скана: base из .env/yaml + overrides из формы/query."""
    base = trend_scan_config_from_env()
    return TrendScanConfig(
        timeframe=(timeframe or base.timeframe).strip() or base.timeframe,
        bars=int(bars) if bars > 0 else base.bars,
        top_n=int(top) if top > 0 else base.top_n,
        stage1_min_score=float(stage1_min_score),
        min_probability_pct=base.min_probability_pct,
        trend_params=base.trend_params,
        use_filtered_symbols=base.use_filtered_symbols,
        universe_top_n=base.universe_top_n,
        symbols=base.symbols,
        long_only=base.long_only,
        use_closed_bar_only=base.use_closed_bar_only,
        allow_trend=base.allow_trend,
        allow_range=base.allow_range,
        btc_regime_filter=base.btc_regime_filter,
    )


def _scanner_report(
    *,
    top: int,
    bars: int,
    timeframe: str,
    stage1_min_score: float,
    max_symbols: int | None,
    live: bool,
) -> tuple[dict, str | None, bool]:
    """Return (report, updated_at, from_cache)."""
    _ = max_symbols
    if not live:
        cached = load_scan_result()
        if cached is not None:
            rep, updated = report_from_cache(cached, top=top)
            rep["cache_source"] = True
            return rep, updated, True

    scan_cfg = _trend_scan_cfg_from_request(
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
    )
    auto_cfg = load_auto_trade_config(_auto_trade_yaml())
    rep = scan_trend_filtered_setups(scan_cfg, auto_cfg=auto_cfg)
    if rep.get("status") == "error":
        rep = {
            "mode": "trend_plus_range",
            "top_setups": [],
            "candidates_found": 0,
            "error": rep.get("error"),
            "symbols_scanned": 0,
        }
    rep["cache_source"] = False
    return rep, None, False


def _run_live_scan_background(
    *,
    top: int,
    bars: int,
    timeframe: str,
    stage1_min_score: float,
) -> None:
    from datetime import datetime, timezone

    scan_cfg = _trend_scan_cfg_from_request(
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
    )
    try:
        symbols = _resolve_scan_symbols(scan_cfg)
    except Exception as e:
        save_scan_progress(
            {
                "status": "error",
                "kind": "live_scan",
                "error": f"нет символов: {e}",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return
    if not symbols:
        save_scan_progress(
            {
                "status": "error",
                "kind": "live_scan",
                "error": "нет символов для скана (проверьте FORECAST_USE_FILTERED / FORECAST_SCAN_TOP_N)",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return
    scan_cfg.symbols = symbols

    started_at = datetime.now(timezone.utc).isoformat()
    save_scan_progress(
        {
            "status": "running",
            "kind": "live_scan",
            "started_at": started_at,
            "progress": {"current": 0, "total": len(symbols), "symbol": None},
        }
    )
    auto_cfg = load_auto_trade_config(_auto_trade_yaml())

    def on_progress(p: dict[str, Any]) -> None:
        save_scan_progress(
            {
                "status": "running",
                "kind": "live_scan",
                "started_at": started_at,
                "progress": p,
            }
        )

    try:
        rep = scan_combined_setups(
            symbols,
            scan_cfg=scan_cfg,
            auto_cfg=auto_cfg,
            progress_cb=on_progress,
        )
        save_scan_result(
            rep,
            scan_config={
                "mode": SCAN_MODE,
                "timeframe": scan_cfg.timeframe,
                "bars": scan_cfg.bars or None,
                "top_n": scan_cfg.top_n,
                "stage1_min_score": scan_cfg.stage1_min_score,
                "symbols_count": len(symbols),
                "use_filtered": scan_cfg.use_filtered_symbols,
                "universe_top_n": scan_cfg.universe_top_n,
                "allow_trend": scan_cfg.allow_trend,
                "allow_range": scan_cfg.allow_range,
                "long_only": scan_cfg.long_only,
                "scan_duration_sec": rep.get("scan_duration_sec"),
                "candidates_found": rep.get("candidates_found"),
                "candidates_by_regime": rep.get("candidates_by_regime"),
            },
        )
        save_scan_progress(
            {
                "status": "done",
                "kind": "live_scan",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "progress": {"current": len(symbols), "total": len(symbols), "symbol": None},
                "candidates_found": rep.get("candidates_found"),
            }
        )
        try:
            update_open_trades()
            record_setups_from_report(rep)
        except Exception:
            _log.exception("paper trading update after live scan failed")
    except Exception as e:
        save_scan_progress(
            {
                "status": "error",
                "kind": "live_scan",
                "error": str(e),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        print(f"[scanner] live scan error: {e}", flush=True)


def _auto_trade_yaml() -> dict:
    import yaml as _yaml

    with open(CONFIGS_DIR / "config.yaml", encoding="utf-8") as f:
        return (_yaml.safe_load(f) or {}).get("auto_trade") or {}


@app.get("/scanner/json", dependencies=PANEL_AUTH_DEPS)
def scanner_json(
    top: int = 20,
    bars: int = 1000,
    timeframe: str = "1h",
    stage1_min_score: float = 18.0,
    max_symbols: int | None = None,
    live: bool = False,
) -> dict:
    """Тренд-скан 50 filtered пар (cached unless live=1)."""
    rep, updated_at, from_cache = _scanner_report(
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
        max_symbols=max_symbols,
        live=live,
    )
    if updated_at:
        rep["updated_at"] = updated_at
    rep["from_cache"] = from_cache
    cached_full = load_scan_result()
    rep["scan_history"] = load_scan_history(30)
    if cached_full:
        rep["scan_config"] = cached_full.get("scan_config") or {}
    return rep


@app.post("/scanner/settings", dependencies=PANEL_AUTH_DEPS)
async def save_scanner_settings(request: Request) -> RedirectResponse:
    """Save dashboard form fields to /opt/forecast/.env."""
    return_q = ""
    try:
        form = await request.form()
        return_q = str(form.get("return_q", "")).strip()
        updates: dict[str, str] = {}
        for meta in SETTINGS_META:
            key = meta["key"]
            if meta.get("type") == "bool":
                raw_list = _form_field_values(form, key)
                updates[key] = "true" if any(v.lower() == "true" for v in raw_list) else "false"
            else:
                vals = _form_field_values(form, key)
                if vals:
                    updates[key] = vals[-1]
        update_env_values(updates)
        msg = "saved=1"
    except OSError as e:
        _log.exception("save .env failed (os)")
        msg = f"error={quote(str(e), safe='')}"
    except ValueError as e:
        _log.warning("save .env invalid value: %s", e)
        msg = f"error={quote(str(e), safe='')}"
    except Exception as e:
        _log.exception("save .env failed")
        detail = str(e)
        if "python-multipart" in detail.lower():
            detail = "На сервере не установлен python-multipart (pip install python-multipart)"
        msg = f"error={quote(detail, safe='')}"
    sep = "&" if return_q else ""
    return RedirectResponse(url=f"/scanner?{return_q}{sep}{msg}", status_code=303)


@app.get("/scanner/pairs", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
def pair_ranking_panel(
    approved: str | None = None,
    started: str | None = None,
    busy: str | None = None,
    error: str | None = None,
) -> str:
    """Тест 400 пар + утверждение списка для live-скана."""
    msg = None
    err = error
    if approved == "1":
        data = load_symbol_ranking_filtered()
        n = int(data.get("count") or 0)
        msg = f"Утверждено {n} пар для live-скана"
    elif started == "1":
        msg = "Тест 400 пар запущен — страница обновится автоматически"
    elif busy == "1":
        msg = "Тест уже выполняется"
    return render_pair_ranking_dashboard(
        result=load_symbol_ranking_result(),
        live_filtered=load_symbol_ranking_filtered(),
        msg=msg,
        err=err,
    )


@app.post("/scanner/pairs/run", dependencies=PANEL_AUTH_DEPS)
async def pair_ranking_run(background_tasks: BackgroundTasks) -> RedirectResponse:
    cur = load_symbol_ranking_result()
    if cur.get("status") == "running":
        return RedirectResponse(url="/scanner/pairs?busy=1", status_code=303)
    background_tasks.add_task(run_symbol_ranking_background)
    return RedirectResponse(url="/scanner/pairs?started=1", status_code=303)


@app.post("/scanner/pairs/approve", dependencies=PANEL_AUTH_DEPS)
async def pair_ranking_approve(request: Request) -> RedirectResponse:
    form = await request.form()
    symbols = [s.strip() for s in _form_field_values(form, "symbols") if s.strip()]
    if not symbols:
        return RedirectResponse(
            url="/scanner/pairs?error=" + quote("Выберите хотя бы одну пару"),
            status_code=303,
        )
    try:
        approve_live_symbols(symbols)
    except OSError as e:
        return RedirectResponse(
            url="/scanner/pairs?error=" + quote(str(e)),
            status_code=303,
        )
    return RedirectResponse(url="/scanner/pairs?approved=1", status_code=303)


@app.get("/scanner/pairs/json", dependencies=PANEL_AUTH_DEPS)
def pair_ranking_json() -> dict[str, Any]:
    data = load_symbol_ranking_result()
    return {
        "status": data.get("status", "idle"),
        "kind": "pair_test",
        "progress": data.get("progress") or {},
        "symbols_count": data.get("symbols_count"),
        "error": data.get("error"),
    }


@app.get("/scanner/progress/json", dependencies=PANEL_AUTH_DEPS)
def scan_progress_json() -> dict[str, Any]:
    return load_scan_progress()


@app.get("/scanner/orderbook/json", dependencies=PANEL_AUTH_DEPS)
def orderbook_json() -> dict[str, Any]:
    """Live spot OBI / microprice for top-N live pairs."""
    import os

    return {
        "symbols": list(top_live_symbols()),
        "top_n": int(os.environ.get("ORDERBOOK_TOP_N", "8") or 8),
        "gate_enabled": os.environ.get("ORDERBOOK_GATE_ENABLED", "0").strip().lower() in ("1", "true", "yes"),
        "rows": get_all_orderbook_snapshots(),
    }


@app.post("/scanner/live/run", dependencies=PANEL_AUTH_DEPS)
async def live_scan_run(request: Request, background_tasks: BackgroundTasks) -> RedirectResponse:
    form = await request.form()
    return_q = str(form.get("return_q", "")).strip()
    cur = load_scan_progress()
    if cur.get("status") == "running":
        sep = "&" if return_q else ""
        return RedirectResponse(url=f"/scanner?{return_q}{sep}scan_busy=1", status_code=303)
    top = int(form.get("top") or 20)
    bars = int(form.get("bars") or 1000)
    timeframe = str(form.get("timeframe") or "1h").strip() or "1h"
    stage1_min_score = float(form.get("stage1_min_score") or 18)
    background_tasks.add_task(
        _run_live_scan_background,
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
    )
    sep = "&" if return_q else ""
    return RedirectResponse(url=f"/scanner?{return_q}{sep}scan_started=1", status_code=303)


@app.get("/scanner", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
def scanner_panel(
    request: Request,
    top: int = 20,
    bars: int = 1000,
    timeframe: str = "1h",
    stage1_min_score: float = 18.0,
    max_symbols: int | None = None,
    live: bool = False,
    tab: str = "",
    saved: str | None = None,
    error: str | None = None,
    scan_started: str | None = None,
    scan_busy: str | None = None,
    mobile: str | None = None,
    desktop: str | None = None,
) -> str:
    """Dashboard: тренд-скан 50 пар, лучший сетап (без автоторговли)."""
    _ = tab  # legacy query param
    if _is_phone_request(request, mobile=mobile, desktop=desktop):
        return render_mobile_app()
    saved_msg = None
    if saved == "1":
        saved_msg = "Настройки сохранены в .env"
    elif error:
        saved_msg = f"Ошибка сохранения: {error}"
    elif scan_started == "1":
        saved_msg = "Live-скан запущен — смотрите прогресс ниже"
    elif scan_busy == "1":
        saved_msg = "Скан уже выполняется"

    prog = load_scan_progress()
    st = str(prog.get("status") or "idle")
    scan_watch = st == "running" or (scan_started == "1" and st not in ("done", "error"))

    rep, updated_at, from_cache = _scanner_report(
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
        max_symbols=max_symbols,
        live=live,
    )
    return render_scanner_dashboard(
        report=rep,
        updated_at=updated_at,
        from_cache=from_cache,
        scan_history=load_scan_history(25),
        top=top,
        bars=bars,
        timeframe=timeframe,
        stage1_min_score=stage1_min_score,
        max_symbols=max_symbols,
        live=live,
        saved_msg=saved_msg,
        scan_watch=scan_watch,
    )


@app.get("/paper", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
def paper_dashboard(updated: str | None = None, error: str | None = None) -> str:
    """Отдельное окно: симуляция сделок по сетапам score >= порога."""
    msg = None
    if updated == "1":
        msg = "Цены обновлены"
    elif error:
        msg = f"Ошибка обновления: {error}"
    state = load_paper_state()
    return render_paper_dashboard(state=state, summary=paper_summary(state), msg=msg)


@app.get("/paper/json", dependencies=PANEL_AUTH_DEPS)
def paper_json() -> dict[str, Any]:
    state = load_paper_state()
    return {"summary": paper_summary(state), "trades": state.get("trades") or []}


@app.post("/paper/update", dependencies=PANEL_AUTH_DEPS)
def paper_update() -> RedirectResponse:
    """Принудительно обновить открытые симуляции по текущим ценам."""
    try:
        update_open_trades()
    except Exception as e:
        _log.exception("paper update failed")
        return RedirectResponse(url=f"/paper?error={quote(str(e), safe='')}", status_code=303)
    return RedirectResponse(url="/paper?updated=1", status_code=303)


@app.get("/stocks", response_class=HTMLResponse, dependencies=PANEL_AUTH_DEPS)
def stocks_dashboard(
    scan_started: str | None = None,
    scan_busy: str | None = None,
    error: str | None = None,
) -> str:
    """Лучшие входы по токенизированным акциям Binance (bStocks)."""
    msg = None
    if scan_started == "1":
        msg = "Скан акций запущен — смотрите прогресс ниже"
    elif scan_busy == "1":
        msg = "Скан акций уже выполняется"
    elif error:
        msg = f"Ошибка: {error}"
    prog = load_stocks_progress()
    st = str(prog.get("status") or "idle")
    # Не держим watch на ?scan_started=1 после done — иначе бесконечная перезагрузка
    scan_watch = st == "running" or (scan_started == "1" and st not in ("done", "error"))
    return render_stocks_dashboard(
        cached=load_stocks_scan(),
        scan_watch=scan_watch,
        msg=msg,
    )


@app.get("/stocks/json", dependencies=PANEL_AUTH_DEPS)
def stocks_json() -> dict[str, Any]:
    data = load_stocks_scan() or {}
    return {
        "updated_at": data.get("updated_at"),
        "universe_count": data.get("universe_count"),
        "scan_config": data.get("scan_config") or {},
        "report": data.get("report") or {},
        "universe": data.get("universe") or [],
    }


@app.get("/stocks/progress/json", dependencies=PANEL_AUTH_DEPS)
def stocks_progress_json() -> dict[str, Any]:
    return load_stocks_progress()


@app.post("/stocks/run", dependencies=PANEL_AUTH_DEPS)
async def stocks_run(background_tasks: BackgroundTasks) -> RedirectResponse:
    cur = load_stocks_progress()
    if cur.get("status") == "running":
        return RedirectResponse(url="/stocks?scan_busy=1", status_code=303)
    background_tasks.add_task(run_stocks_scan_background)
    return RedirectResponse(url="/stocks?scan_started=1", status_code=303)

