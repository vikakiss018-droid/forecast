"""HTML dashboard for scanner + futures auto-trader."""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

from .auto_trader import AutoTradeConfig, load_closed_trades, load_trade_history, load_trade_state
from .binance_client import trading_credentials_source
from .env_config import SETTINGS_META, get_settings_for_panel
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


def _action_badge(action: str) -> str:
    a = (action or "").lower()
    if a in ("executed", "dry_run"):
        return _badge(action.upper(), "ok")
    if a == "failed":
        return _badge("FAILED", "bad")
    return _badge(action or "—", "muted")


def _hero_setup(setup_row: dict[str, Any] | None, *, pick_from_top_n: int = 4) -> str:
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
      <p class="hero-note">Автоторговля: <strong>market</strong> вход, перебор <strong>топ-{int(pick_from_top_n)}</strong> (не только №1). №1 на экране — лучший score.</p>
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


def _trade_history_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<tr><td colspan="6" class="empty-cell">История сделок появится при включённом AUTO_TRADE_ENABLED</td></tr>'
    rows = []
    for t in items:
        rows.append(
            f"""
        <tr>
          <td class="mono">{_fmt_ts(t.get('at'))}</td>
          <td>{_action_badge(str(t.get('action', '')))}</td>
          <td>{_e(t.get('symbol') or '—')}</td>
          <td>{_direction_badge(str(t.get('side') or '')) if t.get('side') else '—'}</td>
          <td class="reason">{_e(t.get('reason') or '—')}</td>
          <td>{'DRY' if t.get('dry_run') else 'LIVE'}</td>
        </tr>"""
        )
    return "\n".join(rows)


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h} ч {m} мин" if m else f"{h} ч"


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


def _close_reason_label(reason: str | None) -> str:
    r = str(reason or "")
    labels = {
        "MANUAL_PANEL": "Вручную (панель)",
        "EXCHANGE_CLOSED": "На бирже (стоп/тейк)",
        "position_closed": "Позиция закрыта",
    }
    if r in labels:
        return labels[r]
    if r.startswith("PROFIT_"):
        return "Прибыль (авто % маржи)"
    if r.startswith("STOP_LOSS_ROI"):
        return "Стоп по ROI (uPnL)"
    return r or "—"


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
    closed_cls = "tab active" if active == "closed" else "tab"
    pairs_cls = "tab active" if active == "pairs" else "tab"
    return f"""
    <nav class="dash-tabs">
      <a class="{scan_cls}" href="/scanner?{base_q}">Сканер и торговля</a>
      <a class="{closed_cls}" href="/scanner?tab=closed&amp;{base_q}">Закрытые сделки</a>
      <a class="{pairs_cls}" href="/scanner/pairs" target="_blank">Тест пар (400)</a>
    </nav>"""


def _closed_trades_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    pnls = [float(r["realized_pnl"]) for r in rows if r.get("realized_pnl") is not None]
    durs = [int(r["duration_sec"]) for r in rows if r.get("duration_sec") is not None]
    wins = sum(1 for p in pnls if p > 0)
    avg_pnl = sum(pnls) / len(pnls) if pnls else None
    avg_dur = int(sum(durs) / len(durs)) if durs else None
    return f"""
    <div class="stats stats-closed">
      <div class="stat"><label>Закрыто</label><strong>{len(rows)}</strong></div>
      <div class="stat"><label>В плюс</label><strong class="pnl-pos">{wins}</strong></div>
      <div class="stat"><label>Средн. PnL</label><strong class="{_pnl_class(avg_pnl)}">{_fmt_num(avg_pnl, 2) if avg_pnl is not None else '—'}</strong></div>
      <div class="stat"><label>Средн. время</label><strong>{_fmt_duration(avg_dur)}</strong></div>
    </div>"""


