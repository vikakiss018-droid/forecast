"""HTML dashboard for scanner (best setup)."""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

from .env_config import get_settings_for_panel
from .run_symbol_ranking import load_filtered_symbols, ranking_config_from_env


def _e(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        dt = dt.astimezone(MSK)
        return dt.strftime("%Y-%m-%d %H:%M MSK")
    except ValueError:
        return _e(iso)


def _fmt_num(v: Any, digits: int = 4) -> str:
    try:
        x = float(v)
        if x != x:
            return "—"
        if abs(x) >= 1000:
            return f"{x:,.2f}"
        if abs(x) >= 1:
            return f"{x:.{digits}f}"
        return f"{x:.6g}"
    except (TypeError, ValueError):
        return "—"


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{_e(text)}</span>'


def _direction_badge(direction: str) -> str:
    d = (direction or "").lower()
    if d == "long":
        return _badge("LONG", "long")
    if d == "short":
        return _badge("SHORT", "short")
    return _badge(direction or "—", "muted")


def _panel_fonts_link() -> str:
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800'
        '&family=Quicksand:wght@500;600;700&display=swap" rel="stylesheet">'
    )


def _panel_theme_css(*, full: bool = False) -> str:
    """Сисси / аниме / feminine — общие стили панели."""
    extra = ""
    if full:
        extra = """
    .balance-section h2 .hint { font-size: 0.72rem; color: var(--muted); font-weight: 400; }
    .balance-error { color: #ffb4d0; margin: 0; }
    .balance-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px;
    }
    .balance-card {
      background: var(--glass); border: 1px solid var(--border); border-radius: 18px; padding: 16px;
      box-shadow: var(--shadow-soft);
    }
    .balance-card label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; }
    .balance-card strong { font-size: 1.35rem; display: block; margin-top: 6px; }
    .balance-card small { font-size: 0.75rem; color: var(--muted); font-weight: 400; }
    .balance-total {
      background: linear-gradient(145deg, rgba(255, 158, 207, 0.22) 0%, rgba(167, 139, 250, 0.12) 100%);
      border-color: rgba(255, 182, 220, 0.45);
    }
    .balance-total strong { font-size: 1.6rem; color: var(--accent2); }
    .chip {
      display: inline-block; background: var(--glass); border: 1px solid var(--border);
      padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; margin: 2px 4px 2px 0;
    }
    .skip-reasons { margin: 12px 0 0; font-size: 0.82rem; }
    .hero {
      background: linear-gradient(135deg, rgba(255, 182, 220, 0.18) 0%, rgba(196, 181, 253, 0.14) 55%, rgba(255, 240, 248, 0.06) 100%);
      border: 1px solid rgba(255, 182, 220, 0.35); border-radius: 20px; padding: 18px 20px;
      box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }
    .hero.empty { color: var(--muted); text-align: center; padding: 32px; }
    .hero-top { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 14px; }
    .hero-symbol {
      font-size: 1.65rem; font-weight: 800; font-family: var(--font-display);
      background: linear-gradient(90deg, #fff0f8, #ffc4e8, #e9d5ff);
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .hero-score { margin-left: auto; color: var(--accent2); font-weight: 700; }
    .hero-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px;
    }
    .hero-grid label { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }
    .hero-why { margin: 14px 0 0; color: var(--muted); font-size: 0.88rem; }
    .hero-note { margin: 8px 0 0; font-size: 0.82rem; color: var(--accent); }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
    .cfg-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px;
    }
    .cfg-card {
      background: var(--glass); border-radius: 14px; padding: 10px 12px; border: 1px solid var(--border);
    }
    .cfg-card span { display: block; font-size: 0.68rem; color: var(--muted); text-transform: uppercase; }
    .cfg-card strong { font-size: 0.9rem; margin-top: 4px; display: block; }
    .pos-card { border-radius: 18px; padding: 14px; border: 1px solid var(--border); }
    .pos-open {
      background: linear-gradient(145deg, rgba(255, 182, 220, 0.12) 0%, rgba(167, 139, 250, 0.08) 100%);
      border-color: rgba(255, 158, 207, 0.45);
      box-shadow: 0 0 28px rgba(255, 158, 207, 0.12);
    }
    .pos-empty { background: var(--glass); color: var(--muted); }
    .pos-card h3 { margin: 0 0 10px; font-size: 0.95rem; font-family: var(--font-display); }
    .pos-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px;
    }
    .pos-grid label { font-size: 0.68rem; color: var(--muted); display: block; }
    .pos-stack { display: flex; flex-direction: column; gap: 12px; }
    .pos-summary { margin: 0 0 4px; font-size: 0.85rem; color: var(--muted); }
    .pos-head {
      display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 10px;
    }
    .pos-head h3 { margin: 0; flex: 1; min-width: 120px; }
    .pos-close-form { margin: 0; }
    .settings-section .settings-hint { color: var(--muted); font-size: 0.85rem; margin: 0 0 14px; }
    .save-banner { padding: 10px 14px; border-radius: 14px; margin-bottom: 14px; font-size: 0.9rem; }
    .save-banner.ok {
      background: rgba(255, 182, 220, 0.15); border: 1px solid rgba(255, 158, 207, 0.4); color: #ffd6ec;
    }
    .settings-form .form-group-title { margin: 16px 0 10px; font-size: 0.9rem; color: var(--muted); }
    .field-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;
    }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field span { font-size: 0.75rem; color: var(--muted); }
    .field input[type="text"] {
      background: rgba(20, 10, 28, 0.55); border: 1px solid var(--border); border-radius: 12px;
      color: var(--text); padding: 8px 10px; font-size: 0.88rem;
    }
    .bool-field { flex-direction: row; align-items: center; gap: 8px; }
    .bool-field input { width: auto; accent-color: var(--accent); }
    .btn-save { margin-top: 16px; cursor: pointer; border: none; }
    .btn-close {
      background: rgba(255, 120, 160, 0.18); border-color: rgba(255, 158, 207, 0.5); color: #ffd0e4;
      cursor: pointer; font-size: 0.8rem; padding: 6px 12px; border-radius: 999px;
    }
    .btn-close:hover { background: rgba(255, 120, 160, 0.32); box-shadow: 0 0 16px rgba(255, 120, 160, 0.25); }
"""
    return f"""
    :root {{
      --bg: #140a18;
      --bg2: #1f0f28;
      --surface: rgba(42, 22, 52, 0.72);
      --surface2: rgba(58, 30, 72, 0.55);
      --glass: rgba(255, 240, 248, 0.05);
      --border: rgba(255, 182, 220, 0.28);
      --text: #fff0f8;
      --muted: #c9a8d4;
      --accent: #ff9ecf;
      --accent2: #e9b8ff;
      --long: #b8f5d4;
      --short: #ff9eb8;
      --warn: #ffd89a;
      --shadow-soft: 0 8px 32px rgba(255, 120, 180, 0.12);
      --font-body: "Nunito", "Segoe UI", system-ui, sans-serif;
      --font-display: "Quicksand", "Nunito", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font-body);
      background:
        radial-gradient(ellipse 900px 520px at 8% -5%, rgba(255, 158, 207, 0.28) 0%, transparent 55%),
        radial-gradient(ellipse 700px 480px at 95% 0%, rgba(167, 139, 250, 0.22) 0%, transparent 50%),
        radial-gradient(ellipse 600px 400px at 50% 100%, rgba(255, 182, 220, 0.1) 0%, transparent 45%),
        linear-gradient(165deg, var(--bg2) 0%, var(--bg) 45%, #0f0812 100%);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
    }}
    body::before {{
      content: "";
      position: fixed; inset: 0; pointer-events: none; z-index: 0;
      background-image:
        radial-gradient(circle at 20% 30%, rgba(255, 255, 255, 0.04) 0 1px, transparent 2px),
        radial-gradient(circle at 70% 60%, rgba(255, 182, 220, 0.06) 0 1px, transparent 2px);
      background-size: 48px 48px, 64px 64px;
      opacity: 0.5;
    }}
    .wrap {{ position: relative; z-index: 1; max-width: 1440px; margin: 0 auto; padding: 20px 16px 48px; }}
    header {{
      display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
      gap: 16px; margin-bottom: 24px;
      padding: 18px 20px;
      border-radius: 22px;
      border: 1px solid rgba(255, 182, 220, 0.25);
      background: linear-gradient(135deg, rgba(255, 182, 220, 0.1) 0%, rgba(167, 139, 250, 0.08) 100%);
      box-shadow: var(--shadow-soft);
    }}
    h1 {{
      margin: 0;
      font-family: var(--font-display);
      font-size: 1.85rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      background: linear-gradient(92deg, #fff8fc, #ffc8e8 40%, #e9d5ff 85%);
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }}
    h1::after {{ content: " ✦"; font-size: 0.85em; opacity: 0.85; -webkit-text-fill-color: #ffc8e8; }}
    .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-top: 6px; }}
    .pills {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .badge {{
      display: inline-block; padding: 4px 12px; border-radius: 999px;
      font-size: 0.72rem; font-weight: 700; letter-spacing: 0.05em;
      font-family: var(--font-display);
    }}
    .badge-long {{
      background: rgba(184, 245, 212, 0.15); color: var(--long);
      border: 1px solid rgba(184, 245, 212, 0.45);
      box-shadow: 0 0 12px rgba(184, 245, 212, 0.15);
    }}
    .badge-short {{
      background: rgba(255, 158, 184, 0.18); color: var(--short);
      border: 1px solid rgba(255, 158, 184, 0.45);
    }}
    .badge-ok {{
      background: rgba(233, 184, 255, 0.18); color: var(--accent2);
      border: 1px solid rgba(233, 184, 255, 0.4);
    }}
    .badge-warn {{
      background: rgba(255, 216, 154, 0.15); color: var(--warn);
      border: 1px solid rgba(255, 216, 154, 0.4);
    }}
    .badge-bad {{
      background: rgba(255, 120, 160, 0.2); color: #ffd0e4;
      border: 1px solid rgba(255, 158, 184, 0.5);
    }}
    .badge-muted {{ background: var(--glass); color: var(--muted); border: 1px solid var(--border); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }}
    .btn {{
      border: 1px solid var(--border); background: var(--glass); color: var(--text);
      padding: 8px 16px; border-radius: 999px; text-decoration: none; font-size: 0.85rem;
      font-weight: 600; transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
    }}
    .btn:hover {{
      transform: translateY(-1px);
      border-color: var(--accent);
      box-shadow: 0 4px 20px rgba(255, 158, 207, 0.2);
    }}
    .btn-primary {{
      background: linear-gradient(135deg, #ff9ecf 0%, #c4b5fd 50%, #ffbce8 100%);
      border-color: transparent; color: #2a1020;
      box-shadow: 0 4px 24px rgba(255, 158, 207, 0.35);
    }}
    .stats {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 12px; margin-bottom: 20px;
    }}
    .stat {{
      background: var(--glass); border: 1px solid var(--border); border-radius: 18px;
      padding: 14px 16px; backdrop-filter: blur(8px);
      box-shadow: var(--shadow-soft);
    }}
    .stat label {{
      display: block; font-size: 0.68rem; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
    }}
    .stat strong {{ font-size: 1.2rem; display: block; margin-top: 4px; font-family: var(--font-display); }}
    .pnl-pos {{ color: var(--long) !important; }}
    .pnl-neg {{ color: var(--short) !important; }}
    .muted {{ color: var(--muted); }}
    section {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
      padding: 18px 20px; margin-bottom: 20px;
      backdrop-filter: blur(10px);
      box-shadow: var(--shadow-soft);
    }}
    section h2 {{
      margin: 0 0 14px; font-size: 1.05rem; font-weight: 700;
      font-family: var(--font-display);
      display: flex; align-items: center; gap: 8px; color: #ffe4f4;
    }}
    section h2::before {{
      content: "♡"; font-size: 0.85em; color: var(--accent); line-height: 1;
    }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }}
    .stop {{ color: var(--short); }}
    .tp {{ color: var(--long); }}
    .table-wrap {{
      overflow-x: auto; border-radius: 14px; border: 1px solid var(--border);
      background: rgba(15, 8, 20, 0.35);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{
      background: rgba(58, 30, 72, 0.65); color: var(--muted); font-weight: 700;
      font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em;
      position: sticky; top: 0;
    }}
    tr:hover td {{ background: rgba(255, 158, 207, 0.06); }}
    tr.rank-1 td {{ background: rgba(255, 182, 220, 0.1); }}
    td.sym {{ font-weight: 700; color: var(--accent); font-family: var(--font-display); }}
    td.why {{ max-width: 220px; color: var(--muted); }}
    td.reason {{ color: var(--muted); max-width: 200px; }}
    .trend-up {{ color: var(--long); }}
    .trend-down {{ color: var(--short); }}
    .trend-range {{ color: var(--warn); }}
    .empty-cell {{ text-align: center; color: var(--muted); padding: 24px !important; }}
    .dash-tabs {{ display: flex; gap: 8px; margin: 0 0 20px; flex-wrap: wrap; }}
    .tab {{
      padding: 10px 18px; border-radius: 999px; border: 1px solid var(--border);
      background: var(--glass); color: var(--text); text-decoration: none; font-size: 0.88rem;
      font-weight: 600; transition: all 0.15s;
    }}
    .tab:hover {{ border-color: var(--accent); color: #fff8fc; }}
    .tab.active {{
      background: linear-gradient(135deg, #ff9ecf, #c4b5fd);
      border-color: transparent; color: #2a1020;
      box-shadow: 0 4px 20px rgba(255, 158, 207, 0.3);
    }}
    footer {{
      text-align: center; color: var(--muted); font-size: 0.78rem; margin-top: 24px;
    }}
    footer a {{ color: var(--accent); text-decoration: none; }}
    footer a:hover {{ text-decoration: underline; color: var(--accent2); }}
    .save-banner.warn {{
      background: rgba(255, 216, 154, 0.12); border: 1px solid rgba(255, 216, 154, 0.35); color: var(--warn);
    }}
    .save-banner.err {{
      background: rgba(255, 120, 160, 0.15); border: 1px solid rgba(255, 158, 184, 0.4); color: #ffd0e4;
    }}
    {extra}"""


