"""Страница /paper — бумажная симуляция сделок по сетапам score >= порога."""

from __future__ import annotations

from typing import Any

from .scanner_panel import (
    _dashboard_tabs,
    _direction_badge,
    _e,
    _fmt_num,
    _fmt_ts,
    _panel_fonts_link,
    _panel_theme_css,
    _pnl_class,
)


def _paper_css() -> str:
    return """
    tr.row-win td { background: rgba(184, 245, 212, 0.07); }
    tr.row-loss td { background: rgba(255, 158, 184, 0.07); }
    tr.row-win:hover td { background: rgba(184, 245, 212, 0.12); }
    tr.row-loss:hover td { background: rgba(255, 158, 184, 0.12); }
    .reason-badge {
      display: inline-block; padding: 3px 10px; border-radius: 999px;
      font-size: 0.72rem; font-weight: 700; font-family: var(--font-display);
    }
    .reason-tp { background: rgba(184, 245, 212, 0.15); color: var(--long); border: 1px solid rgba(184, 245, 212, 0.45); }
    .reason-stop { background: rgba(255, 158, 184, 0.18); color: var(--short); border: 1px solid rgba(255, 158, 184, 0.45); }
    .reason-time { background: var(--glass); color: var(--muted); border: 1px solid var(--border); }
    .paper-note { color: var(--muted); font-size: 0.82rem; margin: 8px 0 0; }
    """


def _reason_badge(reason: str | None) -> str:
    r = str(reason or "")
    label = {"tp": "TP ✓", "stop": "стоп ✗", "time": "таймаут"}.get(r, r or "—")
    cls = {"tp": "reason-tp", "stop": "reason-stop", "time": "reason-time"}.get(r, "reason-time")
    return f'<span class="reason-badge {cls}">{_e(label)}</span>'