def _closed_trades_rows(items: list[dict[str, Any]]) -> str:
    if not items:
        return (
            '<tr><td colspan="14" class="empty-cell">'
            "Закрытые LIVE-сделки появятся после первого закрытия позиции"
            "</td></tr>"
        )
    rows = []
    for t in items:
        pnl = t.get("realized_pnl")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl_f = None
        rows.append(
            f"""
        <tr>
          <td class="sym">{_e(t.get('symbol'))}</td>
          <td>{_direction_badge(str(t.get('side') or ''))}</td>
          <td>{_e(t.get('pattern') or '—')}</td>
          <td><span class="trend-{_e(t.get('trend'))}">{_e(t.get('trend') or '—')}</span></td>
          <td>{_fmt_num(t.get('score'), 1)}</td>
          <td>{_fmt_num(t.get('probability_pct'), 1)}%</td>
          <td>{_fmt_num(t.get('risk_reward'), 2)}</td>
          <td class="mono">{_fmt_ts(t.get('opened_at'))}</td>
          <td class="mono">{_fmt_ts(t.get('closed_at'))}</td>
          <td>{_fmt_duration(t.get('duration_sec'))}</td>
          <td class="reason">{_e(_close_reason_label(t.get('close_reason')))}</td>
          <td>{_fmt_num(t.get('notional_usdt'), 1)}</td>
          <td class="mono {_pnl_class(pnl_f)}">{_fmt_num(pnl_f, 2) if pnl_f is not None else '—'}</td>
          <td class="why">{_e(t.get('why_selected') or '—')}</td>
        </tr>"""
        )
    return "\n".join(rows)


def render_closed_trades_dashboard(
    *,
    closed_trades: list[dict[str, Any]],
    base_q: str,
    saved_msg: str | None = None,
) -> str:
    banner = ""
    if saved_msg:
        banner = f'<div class="save-banner ok">{_e(saved_msg)}</div>'
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="3600" />
  <title>Forecast — Закрытые сделки</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=False)}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Закрытые сделки</h1>
      <p class="subtitle">Журнал LIVE-позиций: паттерн, тренд, время в сделке, причина закрытия</p>
    </header>
    {_dashboard_tabs(active="closed", base_q=base_q)}
    {banner}
    {_closed_trades_summary(closed_trades)}
    <section>
      <h2>Все закрытые сделки</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Пара</th><th>Сторона</th><th>Паттерн</th><th>Тренд</th><th>Score</th>
              <th>Prob</th><th>R:R</th><th>Открыта</th><th>Закрыта</th><th>Длительность</th>
              <th>Причина</th><th>Notional</th><th>PnL</th><th>Почему</th>
            </tr>
          </thead>
          <tbody>{_closed_trades_rows(closed_trades)}</tbody>
        </table>
      </div>
    </section>
    <footer>Данные: auto_trade_state.json → closed_trades · <a href="/scanner?{base_q}" style="color:var(--accent)">← Сканер</a></footer>
  </div>
</body>
</html>"""


def _tf_study_rows(ranking: list[dict[str, Any]]) -> str:
    if not ranking:
        return '<tr><td colspan="8" class="empty-cell">Нет данных — запустите тест</td></tr>'
    rows: list[str] = []
    for i, r in enumerate(ranking, start=1):
        pf = r.get("profit_factor")
        pf_s = _fmt_num(pf, 2) if pf is not None else "—"
        target_ok = "да" if r.get("target_reached") else "нет"
        rows.append(
            f"""
        <tr class="{'rank-1' if i == 1 else ''}">
          <td>{i}</td>
          <td class="sym">{_e(r.get('timeframe'))}</td>
          <td>{int(r.get('trades') or 0)} / {int(r.get('target') or 100)}</td>
          <td>{target_ok}</td>
          <td>{_fmt_num(r.get('win_rate_pct'), 1)}%</td>
          <td class="{_pnl_class(float(r.get('avg_r') or 0))}">{_fmt_num(r.get('avg_r'), 3)}</td>
          <td class="{_pnl_class(float(r.get('total_r') or 0))}">{_fmt_num(r.get('total_r'), 2)}</td>
          <td>{pf_s}</td>
          <td class="muted">{_e(r.get('best_symbol') or '—')}</td>
        </tr>"""
        )
    return "".join(rows)


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
      <p class="subtitle">Combined backtest · параметры из .env (как live-скан)</p>
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
      (<code>FORECAST_*</code>, <code>TREND_*</code>, <code>AUTO_TRADE_*</code>, <code>RANK_*</code>) —
      те же, что у почасового скана. По завершении выберите пары и утвердите —
      они попадут в <code>symbol_ranking_filtered_r05_win50.json</code> для почасового скана.
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
      <a href="/scanner" style="color:var(--accent)">← Сканер и торговля</a>
    </footer>
  </div>
  {poll_script}
</body>
</html>"""