def _hero_setup(setup_row: dict[str, Any] | None) -> str:
    if not setup_row:
        return '<div class="hero empty">Нет сетапов в последнем скане</div>'
    plan = setup_row.get("setup") or {}
    sym = _e(setup_row.get("symbol"))
    direction = str(plan.get("direction", ""))
    return f"""
    <div class="hero">
      <div class="hero-top">
        <div class="hero-symbol">{sym}</div>
        {_direction_badge(direction)}
        <span class="hero-score">Score {_fmt_num(setup_row.get('score'), 1)}</span>
      </div>
      <div class="hero-grid">
        <div><label>Паттерн</label><div>{_e(setup_row.get('pattern'))}</div></div>
        <div><label>Тренд</label><div>{_e(setup_row.get('trend'))}</div></div>
        <div><label>Вероятность</label><div>{_fmt_num(plan.get('probability_pct'), 1)}%</div></div>
        <div><label>R:R</label><div>{_fmt_num(plan.get('risk_reward'), 2)}</div></div>
        <div><label>Вход</label><div class="mono">{_fmt_num(plan.get('entry'))}</div></div>
        <div><label>Стоп</label><div class="mono stop">{_fmt_num(plan.get('stop'))}</div></div>
        <div><label>TP1</label><div class="mono tp">{_fmt_num(plan.get('target_1'))}</div></div>
        <div><label>TP2</label><div class="mono tp">{_fmt_num(plan.get('target_2'))}</div></div>
      </div>
      <p class="hero-why">{_e(setup_row.get('why_selected'))}</p>
      <p class="hero-note">№1 — лучший score. Сетап для ручного входа (автоторговля отключена).</p>
    </div>
    """


