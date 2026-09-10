"""Страница /swing — лучшие входы на среднесрок (неделя–месяц)."""

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
    _setup_rows,
)


def _swing_css() -> str:
    return """
    .univ-wrap { overflow-x: auto; margin-top: 8px; }
    .univ-chip {
      display: inline-flex; flex-direction: column; gap: 2px;
      background: var(--glass); border: 1px solid var(--border);
      border-radius: 14px; padding: 8px 12px; margin: 4px 4px 0 0;
      min-width: 110px;
    }
    .univ-chip.core { border-color: rgba(255, 158, 207, 0.55); }
    .univ-chip strong { font-size: 0.85rem; font-family: var(--font-display); }
    .univ-chip span { font-size: 0.7rem; color: var(--muted); }
    .toolkit {
      display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 8px;
    }
    .toolkit .chip {
      display: inline-block; background: var(--glass); border: 1px solid var(--border);
      padding: 4px 12px; border-radius: 999px; font-size: 0.78rem;
    }
    """


def _fmt_vol(vol: Any) -> str:
    try:
        v = float(vol or 0)
    except (TypeError, ValueError):
        return "—"
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def _universe_chips(universe: list[dict[str, Any]], limit: int = 24) -> str:
    if not universe:
        return '<p class="muted">Список пар появится после скана.</p>'
    chips = []
    for r in universe[:limit]:
        chg = r.get("change_pct")
        chg_s = f"{float(chg):+.1f}%" if chg is not None else "—"
        core_cls = " core" if r.get("core") else ""
        chips.append(
            f'<div class="univ-chip{core_cls}"><strong>{_e(r.get("base") or r.get("symbol"))}</strong>'
            f'<span>vol {_fmt_vol(r.get("quote_volume"))} · {chg_s}</span></div>'
        )
    more = len(universe) - limit
    extra = f'<span class="muted"> и ещё {more}</span>' if more > 0 else ""
    return '<div class="univ-wrap">' + "".join(chips) + extra + "</div>"


def render_swing_dashboard(
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
    tf = cfg.get("timeframe") or report.get("timeframe") or "1d"
    htf = cfg.get("htf_timeframe")
    htf_s = str(htf) if htf else "—"
    range_on = bool(cfg.get("allow_range", True))
    toolkit = (
        f'<span class="chip">вход {_e(tf)}</span>'
        + (f'<span class="chip">HTF {_e(htf_s)}</span>' if htf else '<span class="chip">дневной график</span>')
        + '<span class="chip">цель 2R</span>'
        + f'<span class="chip">{"тренд + range" if range_on else "только тренд"}</span>'
        + '<span class="chip">BTC-regime</span>'
        + '<span class="chip">ликвидные majors</span>'
    )

    poll = (
        _progress_poll_script(
            json_url="/swing/progress/json",
            reload_on_done=True,
            reload_url="/swing",
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
  <title>Forecast — Среднесрок</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=True)}{_progress_panel_css()}{_swing_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Среднесрок</h1>
        <p class="subtitle">
          Лучшие входы на удержание 1–4 недели · ликвидные majors ·
          обновлено {_fmt_ts(updated)}
        </p>
      </div>
    </header>

    {_dashboard_tabs(active="swing", base_q="")}
    {msg_html}
    {_progress_bar_block(visible=scan_watch)}

    <div class="actions">
      <form method="post" action="/swing/run" style="display:inline">
        <button type="submit" class="btn btn-primary">Сканировать среднесрок</button>
      </form>
      <a class="btn" href="/swing">Обновить</a>
    </div>

    <div class="toolkit">
      {toolkit}
    </div>

    <div class="stats">
      <div class="stat"><label>В универсуме</label><strong>{int((cached or {}).get('universe_count') or len(universe))}</strong></div>
      <div class="stat"><label>Кандидаты</label><strong>{int(report.get('candidates_found') or 0)}</strong></div>
      <div class="stat"><label>Топ сетапов</label><strong>{len(setups)}</strong></div>
      <div class="stat"><label>Таймфрейм</label><strong>{_e(tf)}</strong></div>
      <div class="stat"><label>HTF</label><strong>{_e(htf_s)}</strong></div>
      <div class="stat"><label>Score ≥</label><strong>{_fmt_num(cfg.get('stage1_min_score'), 0)}</strong></div>
      <div class="stat"><label>Время скана</label><strong>{_fmt_scan_duration(report.get('scan_duration_sec'))}</strong></div>
    </div>

    <section>
      <h2>Лучший среднесрочный вход</h2>
      {_hero_setup(hero)}
    </section>

    <section>
      <h2>Топ входы на неделю–месяц</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Пара</th><th>Паттерн</th><th>Тренд</th><th>Score</th>
              <th>Направление</th><th>Prob</th><th>Entry</th><th>Stop</th>
              <th>TP1</th><th>TP2</th><th>R:R</th><th>Почему</th>
            </tr>
          </thead>
          <tbody>
            {_setup_rows(setups)}
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Универсум (ликвидные majors)</h2>
      {_universe_chips(universe)}
    </section>

    <footer>
      Среднесрок: дневной тренд/range у S/R, TP 2R, ликвидные majors ·
      горизонт удержания 1–4 недели · не финансовый совет
    </footer>
  </div>
  {poll}
</body>
</html>"""