def _market_display_name(market_type: str) -> str:
    return "Binance Spot" if str(market_type).strip().lower() == "spot" else "Binance USDT-M Futures"


def _trader_config_cards(at: AutoTradeConfig) -> str:
    api_src = trading_credentials_source()
    api_label = {
        "BINANCE_TRADE_*": "Отдельный торговый ключ",
        "BINANCE_*": "Общий ключ (BINANCE_*)",
        "none": "Ключи не заданы",
    }.get(api_src, api_src)
    mkt = str(getattr(at, "market_type", "spot")).strip().lower()
    items = [
        ("API торговли", api_label),
        ("Рынок (торговля)", _market_display_name(mkt)),
        ("Скан OHLCV", "Binance Spot (всегда)"),
        ("Включено", "Да" if at.enabled else "Нет"),
        ("Режим", "Dry-run" if at.dry_run else "LIVE"),
        ("Min score", _fmt_num(at.min_score, 1)),
        ("Min prob %", _fmt_num(at.min_probability_pct, 1)),
        ("Min R:R", _fmt_num(at.min_risk_reward, 2)),
        ("Риск %", _fmt_num(at.risk_pct_of_balance, 2)),
        ("Max notional", f"{_fmt_num(at.max_notional_usdt, 1)} USDT"),
        ("Плечо", f"{at.leverage}x"),
        ("Маржа", _e(at.margin_mode)),
        ("Cooldown", f"{at.cooldown_minutes} мин"),
        ("Выбор пар", f"Топ-{int(at.pick_from_top_n)} (все подходящие за скан)"),
        ("Макс. позиций", str(int(at.max_open_positions))),
        ("Закрытие при +%", f"{_fmt_num(at.profit_close_pct, 1)}% от маржи"),
        (
            "Стоп ROI (uPnL)",
            f"{_fmt_num(at.stop_loss_roi_usdt, 1)} USDT" if at.stop_loss_roi_usdt > 0 else "Выкл",
        ),
        (
            "Пробитие уровня",
            "Разрешено" if at.allow_level_breakout else "Отключено",
        ),
        (
            "Паттерн triangle",
            "Разрешён" if at.allow_triangle else "Отключён",
        ),
        (
            "Часы входа (UTC)",
            f"{at.allowed_hours[0]}-{at.allowed_hours[1]}" if at.allowed_hours else "Все",
        ),
        (
            "Min ATR",
            f"{at.min_atr_pct * 100:.2f}%" if at.min_atr_pct > 0 else "Выкл",
        ),
    ]
    return "".join(
        f'<div class="cfg-card"><span>{_e(k)}</span><strong>{_e(v)}</strong></div>' for k, v in items
    )


def _balance_section(account: dict[str, Any]) -> str:
    is_spot = str(account.get("market") or "").lower() == "spot"
    title = "Spot — баланс USDT" if is_spot else "Futures — баланс USDT"
    used_label = "В ордерах" if is_spot else "В марже"
    if not account.get("ok"):
        err = _e(account.get("error") or "Нет данных")
        return f"""
    <section class="balance-section">
      <h2>{title}</h2>
      <p class="balance-error">{err}</p>
    </section>"""
    usdt = account.get("usdt") or {}
    upnl = float(account.get("unrealized_pnl") or 0.0)
    updated = _fmt_ts(account.get("updated_at"))
    return f"""
    <section class="balance-section">
      <h2>{title} <span class="hint">обновлено {updated}</span></h2>
      <div class="balance-grid">
        <div class="balance-card balance-total">
          <label>Капитал (total)</label>
          <strong>{_fmt_num(usdt.get('total'), 2)} <small>USDT</small></strong>
        </div>
        <div class="balance-card">
          <label>Свободно</label>
          <strong>{_fmt_num(usdt.get('free'), 2)}</strong>
        </div>
        <div class="balance-card">
          <label>{used_label}</label>
          <strong>{_fmt_num(usdt.get('used'), 2)}</strong>
        </div>
        <div class="balance-card">
          <label>Нереализ. PnL</label>
          <strong class="{_pnl_class(upnl)}">{_fmt_num(upnl, 2)}</strong>
        </div>
        <div class="balance-card">
          <label>Позиций на бирже</label>
          <strong>{int(account.get('positions_count', 0))}</strong>
        </div>
      </div>
    </section>"""