def _setup_rows(setups: list[dict[str, Any]]) -> str:
    if not setups:
        return '<tr><td colspan="13" class="empty-cell">Сетапы не найдены</td></tr>'
    rows = []
    for i, s in enumerate(setups, start=1):
        plan = s.get("setup") or {}
        rank_cls = "rank-1" if i == 1 else ""
        rows.append(
            f"""
        <tr class="{rank_cls}">
          <td>{i}</td>
          <td class="sym">{_e(s.get('symbol'))}</td>
          <td>{_e(s.get('pattern'))}</td>
          <td><span class="trend-{_e(s.get('trend'))}">{_e(s.get('trend'))}</span></td>
          <td><strong>{_fmt_num(s.get('score'), 1)}</strong></td>
          <td>{_direction_badge(str(plan.get('direction', '')))}</td>
          <td>{_fmt_num(plan.get('probability_pct'), 1)}%</td>
          <td class="mono">{_fmt_num(plan.get('entry'))}</td>
          <td class="mono">{_fmt_num(plan.get('stop'))}</td>
          <td class="mono">{_fmt_num(plan.get('target_1'))}</td>
          <td class="mono">{_fmt_num(plan.get('target_2'))}</td>
          <td>{_fmt_num(plan.get('risk_reward'), 2)}</td>
          <td class="why">{_e(s.get('why_selected'))}</td>
        </tr>"""
        )
    return "\n".join(rows)


