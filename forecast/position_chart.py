"""Candlestick chart for open positions (panel iframe)."""

from __future__ import annotations

import html as html_mod
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo

from .binance_client import BinanceConfig, fetch_ohlcv

MSK = ZoneInfo("Europe/Moscow")


def _error_html(message: str) -> str:
    msg = html_mod.escape(message)
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8" />
<style>
  body {{ margin: 0; background: #0b1020; color: #8b96b8; font: 13px system-ui, sans-serif;
         display: flex; align-items: center; justify-content: center; min-height: 260px; padding: 16px; }}
</style></head><body><p>{msg}</p></body></html>"""


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
        if x > 0 and x == x:
            return x
    except (TypeError, ValueError):
        pass
    return None


def build_position_chart_html(
    *,
    symbol: str,
    timeframe: str = "1h",
    entry: float | None = None,
    stop: float | None = None,
    take_profit: float | None = None,
    side: str = "long",
    bars: int = 120,
) -> str:
    symbol = symbol.strip()
    if not symbol:
        return _error_html("Символ не задан")

    limit = max(40, min(int(bars), 500))
    try:
        ohlcv = fetch_ohlcv(BinanceConfig(symbol=symbol, timeframe=timeframe, limit=limit))
    except Exception as e:
        return _error_html(f"Не удалось загрузить свечи: {e}")

    if not ohlcv:
        return _error_html("Пустой ответ биржи")

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = (
        pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(MSK)
    )

    fig = go.Figure(
        data=[
            go.Candlestick(
                x=df["datetime"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                increasing_line_color="#22c55e",
                decreasing_line_color="#ef4444",
                name=symbol,
            )
        ]
    )

    if entry is not None:
        fig.add_hline(
            y=entry,
            line_color="#5b8cff",
            line_width=1.5,
            annotation_text="Вход",
            annotation_position="top left",
            annotation_font_color="#5b8cff",
        )
    if stop is not None:
        fig.add_hline(
            y=stop,
            line_color="#ef4444",
            line_width=1.5,
            line_dash="dash",
            annotation_text="Стоп",
            annotation_position="bottom left",
            annotation_font_color="#ef4444",
        )
    if take_profit is not None:
        fig.add_hline(
            y=take_profit,
            line_color="#22d3a8",
            line_width=1.5,
            line_dash="dot",
            annotation_text="Тейк",
            annotation_position="top right",
            annotation_font_color="#22d3a8",
        )

    side_l = side.strip().lower()
    side_label = "Long" if side_l in ("long", "buy") else "Short" if side_l in ("short", "sell") else side_l

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b1020",
        plot_bgcolor="#0f1628",
        margin=dict(l=4, r=4, t=32, b=4),
        height=268,
        title=dict(text=f"{symbol} · {timeframe} · {side_label}", font=dict(size=12), x=0.01),
        xaxis=dict(rangeslider=dict(visible=False), showgrid=True, gridcolor="#24304d"),
        yaxis=dict(showgrid=True, gridcolor="#24304d", side="right"),
        showlegend=False,
    )
    fig.update_xaxes(tickformat="%d.%m %H:%M")

    return fig.to_html(
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": False, "scrollZoom": True, "responsive": True},
    )