def _exchange_positions_table(account: dict[str, Any]) -> str:
    positions = account.get("positions") or []
    if not account.get("ok") or not positions:
        return ""
    rows = []
    for p in positions:
        upnl = float(p.get("unrealized_pnl") or 0.0)
        rows.append(
            f"""
        <tr>
          <td class="sym">{_e(p.get('symbol'))}</td>
          <td>{_direction_badge(str(p.get('side', '')))}</td>
          <td class="mono">{_fmt_num(p.get('contracts'), 4)}</td>
          <td>{_fmt_num(p.get('notional_usdt'), 1)}</td>
          <td class="mono">{_fmt_num(p.get('entry_price'))}</td>
          <td class="mono {_pnl_class(upnl)}">{_fmt_num(upnl, 2)}</td>
          <td>{_e(p.get('leverage'))}x</td>
        </tr>"""
        )
    return f"""
    <section>
      <h2>Позиции на бирже ({'Spot' if str(account.get('market') or '').lower() == 'spot' else 'Futures'})</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Пара</th><th>Сторона</th><th>Контракты</th><th>Notional</th>
              <th>Вход</th><th>uPnL</th><th>Плечо</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
    </section>"""


def _bot_stats_section(stats: dict[str, Any]) -> str:
    reasons = stats.get("top_skip_reasons") or []
    reason_html = ""
    if reasons:
        chips = " ".join(
            f'<span class="chip">{_e(r)} × {n}</span>' for r, n in reasons[:5]
        )
        reason_html = f'<p class="skip-reasons"><span class="muted">Частые skip:</span> {chips}</p>'

    return f"""
    <section>
      <h2>Статистика бота</h2>
      <div class="stats stats-bot">
        <div class="stat"><label>Событий торговли</label><strong>{int(stats.get('trade_events_total', 0))}</strong></div>
        <div class="stat"><label>LIVE сделок</label><strong class="pnl-pos">{int(stats.get('live_executed', 0))}</strong></div>
        <div class="stat"><label>Dry-run</label><strong>{int(stats.get('dry_run', 0))}</strong></div>
        <div class="stat"><label>Пропущено</label><strong>{int(stats.get('skipped', 0))}</strong></div>
        <div class="stat"><label>Ошибок</label><strong class="pnl-neg">{int(stats.get('failed', 0))}</strong></div>
        <div class="stat"><label>Сканов в истории</label><strong>{int(stats.get('scans_in_history', 0))}</strong></div>
      </div>
      {reason_html}
    </section>"""


def _settings_form_html(*, return_q: str, saved_msg: str | None = None) -> str:
    rows = get_settings_for_panel()
    trade_fields: list[str] = []
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
        if row.get("group") == "scan":
            scan_fields.append(field)
        else:
            trade_fields.append(field)

    banner = ""
    if saved_msg:
        banner = f'<div class="save-banner ok">{_e(saved_msg)}</div>'

    return f"""
    <section class="settings-section">
      <h2>Настройки (.env)</h2>
      <p class="settings-hint">Ключи Binance и пароль панели (PANEL_AUTH_*) — только в .env на сервере, не здесь. После сохранения настройки применяются сразу.</p>
      {banner}
      <form method="post" action="/scanner/settings" class="settings-form">
        <input type="hidden" name="return_q" value="{_e(return_q)}" />
        <h3 class="form-group-title">Автоторговля</h3>
        <div class="field-grid">{"".join(trade_fields)}</div>
        <h3 class="form-group-title">Сканер (таймер)</h3>
        <div class="field-grid">{"".join(scan_fields)}</div>
        <button type="submit" class="btn btn-primary btn-save">Сохранить в .env</button>
      </form>
    </section>"""