def _scan_history_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<tr><td colspan="6" class="empty-cell">История появится после нескольких сканов (каждые 15 мин)</td></tr>'
    rows = []
    for h in items:
        top = h.get("top") or {}
        sym = top.get("symbol") or "—"
        pairs = h.get("symbols_scanned") or h.get("universe_size")
        rows.append(
            f"""
        <tr>
          <td class="mono">{_fmt_ts(h.get('updated_at'))}</td>
          <td>{_e(sym)}</td>
          <td>{_direction_badge(str(top.get('direction') or ''))}</td>
          <td>{_fmt_num(top.get('score'), 1) if top.get('score') is not None else '—'}</td>
          <td>{int(h.get('candidates_found') or 0)} / {int(pairs or 0)}</td>
          <td>{_fmt_scan_duration(h.get('scan_duration_sec'))}</td>
        </tr>"""
        )
    return "\n".join(rows)


def _fmt_duration(seconds: int) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} сек"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m} мин {sec} сек" if sec else f"{m} мин"
    h, m = divmod(m, 60)
    return f"{h} ч {m} мин"


def _fmt_scan_duration(sec: Any) -> str:
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return "—"
    if s != s or s < 0:
        return "—"
    if s < 60:
        return f"{s:.1f} сек"
    return _fmt_duration(int(round(s)))


def _pnl_class(v: float | None) -> str:
    if v is None:
        return ""
    if v > 0:
        return "pnl-pos"
    if v < 0:
        return "pnl-neg"
    return ""


def _dashboard_tabs(*, active: str, base_q: str) -> str:
    scan_cls = "tab active" if active == "scan" else "tab"
    pairs_cls = "tab active" if active == "pairs" else "tab"
    paper_cls = "tab active" if active == "paper" else "tab"
    stocks_cls = "tab active" if active == "stocks" else "tab"
    return f"""
    <nav class="dash-tabs">
      <a class="{scan_cls}" href="/scanner?{base_q}">Сканер</a>
      <a class="{stocks_cls}" href="/stocks">Акции</a>
      <a class="{pairs_cls}" href="/scanner/pairs">Тест пар</a>
      <a class="{paper_cls}" href="/paper">Симуляция</a>
      <a class="tab" href="/scanner?mobile=1">Телефон</a>
    </nav>"""


def _progress_pct(current: int, total: int) -> int:
    if total <= 0:
        return 0
    return min(100, int(100 * current / total))


def _progress_bar_block(
    *,
    bar_id: str = "scan-progress-bar",
    label_id: str = "scan-progress-label",
    detail_id: str = "scan-progress-detail",
    visible: bool = False,
) -> str:
    vis = "flex" if visible else "none"
    return f"""
    <div id="progress-panel" class="progress-panel" style="display:{vis}">
      <div class="progress-head">
        <strong id="{label_id}">Сканирование…</strong>
        <span id="progress-pct">0%</span>
      </div>
      <div class="progress-track"><div id="{bar_id}" class="progress-fill" style="width:0%"></div></div>
      <div id="{detail_id}" class="progress-detail">—</div>
    </div>"""


