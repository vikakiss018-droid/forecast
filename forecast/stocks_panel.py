"""Страница /stocks — лучшие входы по токенизированным акциям Binance (bStocks)."""

from __future__ import annotations

from typing import Any

from .scanner_panel import (
    _dashboard_tabs,
    _direction_badge,
    _e,
    _fmt_num,
    _fmt_scan_duration,
    _fmt_ts,
    _hero_setup,
    _panel_fonts_link,
    _panel_theme_css,
    _progress_bar_block,
    _progress_panel_css,
    _progress_poll_script,
)
from .stocks_scanner import stock_display_name


def _stocks_css() -> str:
    return """
    .univ-wrap { overflow-x: auto; margin-top: 8px; }
    .univ-chip {
      display: inline-flex; flex-direction: column; gap: 2px;
      background: var(--glass); border: 1px solid var(--border);
      border-radius: 14px; padding: 8px 12px; margin: 4px 4px 0 0;
      min-width: 110px;
    }
    .univ-chip strong { font-size: 0.85rem; font-family: var(--font-display); }
    .univ-chip span { font-size: 0.7rem; color: var(--muted); }
    td.name { color: var(--muted); font-size: 0.82rem; max-width: 160px; }
    """


def _setup_rows_stocks(setups: list[dict[str, Any]]) -> str:
    if not setups:
        return '<tr><td colspan="14" class="empty-cell">Сетапы не найдены — запустите скан</td></tr>'
    rows = []
    for i, s in enumerate(setups, start=1):
        plan = s.get("setup") or {}
        rank_cls = "rank-1" if i == 1 else ""
        name = s.get("stock_name") or stock_display_name(str(s.get("symbol") or ""))
        rows.append(
            f"""
        <tr class="{rank_cls}">
          <td>{i}</td>
          <td class="sym">{_e(s.get('symbol'))}</td>
          <td class="name">{_e(name)}</td>
          <td>{_e(s.get('pattern'))}</td>
          <td><span class="trend-{_e(s.get('trend'))}">{_e(s.get('trend'))}</span></td>
          <td><strong>{_fmt_num(s.get('score'), 1)}</strong></td>
          <td>{_direction_badge(str(plan.get('direction', '')))}</td>
          <td>{_fmt_num(plan.get('probability_pct'), 1)}%</td>
          <td class="mono">{_fmt_num(plan.get('entry'))}</td>
          <td class="mono stop">{_fmt_num(plan.get('stop'))}</td>
          <td class="mono tp">{_fmt_num(plan.get('target_1'))}</td>
          <td class="mono tp">{_fmt_num(plan.get('target_2'))}</td>
          <td>{_fmt_num(plan.get('risk_reward'), 2)}</td>
          <td class="why">{_e(s.get('why_selected'))}</td>
        </tr>"""
        )
    return "\n".join(rows)


def _universe_chips(universe: list[dict[str, Any]], limit: int = 24) -> str:
    if not universe:
        return '<p class="muted">Список акций появится после скана.</p>'
    chips = []
    for r in universe[:limit]:
        vol = float(r.get("quote_volume") or 0)
        if vol >= 1_000_000:
            vol_s = f"{vol/1_000_000:.1f}M"
        elif vol >= 1_000:
            vol_s = f"{vol/1_000:.0f}K"
        else:
            vol_s = f"{vol:.0f}"
        chg = r.get("change_pct")
        chg_s = f"{float(chg):+.1f}%" if chg is not None else "—"
        chips.append(
            f'<div class="univ-chip"><strong>{_e(r.get("ticker") or r.get("base"))}</strong>'
            f'<span>{_e(r.get("name"))}</span>'
            f'<span>vol {vol_s} · {chg_s}</span></div>'
        )
    more = len(universe) - limit
    extra = f'<span class="muted"> и ещё {more}</span>' if more > 0 else ""
    return '<div class="univ-wrap">' + "".join(chips) + extra + "</div>"


def render_stocks_dashboard(
    *,
    cached: dict[str, Any] | None,
    scan_watch: bool = False,
    msg: str | None = None,
) -> str:
    report = (cached or {}).get("report") or {}
    cfg = (cached or {}).get("scan_config") or {}
    universe = (cached or {}).get("universe") or []
    setups = list(report.get("top_setups") or [])
    hero = setups[0] if setups else None
    updated = (cached or {}).get("updated_at")
    msg_html = f'<div class="save-banner warn">{_e(msg)}</div>' if msg else ""

    poll = (
        _progress_poll_script(
            json_url="/stocks/progress/json",
            reload_on_done=True,
        )
        if scan_watch
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="180" />
  <title>Forecast — Акции</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=True)}{_progress_panel_css()}{_stocks_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Акции Binance</h1>
        <p class="subtitle">
          Лучшие входы по токенизированным акциям (bStocks: TSLAB, NVDAB, QQQB…) ·
          те же правила тренд/флет, без фильтра BTC ·
          обновлено {_fmt_ts(updated)}
        </p>
      </div>
    </header>

    {_dashboard_tabs(active="stocks", base_q="")}
    {msg_html}
    {_progress_bar_block(visible=scan_watch)}

    <div class="actions">
      <form method="post" action="/stocks/run" style="display:inline">
        <button type="submit" class="btn btn-primary">Сканировать акции</button>
      </form>
      <a class="btn" href="/stocks">Обновить</a>
    </div>

    <div class="stats">
      <div class="stat"><label>В универсуме</label><strong>{int((cached or {}).get('universe_count') or len(universe))}</strong></div>
      <div class="stat"><label>Кандидаты</label><strong>{int(report.get('candidates_found') or 0)}</strong></div>
      <div class="stat"><label>Топ сетапов</label><strong>{len(setups)}</strong></div>
      <div class="stat"><label>Таймфрейм</label><strong>{_e(cfg.get('timeframe') or report.get('timeframe') or '15m')}</strong></div>
      <div class="stat"><label>Score ≥</label><strong>{_fmt_num(cfg.get('stage1_min_score'), 0)}</strong></div>
      <div class="stat"><label>Время скана</label><strong>{_fmt_scan_duration(report.get('scan_duration_sec'))}</strong></div>
    </div>

    <section>
      <h2>Лучший вход</h2>
      {_hero_setup(hero)}
    </section>

    <section>
      <h2>Топ входы по акциям</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Пара</th><th>Компания</th><th>Паттерн</th><th>Тренд</th><th>Score</th>
              <th>Направление</th><th>Prob</th><th>Entry</th><th>Stop</th>
              <th>TP1</th><th>TP2</th><th>R:R</th><th>Почему</th>
            </tr>
          </thead>
          <tbody>
            {_setup_rows_stocks(setups)}
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Универсум (топ по объёму 24h)</h2>
      {_universe_chips(universe)}
    </section>

    <footer>
      bStocks — токены акций 1:1 на Binance spot · не финансовый совет ·
      инструменты: тренд/range, ATR-стоп, R:R, относительный объём (без BTC-regime)
    </footer>
  </div>
  {poll}
</body>
</html>"""