def _position_chart_url(op: dict[str, Any], *, timeframe: str, bars: int = 120) -> str:
    symbol = str(op.get("symbol") or "").strip()
    if not symbol:
        return ""
    params: dict[str, str | int | float] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "side": str(op.get("side") or "long"),
        "bars": max(40, min(int(bars), 500)),
    }
    for key, fields in (
        ("entry", ("entry_price", "entry")),
        ("stop", ("stop",)),
        ("tp", ("take_profit",)),
    ):
        val = None
        for f in fields:
            try:
                x = float(op.get(f) or 0)
                if x > 0 and x == x:
                    val = x
                    break
            except (TypeError, ValueError):
                continue
        if val is not None:
            params[key] = val
    return f"/trader/chart?{urlencode(params)}"


def _exchange_holdings_as_positions(account: dict[str, Any]) -> list[dict[str, Any]]:
    if not account.get("ok") or str(account.get("market") or "").lower() != "spot":
        return []
    out: list[dict[str, Any]] = []
    for p in account.get("positions") or []:
        sym = str(p.get("symbol") or "")
        if not sym or sym == "USDT/USDT":
            continue
        out.append(
            {
                "symbol": sym,
                "side": str(p.get("side") or "long"),
                "amount": p.get("contracts"),
                "entry_price": p.get("entry_price"),
                "notional_usdt": p.get("notional_usdt"),
                "unrealized_pnl": p.get("unrealized_pnl"),
                "leverage": 1,
                "from_exchange_only": True,
            }
        )
    return out


def _open_positions_section(
    state: dict[str, Any],
    at: AutoTradeConfig,
    *,
    return_q: str,
    timeframe: str = "1h",
    account: dict[str, Any] | None = None,
) -> str:
    from .auto_trader import open_positions_for_panel

    positions = open_positions_for_panel(state, account, at)
    exchange_only = False
    if not positions and account:
        positions = _exchange_holdings_as_positions(account)
        exchange_only = bool(positions)
    max_n = int(at.max_open_positions)
    if not positions:
        return f"""
        <div class="pos-card pos-empty">
          <h3>Открытые позиции (0/{max_n})</h3>
          <p>Нет активных позиций в state. Если сделка есть на Binance Spot — обновите страницу после деплоя fix или проверьте «Позиции на бирже» ниже.</p>
        </div>"""

    mkt_label = "Spot" if str(getattr(at, "market_type", "futures")).lower() == "spot" else "Futures"
    cards: list[str] = []
    for op in positions:
        fsym = str(op.get("futures_symbol") or "")
        spot_sym = str(op.get("symbol") or "")
        close_key = spot_sym if mkt_label == "Spot" else fsym
        upnl = float(op.get("unrealized_pnl") or 0.0)
        notional = float(op.get("notional_usdt") or 0.0)
        lev = max(1, int(op.get("leverage") or 1))
        margin = notional / lev if notional > 0 else 0.0
        target = float(op.get("profit_target_usdt") or 0.0)
        loss_lim = float(op.get("loss_limit_usdt") or at.stop_loss_roi_usdt or 0.0)
        dry = bool(op.get("dry_run"))
        ex_only = bool(op.get("from_exchange_only"))
        close_html = ""
        if ex_only:
            close_html = '<span class="muted">С биржи (state пуст) — закройте вручную или дождитесь sync</span>'
        elif close_key and not dry and not at.dry_run:
            sym_label = _e(op.get("symbol") or close_key)
            close_html = f"""
        <form method="post" action="/trader/close" class="pos-close-form" onsubmit="return confirm('Закрыть {sym_label} по рынку?');">
          <input type="hidden" name="futures_symbol" value="{_e(close_key)}" />
          <input type="hidden" name="return_q" value="{_e(return_q)}" />
          <button type="submit" class="btn btn-close">Закрыть</button>
        </form>"""
        elif dry or at.dry_run:
            close_html = '<span class="muted">Dry-run — закрытие недоступно</span>'

        cards.append(
            f"""
      <div class="pos-card pos-open">
        <div class="pos-head">
          <h3>{_e(op.get('symbol'))}</h3>
          {_direction_badge(str(op.get('side', '')))}
          {close_html}
        </div>
        <div class="pos-grid">
          <div><label>{mkt_label}</label><strong class="mono">{_e(fsym or spot_sym)}</strong></div>
          <div><label>Открыта</label><span class="mono">{_fmt_ts(op.get('opened_at'))}</span></div>
          <div><label>Notional</label><span>{_fmt_num(op.get('notional_usdt'), 1)} USDT</span></div>
          <div><label>Плечо</label><span>{_e(op.get('leverage'))}x</span></div>
          <div><label>uPnL</label><span class="{_pnl_class(upnl)}">{_fmt_num(upnl, 2)}</span></div>
          <div><label>Маржа</label><span>{_fmt_num(margin, 2)} USDT</span></div>
          <div><label>Цель +{at.profit_close_pct:.0f}% маржи</label><span class="tp">{_fmt_num(target, 2)} USDT</span></div>
          <div><label>Стоп ROI</label><span class="stop">{'—' if loss_lim <= 0 else f'−{_fmt_num(loss_lim, 2)} USDT'}</span></div>
          <div><label>Вход</label><span class="mono">{_fmt_num(op.get('entry_price') or op.get('entry'))}</span></div>
          <div><label>Стоп</label><span class="mono stop">{_fmt_num(op.get('stop'))}</span></div>
          <div><label>Тейк</label><span class="mono tp">{_fmt_num(op.get('take_profit'))}</span></div>
        </div>
      </div>"""
        )

    hint = ""
    if exchange_only:
        hint = '<p class="pos-summary muted">Показаны holdings с биржи (в state бота записи нет — будет восстановлено после sync)</p>'
    return f"""
    <div class="pos-stack">
      <p class="pos-summary">Открыто <strong>{len(positions)}/{max_n}</strong> — новые сделки не откроются при лимите</p>
      {hint}
      {"".join(cards)}
    </div>"""