def _progress_poll_script(
    *,
    json_url: str,
    reload_on_done: bool = True,
    bar_id: str = "scan-progress-bar",
    label_id: str = "scan-progress-label",
    detail_id: str = "scan-progress-detail",
    panel_id: str = "progress-panel",
    interval_ms: int = 2500,
) -> str:
    reload_js = "window.location.reload();" if reload_on_done else ""
    url_js = json.dumps(json_url)
    return f"""
  <script>
  (function() {{
    const url = {url_js};
    const panel = document.getElementById({json.dumps(panel_id)});
    const bar = document.getElementById({json.dumps(bar_id)});
    const label = document.getElementById({json.dumps(label_id)});
    const detail = document.getElementById({json.dumps(detail_id)});
    const pctEl = document.getElementById('progress-pct');
    if (!panel) return;

    async function tick() {{
      try {{
        const r = await fetch(url, {{ credentials: 'same-origin' }});
        const d = await r.json();
        const st = d.status || 'idle';
        const prog = d.progress || d;
        const cur = parseInt(prog.current || 0, 10);
        const tot = parseInt(prog.total || 0, 10);
        const sym = prog.symbol || '—';
        const pct = tot > 0 ? Math.min(100, Math.round(100 * cur / tot)) : 0;
        if (st === 'running') {{
          panel.style.display = 'flex';
          if (bar) bar.style.width = pct + '%';
          if (pctEl) pctEl.textContent = pct + '%';
          if (label) label.textContent = d.kind === 'pair_test' ? 'Тест пар…' : 'Live-скан…';
          if (detail) detail.textContent = cur + ' / ' + tot + ' · ' + sym;
          return;
        }}
        if (st === 'done') {{
          if (bar) bar.style.width = '100%';
          if (pctEl) pctEl.textContent = '100%';
          if (detail) detail.textContent = 'Готово';
          {reload_js}
          return;
        }}
        if (st === 'error') {{
          panel.style.display = 'flex';
          if (label) label.textContent = 'Ошибка';
          if (detail) detail.textContent = d.error || 'unknown';
          return;
        }}
        panel.style.display = 'none';
      }} catch (e) {{
        if (detail) detail.textContent = 'Ошибка опроса прогресса';
      }}
    }}
    tick();
    setInterval(tick, {int(interval_ms)});
  }})();
  </script>"""


def _progress_panel_css() -> str:
    return """
    .progress-panel {
      display: flex; flex-direction: column; gap: 8px;
      background: linear-gradient(135deg, rgba(255, 182, 220, 0.14), rgba(167, 139, 250, 0.1));
      border: 1px solid rgba(255, 182, 220, 0.35); border-radius: 16px;
      padding: 14px 16px; margin: 0 0 16px;
    }
    .progress-head { display: flex; justify-content: space-between; align-items: center; }
    .progress-track {
      height: 10px; border-radius: 999px; background: rgba(255,255,255,0.08); overflow: hidden;
    }
    .progress-fill {
      height: 100%; border-radius: 999px;
      background: linear-gradient(90deg, #ff9ecf, #c4b5fd);
      transition: width 0.35s ease;
    }
    .progress-detail { font-size: 0.82rem; color: var(--muted); }
    """


def _pair_passes_auto_filter(row: dict[str, Any]) -> bool:
    return (
        float(row.get("total_r") or 0) > 0.5
        and float(row.get("win_rate_pct") or 0) > 50.0
        and int(row.get("trades") or 0) > 0
    )


def _pair_ranking_rows(
    ranking: list[dict[str, Any]],
    *,
    selected_symbols: set[str] | None = None,
) -> str:
    if not ranking:
        return (
            '<tr><td colspan="8" class="empty-cell">'
            "Запустите тест — таблица заполнится после завершения (~5–10 мин)"
            "</td></tr>"
        )
    rows_sorted = sorted(ranking, key=lambda r: -float(r.get("total_r") or 0))
    out: list[str] = []
    for i, row in enumerate(rows_sorted, 1):
        sym = str(row.get("symbol") or "")
        tr = float(row.get("total_r") or 0)
        win = float(row.get("win_rate_pct") or 0)
        trades = int(row.get("trades") or 0)
        auto_ok = _pair_passes_auto_filter(row)
        checked = ""
        if selected_symbols is not None:
            if sym in selected_symbols:
                checked = " checked"
        elif auto_ok:
            checked = " checked"
        r_cls = "pnl-pos" if tr > 0 else ("pnl-neg" if tr < 0 else "")
        tag = _badge("авто", "ok") if auto_ok else ""
        out.append(
            f"""
        <tr class="{'row-plus' if auto_ok else ''}">
          <td><input type="checkbox" name="symbols" value="{_e(sym)}"{checked} /></td>
          <td>{i}</td>
          <td class="sym">{_e(sym)}</td>
          <td class="mono {_pnl_class(tr)}">{tr:+.2f}</td>
          <td>{_fmt_num(row.get('estimated_pnl_usdt'), 2)}</td>
          <td>{trades}</td>
          <td>{win:.1f}%</td>
          <td>{tag}</td>
        </tr>"""
        )
    return "\n".join(out)