def _r_cell(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "<td>—</td>"
    sign = "+" if f > 0 else ""
    return f'<td class="{_pnl_class(f)}"><strong>{sign}{f:.2f}R</strong></td>'


def _open_table(trades: list[dict[str, Any]]) -> str:
    if not trades:
        return (
            '<p class="paper-note">Нет открытых симуляций — они появятся автоматически, '
            "когда скан найдёт сетап со score выше порога.</p>"
        )
    rows = []
    for t in trades:
        rows.append(
            "<tr>"
            f'<td class="sym">{_e(str(t.get("symbol") or ""))}</td>'
            f"<td>{_direction_badge(str(t.get('side') or ''))}</td>"
            f"<td>{_fmt_num(t.get('score'), 1)}</td>"
            f"<td>{_e(str(t.get('pattern') or t.get('regime') or ''))}</td>"
            f'<td class="mono">{_fmt_num(t.get("entry"))}</td>'
            f'<td class="mono stop">{_fmt_num(t.get("stop"))}</td>'
            f'<td class="mono tp">{_fmt_num(t.get("tp"))}</td>'
            f'<td class="mono">{_fmt_num(t.get("last_price"))}</td>'
            + _r_cell(t.get("unrealized_r"))
            + f"<td>{_fmt_ts(t.get('opened_at'))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Пара</th><th>Сторона</th><th>Score</th><th>Сетап</th><th>Вход</th><th>Стоп</th>"
        "<th>TP</th><th>Цена сейчас</th><th>Тек. R</th><th>Открыта</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _closed_table(trades: list[dict[str, Any]], risk_usdt: float) -> str:
    if not trades:
        return '<p class="paper-note">Закрытых сделок пока нет — подождите, пока сработает TP, стоп или таймаут.</p>'
    rows = []
    for t in trades:
        r = t.get("r_multiple")
        try:
            pnl = float(r) * risk_usdt if r is not None else None
        except (TypeError, ValueError):
            pnl = None
        row_cls = "row-win" if t.get("win") else "row-loss"
        pnl_txt = f"{'+' if (pnl or 0) > 0 else ''}{pnl:.2f} $" if pnl is not None else "—"
        rows.append(
            f'<tr class="{row_cls}">'
            f'<td class="sym">{_e(str(t.get("symbol") or ""))}</td>'
            f"<td>{_direction_badge(str(t.get('side') or ''))}</td>"
            f"<td>{_fmt_num(t.get('score'), 1)}</td>"
            f"<td>{_e(str(t.get('pattern') or t.get('regime') or ''))}</td>"
            f'<td class="mono">{_fmt_num(t.get("entry"))}</td>'
            f'<td class="mono">{_fmt_num(t.get("exit"))}</td>'
            f"<td>{_reason_badge(t.get('exit_reason'))}</td>"
            + _r_cell(r)
            + f'<td class="{_pnl_class(pnl)}">{_e(pnl_txt)}</td>'
            f"<td>{_fmt_ts(t.get('opened_at'))}</td>"
            f"<td>{_fmt_ts(t.get('closed_at'))}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Пара</th><th>Сторона</th><th>Score</th><th>Сетап</th><th>Вход</th><th>Выход</th>"
        "<th>Итог</th><th>R</th><th>PnL</th><th>Открыта</th><th>Закрыта</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def render_paper_dashboard(
    *,
    state: dict[str, Any],
    summary: dict[str, Any],
    msg: str | None = None,
) -> str:
    trades = state.get("trades") or []
    open_t = [t for t in trades if t.get("status") == "open"]
    closed = [t for t in trades if t.get("status") == "closed"]
    closed.sort(key=lambda t: str(t.get("closed_at") or ""), reverse=True)
    open_t.sort(key=lambda t: str(t.get("opened_at") or ""), reverse=True)

    total_r = summary.get("total_r")
    pnl = summary.get("pnl_usdt")
    pf = summary.get("profit_factor")
    msg_html = f'<div class="save-banner warn">{_e(msg)}</div>' if msg else ""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="120" />
  <title>Forecast — Симуляция</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=True)}{_paper_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Симуляция сделок</h1>
        <p class="subtitle">
          Сетапы со score ≥ {_fmt_num(summary.get('min_score'), 0)} открываются автоматически после каждого скана ·
          риск на сделку {_fmt_num(summary.get('risk_usdt'), 2)} $ ·
          обновлено {_fmt_ts(summary.get('updated_at'))}
        </p>
      </div>
    </header>

    {_dashboard_tabs(active="paper", base_q="")}
    {msg_html}

    <div class="actions">
      <form method="post" action="/paper/update" style="display:inline">
        <button type="submit" class="btn btn-primary">Обновить цены сейчас</button>
      </form>
    </div>

    <div class="stats">
      <div class="stat"><label>Открыто</label><strong>{summary.get('open')}</strong></div>
      <div class="stat"><label>Закрыто</label><strong>{summary.get('closed')}</strong></div>
      <div class="stat"><label>Успешных</label><strong class="pnl-pos">{summary.get('wins')}</strong></div>
      <div class="stat"><label>Неуспешных</label><strong class="pnl-neg">{summary.get('losses')}</strong></div>
      <div class="stat"><label>Win rate</label><strong>{_fmt_num(summary.get('win_rate_pct'), 1)}%</strong></div>
      <div class="stat"><label>Total R</label><strong class="{_pnl_class(total_r)}">{_fmt_num(total_r, 2)}</strong></div>
      <div class="stat"><label>Profit factor</label><strong>{_fmt_num(pf, 2) if pf is not None else '—'}</strong></div>
      <div class="stat"><label>PnL</label><strong class="{_pnl_class(pnl)}">{_fmt_num(pnl, 2)} $</strong></div>
    </div>

    <section>
      <h2>Открытые симуляции ({len(open_t)})</h2>
      {_open_table(open_t)}
    </section>

    <section>
      <h2>Закрытые сделки ({len(closed)})</h2>
      {_closed_table(closed, float(summary.get('risk_usdt') or 5.0))}
    </section>

    <footer>Симуляция без реальных денег · TP / стоп / таймаут проверяются по закрытым барам Binance</footer>
  </div>
</body>
</html>"""