def render_scanner_dashboard(
    *,
    report: dict[str, Any],
    updated_at: str | None,
    from_cache: bool,
    scan_config: dict[str, Any],
    at: AutoTradeConfig,
    trade_state: dict[str, Any],
    scan_history: list[dict[str, Any]],
    trade_history: list[dict[str, Any]],
    account: dict[str, Any],
    bot_stats: dict[str, Any],
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

    trader_on = at.enabled
    trader_live = trader_on and not at.dry_run
    status_scan = _badge("Кэш" if from_cache else "Live", "ok" if from_cache else "warn")
    status_trade = (
        _badge("LIVE", "bad") if trader_live else (_badge("Dry-run", "warn") if trader_on else _badge("Выкл", "muted"))
    )

    last_trade = trade_state.get("last_trade") or {}
    last_close = trade_state.get("last_close_at")
    sym_scanned = int(report.get("symbols_scanned") or report.get("universe_size") or 0)
    scan_dur = report.get("scan_duration_sec")
    market_label = "Spot 1x" if str(getattr(at, "market_type", "futures")).lower() == "spot" else "Futures"
    scan_mode = str(report.get("mode") or "scanner")
    if scan_mode == "trend_plus_range":
        scan_hint = "тренд + флет · 50 пар"
    elif scan_mode == "trend_momentum":
        scan_hint = "тренд 50 пар"
    else:
        scan_hint = "сканер"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="3600" />
  <title>Forecast — Сканер и торговля</title>
  {_panel_fonts_link()}
  <style>{_panel_theme_css(full=True)}{_progress_panel_css()}</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Forecast Dashboard</h1>
        <p class="subtitle">{scan_hint} · {market_label} · обновление каждые 15 мин</p>
      </div>
      <div class="pills">
        {status_scan}
        {status_trade}
        {_badge(market_label, "ok")}
      </div>
    </header>

    {_dashboard_tabs(active="scan", base_q=base_q)}

    {_progress_bar_block(visible=scan_watch)}

    <div class="actions">
      <a class="btn btn-primary" href="{refresh_url}">Обновить</a>
      <form method="post" action="/scanner/live/run" style="display:inline">
        <input type="hidden" name="return_q" value="{_e(base_q)}" />
        <input type="hidden" name="top" value="{int(top)}" />
        <input type="hidden" name="bars" value="{int(bars)}" />
        <input type="hidden" name="timeframe" value="{_e(timeframe)}" />
        <input type="hidden" name="stage1_min_score" value="{float(stage1_min_score)}" />
        <button type="submit" class="btn">Live-скан</button>
      </form>
      <a class="btn" href="/scanner/pairs" target="_blank">Тест пар (400)</a>
    </div>

    <div class="stats">
      <div class="stat"><label>Live-пары</label><strong>{live_pairs}</strong></div>
      <div class="stat"><label>Последний скан</label><strong>{_fmt_ts(updated_at)}</strong></div>
      <div class="stat"><label>Пар в скане</label><strong>{sym_scanned}</strong></div>
      <div class="stat"><label>Время скана</label><strong>{_fmt_scan_duration(scan_dur)}</strong></div>
      <div class="stat"><label>Кандидаты</label><strong>{int(report.get('candidates_found', 0))}</strong></div>
      <div class="stat"><label>Таймфрейм</label><strong>{_e(timeframe)}</strong></div>
      <div class="stat"><label>Bars</label><strong>{int(bars)}</strong></div>
      <div class="stat"><label>Stage1 ≥</label><strong>{float(stage1_min_score):.0f}</strong></div>
      <div class="stat"><label>Открыто позиций</label><strong>{len(trade_state.get('open_positions') or [])}/{int(at.max_open_positions)}</strong></div>
      <div class="stat"><label>Посл. сделка</label><strong>{_fmt_ts(last_trade.get('at'))}</strong></div>
      <div class="stat"><label>Закрытие</label><strong>{_fmt_ts(last_close) if last_close else '—'}</strong></div>
    </div>

    {_balance_section(account)}
    {_settings_form_html(return_q=base_q, saved_msg=saved_msg)}
    {_bot_stats_section(bot_stats)}
    {_exchange_positions_table(account)}

    <section>
      <h2>Лучший сетап (№1)</h2>
      {_hero_setup(hero, pick_from_top_n=int(at.pick_from_top_n))}
    </section>

    <div class="two-col">
      <section>
        <h2>Автоторговля — настройки</h2>
        <div class="cfg-grid">{_trader_config_cards(at)}</div>
      </section>
      <section>
        <h2>Открытые позиции бота</h2>
        {_open_positions_section(trade_state, at, return_q=base_q, timeframe=timeframe, account=account)}
      </section>
    </div>

    <section>
      <h2>Топ сетапы сканера</h2>
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

    <div class="two-col">
      <section>
        <h2>История сканов</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Время</th><th>Топ пара</th><th>Dir</th><th>Score</th><th>Кандидаты</th><th>Время скана</th></tr></thead>
            <tbody>{_scan_history_rows(scan_history)}</tbody>
          </table>
        </div>
      </section>
      <section>
        <h2>История торговли</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Время</th><th>Действие</th><th>Пара</th><th>Сторона</th><th>Причина</th><th>Режим</th></tr></thead>
            <tbody>{_trade_history_rows(trade_history)}</tbody>
          </table>
        </div>
      </section>
    </div>

    <footer>
      Кэш: market_scan_latest.json · История: scan_history.jsonl · Торги: auto_trade_state.json
      · <a href="/scanner?tab=closed&amp;{base_q}" style="color:var(--accent)">Закрытые</a>
      · <a href="/scanner/pairs" target="_blank" style="color:var(--accent)">Тест пар</a>
      · <a class="btn" href="/legacy" style="display:inline-block;margin-top:8px">Старый forecast UI</a>
    </footer>
  </div>
  {scan_poll}
</body>
</html>"""