def _pair_test_config_block(result: dict[str, Any]) -> str:
    cfg = dict(result.get("test_config") or {})
    if not cfg:
        try:
            cfg = ranking_config_from_env().to_meta()
        except Exception:
            cfg = {}
    if not cfg:
        return ""
    modes = []
    if cfg.get("allow_trend"):
        modes.append("trend")
    if cfg.get("allow_range"):
        modes.append("range")
    mode_s = "+".join(modes) if modes else "—"
    top_n = cfg.get("top_n", "—")
    items = [
        ("Пар", str(top_n)),
        ("ТФ / bars", f"{cfg.get('timeframe', '—')} / {cfg.get('bars', '—')}"),
        ("Режим", mode_s),
        ("Stage1", f"{cfg.get('stage1_min', '—')} (relax {cfg.get('stage1_relax', '—')})"),
        ("Long only", "да" if cfg.get("long_only") else "нет"),
        ("Min score", str(cfg.get("min_score", "—"))),
        ("Min prob %", str(cfg.get("min_probability_pct", "—"))),
        ("Min R:R", str(cfg.get("min_risk_reward", "—"))),
        ("Min ATR %", str(cfg.get("min_atr_pct_auto", "—"))),
        ("Rel volume", str(cfg.get("min_rel_volume", "—"))),
        ("ATR trend %", str(cfg.get("min_atr_pct_trend", "—"))),
        ("Lookback", str(cfg.get("trend_lookback", "—"))),
        ("Сделок/пара", str(cfg.get("target_trades_per_symbol", "—"))),
    ]
    rows = "".join(
        f"<div class='cfg-item'><label>{_e(lbl)}</label><strong>{_e(val)}</strong></div>"
        for lbl, val in items
    )
    rule = cfg.get("rule") or ""
    return f"""
    <div class="cfg-box">
      <strong>Параметры теста (.env + config.yaml)</strong>
      <div class="cfg-grid">{rows}</div>
      <p class="cfg-rule">{_e(rule)}</p>
    </div>"""


def render_pair_ranking_dashboard(
    *,
    result: dict[str, Any],
    live_filtered: dict[str, Any],
    msg: str | None = None,
    err: str | None = None,
) -> str:
    status = str(result.get("status") or "idle")
    ranking = list(result.get("ranking") or [])
    progress = result.get("progress") or {}
    cur = int(progress.get("current") or 0)
    total = int(progress.get("total") or 0)
    cur_sym = progress.get("symbol") or "—"
    finished = _fmt_ts(result.get("finished_at"))
    started = _fmt_ts(result.get("started_at"))
    sym_count = int(result.get("symbols_count") or 0)
    total_trades = int(result.get("total_trades") or 0)
    total_r = result.get("total_r")

    live_count = int(live_filtered.get("count") or 0)
    live_at = _fmt_ts(live_filtered.get("approved_at") or live_filtered.get("created_at"))

    banner = ""
    if err:
        banner = f'<div class="save-banner err">{_e(err)}</div>'
    elif msg:
        banner = f'<div class="save-banner ok">{_e(msg)}</div>'
    elif status == "running":
        pct = int(100 * cur / total) if total else 0
        banner = (
            f'<div class="save-banner warn">Тест выполняется: {cur}/{total} ({pct}%) · '
            f"{_e(cur_sym)} · страница обновится автоматически</div>"
        )
    elif status == "error":
        banner = f'<div class="save-banner err">Ошибка: {_e(result.get("error"))}</div>'

    run_disabled = "disabled" if status == "running" else ""
    poll_script = ""
    if status == "running":
        poll_script = _progress_poll_script(
            json_url="/scanner/pairs/json",
            reload_on_done=True,
            bar_id="pair-progress-bar",
            label_id="pair-progress-label",
            detail_id="pair-progress-detail",
            panel_id="progress-panel",
        )
    plus_count = sum(1 for r in ranking if _pair_passes_auto_filter(r))
    show_form = status == "done" and bool(ranking)
    test_cfg = result.get("test_config") or {}
    try:
        env_cfg = ranking_config_from_env().to_meta()
    except Exception:
        env_cfg = {}
    top_n = int(test_cfg.get("top_n") or env_cfg.get("top_n") or 400)
    config_block = _pair_test_config_block(result)

    approve_block = ""
    if show_form:
        approve_block = f"""
    <form method="post" action="/scanner/pairs/approve" id="approve-form">
      <div class="approve-bar">
        <button type="button" class="btn" onclick="toggleAll(true)">Выбрать все плюсовые</button>
        <button type="button" class="btn" onclick="toggleAll(false)">Снять все</button>
        <button type="submit" class="btn btn-primary">Утвердить для live-скана</button>
        <span class="hint-inline">Выбранные пары заменят текущий список для почасового скана</span>
      </div>
      <div class="table-wrap table-scroll">
        <table>
          <thead>
            <tr>
              <th></th><th>#</th><th>Пара</th><th>Total R</th><th>$ est.</th>
              <th>Сделок</th><th>Win%</th><th>Авто</th>
            </tr>
          </thead>
          <tbody>{_pair_ranking_rows(ranking)}</tbody>
        </table>
      </div>
    </form>"""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Forecast — Тест пар ({top_n})</title>
  {_panel_fonts_link()}
  <style>
    {_panel_theme_css(full=False)}
    {_progress_panel_css()}
    .wrap {{ max-width: 1200px; }}
    .hint {{ color: var(--muted); font-size: 0.85rem; margin: 12px 0 20px; line-height: 1.6; }}
    .btn {{ cursor: pointer; border: none; color: #2a1020; }}
    .btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .approve-bar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 16px 0; }}
    .hint-inline {{ color: var(--muted); font-size: 0.8rem; }}
    .table-scroll {{ max-height: 65vh; overflow: auto; }}
    tr.row-plus td {{ background: rgba(134, 239, 172, 0.06); }}
    .live-box {{
      background: var(--glass); border: 1px solid var(--border); border-radius: 16px;
      padding: 14px 18px; margin-bottom: 16px;
    }}
    .cfg-box {{
      background: var(--glass); border: 1px solid var(--border); border-radius: 16px;
      padding: 14px 18px; margin-bottom: 16px;
    }}
    .cfg-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 10px 16px; margin-top: 12px;
    }}
    .cfg-item label {{ display: block; color: var(--muted); font-size: 0.75rem; }}
    .cfg-item strong {{ font-size: 0.9rem; }}
    .cfg-rule {{ color: var(--muted); font-size: 0.78rem; margin: 12px 0 0; line-height: 1.5; }}
  </style>
  <script>
    function toggleAll(on) {{
      document.querySelectorAll('#approve-form input[name="symbols"]').forEach(cb => {{
        if (on) {{
          const row = cb.closest('tr');
          cb.checked = row && row.classList.contains('row-plus');
        }} else {{
          cb.checked = false;
        }}
      }});
    }}
  </script>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Тест пар ({top_n})</h1>
      <p class="subtitle">Отбор пар для live-скана · параметры из .env</p>
    </header>
    {_dashboard_tabs(active="pairs", base_q="")}
    {banner}
    {_progress_bar_block(
        bar_id="pair-progress-bar",
        label_id="pair-progress-label",
        detail_id="pair-progress-detail",
        visible=(status == "running"),
    )}
    {config_block}
    <div class="live-box">
      <strong>Live сейчас:</strong> {live_count} пар
      <span style="color:var(--muted)"> · обновлено {_e(live_at)}</span>
      <br/><span style="color:var(--muted);font-size:0.82rem">
        Авто-отбор: R &gt; 0.5, win &gt; 50%, ≥1 сделка · после теста отметьте пары и нажмите «Утвердить»
      </span>
    </div>
    <form method="post" action="/scanner/pairs/run" style="margin-bottom: 12px;">
      <button type="submit" class="btn btn-primary" {run_disabled}>Запустить тест {top_n} пар (~5–10 мин)</button>
    </form>
    <p class="hint">
      Тест не меняет live-список автоматически. Параметры берутся из <code>.env</code>
      (<code>FORECAST_*</code>, <code>TREND_*</code>, <code>RANK_*</code>) —
      те же, что у live-скана. По завершении выберите пары и утвердите —
      они попадут в <code>symbol_ranking_filtered_r05_win50.json</code>.
    </p>
    <div class="stats">
      <div class="stat"><label>Статус</label><strong>{_e(status)}</strong></div>
      <div class="stat"><label>Пар в тесте</label><strong>{sym_count or total or '—'}</strong></div>
      <div class="stat"><label>Плюсовых (авто)</label><strong>{plus_count if status == 'done' else '—'}</strong></div>
      <div class="stat"><label>Сделок в тесте</label><strong>{total_trades if status == 'done' else '—'}</strong></div>
      <div class="stat"><label>Total R</label><strong>{_fmt_num(total_r, 2) if total_r is not None else '—'}</strong></div>
      <div class="stat"><label>Завершён</label><strong>{finished}</strong></div>
      <div class="stat"><label>Прогресс</label><strong>{cur}/{total if total else '—'}</strong></div>
    </div>
    {approve_block}
    <footer style="margin-top:24px;color:var(--muted);font-size:0.8rem">
      <a href="/scanner" style="color:var(--accent)">← Сканер</a>
    </footer>
  </div>
  {poll_script}
</body>
</html>"""


def _settings_form_html(*, return_q: str, saved_msg: str | None = None) -> str:
    rows = [r for r in get_settings_for_panel() if r.get("group") != "rank"]
    scan_fields: list[str] = []
    for row in rows:
        key = row["key"]
        label = _e(row["label"])
        val = _e(row.get("value", ""))
        t = row.get("type", "str")
        if t == "bool":
            checked = "checked" if val.lower() in ("true", "1", "yes", "on") else ""
            field = f"""
            <label class="field bool-field">
              <input type="hidden" name="{key}" value="false" />
              <input type="checkbox" name="{key}" value="true" {checked} />
              <span>{label}</span>
            </label>"""
        else:
            field = f"""
            <label class="field">
              <span>{label}</span>
              <input type="text" name="{key}" value="{val}" />
            </label>"""
        scan_fields.append(field)

    banner = ""
    if saved_msg:
        banner = f'<div class="save-banner ok">{_e(saved_msg)}</div>'

    return f"""
    <section class="settings-section">
      <h2>Настройки сканера (.env)</h2>
      <p class="settings-hint">Ключи Binance и пароль панели — только в .env на сервере. После сохранения применяются сразу.</p>
      {banner}
      <form method="post" action="/scanner/settings" class="settings-form">
        <input type="hidden" name="return_q" value="{_e(return_q)}" />
        <div class="field-grid">{"".join(scan_fields)}</div>
        <button type="submit" class="btn btn-primary btn-save">Сохранить в .env</button>
      </form>
    </section>"""



def render_scanner_dashboard(
    *,
    report: dict[str, Any],
    updated_at: str | None,
    from_cache: bool,
    scan_history: list[dict[str, Any]],
    top: int,
    bars: int,
    timeframe: str,
    stage1_min_score: float,
    max_symbols: int | None,
    live: bool,
    saved_msg: str | None = None,
    scan_watch: bool = False,
) -> str:
    setups = report.get("top_setups") or []
    hero = setups[0] if setups else None
    max_symbols_q = "" if max_symbols is None else str(max_symbols)
    base_q = (
        f"top={int(top)}&bars={int(bars)}&timeframe={html.escape(timeframe)}"
        f"&stage1_min_score={float(stage1_min_score)}&max_symbols={html.escape(max_symbols_q)}"
    )
    refresh_url = f"/scanner?{base_q}"
    live_pairs = len(load_filtered_symbols())
    scan_poll = ""
    if scan_watch:
        scan_poll = _progress_poll_script(json_url="/scanner/progress/json", reload_on_done=True)

    status_scan = _badge("Кэш" if from_cache else "Live", "ok" if from_cache else "warn")
    sym_scanned = int(report.get("symbols_scanned") or report.get("universe_size") or 0)
    scan_dur = report.get("scan_duration_sec")
    scan_hint = f"{_e(timeframe)} trend · {live_pairs} пар"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="3600" />
  <title>Forecast — Сканер</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=True)}{_progress_panel_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Forecast Scanner</h1>
        <p class="subtitle">{scan_hint}</p>
      </div>
      <div class="pills">
        {status_scan}
      </div>
    </header>

    {_dashboard_tabs(active="scan", base_q=base_q)}

    {_progress_bar_block(visible=scan_watch)}

    <div class="actions">
      <form method="post" action="/scanner/live/run" style="display:inline">
        <input type="hidden" name="return_q" value="{_e(base_q)}" />
        <input type="hidden" name="top" value="{int(top)}" />
        <input type="hidden" name="bars" value="{int(bars)}" />
        <input type="hidden" name="timeframe" value="{_e(timeframe)}" />
        <input type="hidden" name="stage1_min_score" value="{float(stage1_min_score)}" />
        <button type="submit" class="btn btn-primary">Запустить скан</button>
      </form>
      <a class="btn" href="{refresh_url}">Обновить кэш</a>
    </div>

    <div class="stats">
      <div class="stat"><label>Пары</label><strong>{live_pairs}</strong></div>
      <div class="stat"><label>Последний скан</label><strong>{_fmt_ts(updated_at)}</strong></div>
      <div class="stat"><label>Просканировано</label><strong>{sym_scanned}</strong></div>
      <div class="stat"><label>Время скана</label><strong>{_fmt_scan_duration(scan_dur)}</strong></div>
      <div class="stat"><label>Кандидаты</label><strong>{int(report.get('candidates_found', 0))}</strong></div>
      <div class="stat"><label>Таймфрейм</label><strong>{_e(timeframe)}</strong></div>
    </div>

    {_settings_form_html(return_q=base_q, saved_msg=saved_msg)}

    <section>
      <h2>Лучший сетап</h2>
      {_hero_setup(hero)}
    </section>

    <section>
      <h2>Топ сетапы</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Пара</th><th>Паттерн</th><th>Тренд</th><th>Score</th>
              <th>Направление</th><th>Prob</th><th>Entry</th><th>Stop</th>
              <th>TP1</th><th>TP2</th><th>R:R</th><th>Почему</th>
            </tr>
          </thead>
          <tbody>{_setup_rows(setups)}</tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>История сканов</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Время</th><th>Топ пара</th><th>Dir</th><th>Score</th><th>Кандидаты</th><th>Время скана</th></tr></thead>
          <tbody>{_scan_history_rows(scan_history)}</tbody>
        </table>
      </div>
    </section>
  </div>
  {scan_poll}
</body>
</html>"""
