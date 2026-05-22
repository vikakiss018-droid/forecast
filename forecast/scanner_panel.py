"""HTML dashboard for scanner + futures auto-trader."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from .auto_trader import AutoTradeConfig, load_trade_history, load_trade_state
from .binance_client import trading_credentials_source
from .env_config import SETTINGS_META, get_settings_for_panel


def _e(s: Any) -> str:
    return html.escape(str(s) if s is not None else "")


def _fmt_ts(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
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
        return '<tr><td colspan="5" class="empty-cell">История появится после нескольких сканов (каждые 15 мин)</td></tr>'
    rows = []
    for h in items:
        top = h.get("top") or {}
        sym = top.get("symbol") or "—"
        rows.append(
            f"""
        <tr>
          <td class="mono">{_fmt_ts(h.get('updated_at'))}</td>
          <td>{_e(sym)}</td>
          <td>{_direction_badge(str(top.get('direction') or ''))}</td>
          <td>{_fmt_num(top.get('score'), 1) if top.get('score') is not None else '—'}</td>
          <td>{int(h.get('candidates_found') or 0)} / {int(h.get('universe_size') or 0)}</td>
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


def _trader_config_cards(at: AutoTradeConfig) -> str:
    api_src = trading_credentials_source()
    api_label = {
        "BINANCE_TRADE_*": "Отдельный торговый ключ",
        "BINANCE_*": "Общий ключ (BINANCE_*)",
        "none": "Ключи не заданы",
    }.get(api_src, api_src)
    items = [
        ("API торговли", api_label),
        ("Рынок", "Binance USDT-M Futures"),
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
        ("Выбор пары", f"Топ-{int(at.pick_from_top_n)} (первая под фильтры)"),
        ("Макс. позиций", str(int(at.max_open_positions))),
        ("Закрытие при +%", f"{_fmt_num(at.profit_close_pct, 1)}% от notional"),
    ]
    return "".join(
        f'<div class="cfg-card"><span>{_e(k)}</span><strong>{_e(v)}</strong></div>' for k, v in items
    )


def _pnl_class(v: float) -> str:
    if v > 0:
        return "pnl-pos"
    if v < 0:
        return "pnl-neg"
    return ""


def _balance_section(account: dict[str, Any]) -> str:
    if not account.get("ok"):
        err = _e(account.get("error") or "Нет данных")
        return f"""
    <section class="balance-section">
      <h2>Futures — баланс USDT</h2>
      <p class="balance-error">{err}</p>
    </section>"""
    usdt = account.get("usdt") or {}
    upnl = float(account.get("unrealized_pnl") or 0.0)
    updated = _fmt_ts(account.get("updated_at"))
    return f"""
    <section class="balance-section">
      <h2>Futures — баланс USDT <span class="hint">обновлено {updated}</span></h2>
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
          <label>В марже</label>
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
      <h2>Позиции на бирже (Futures)</h2>
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


def _open_positions_section(state: dict[str, Any], at: AutoTradeConfig, *, return_q: str) -> str:
    positions = list(state.get("open_positions") or [])
    max_n = int(at.max_open_positions)
    if not positions:
        return f"""
        <div class="pos-card pos-empty">
          <h3>Открытые позиции (0/{max_n})</h3>
          <p>Нет активных позиций в state</p>
        </div>"""

    cards: list[str] = []
    for op in positions:
        fsym = str(op.get("futures_symbol") or "")
        upnl = float(op.get("unrealized_pnl") or 0.0)
        target = float(op.get("profit_target_usdt") or 0.0)
        dry = bool(op.get("dry_run"))
        close_html = ""
        if fsym and not dry and not at.dry_run:
            sym_label = _e(op.get("symbol") or fsym)
            close_html = f"""
        <form method="post" action="/trader/close" class="pos-close-form" onsubmit="return confirm('Закрыть {sym_label} по рынку?');">
          <input type="hidden" name="futures_symbol" value="{_e(fsym)}" />
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
          <div><label>Futures</label><strong class="mono">{_e(fsym)}</strong></div>
          <div><label>Открыта</label><span class="mono">{_fmt_ts(op.get('opened_at'))}</span></div>
          <div><label>Notional</label><span>{_fmt_num(op.get('notional_usdt'), 1)} USDT</span></div>
          <div><label>Плечо</label><span>{_e(op.get('leverage'))}x</span></div>
          <div><label>uPnL</label><span class="{_pnl_class(upnl)}">{_fmt_num(upnl, 2)}</span></div>
          <div><label>Цель +{at.profit_close_pct:.0f}%</label><span class="tp">{_fmt_num(target, 2)} USDT</span></div>
          <div><label>Вход</label><span class="mono">{_fmt_num(op.get('entry_price') or op.get('entry'))}</span></div>
          <div><label>Стоп</label><span class="mono stop">{_fmt_num(op.get('stop'))}</span></div>
          <div><label>Тейк</label><span class="mono tp">{_fmt_num(op.get('take_profit'))}</span></div>
        </div>
      </div>"""
        )

    return f"""
    <div class="pos-stack">
      <p class="pos-summary">Открыто <strong>{len(positions)}/{max_n}</strong> — новые сделки не откроются при лимите</p>
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
) -> str:
    setups = report.get("top_setups") or []
    hero = setups[0] if setups else None
    max_symbols_q = "" if max_symbols is None else str(max_symbols)
    base_q = (
        f"top={int(top)}&bars={int(bars)}&timeframe={html.escape(timeframe)}"
        f"&stage1_min_score={float(stage1_min_score)}&max_symbols={html.escape(max_symbols_q)}"
    )
    refresh_url = f"/scanner?{base_q}"
    live_url = f"/scanner?{base_q}&live=1"
    json_url = f"/scanner/json?{base_q}"

    trader_on = at.enabled
    trader_live = trader_on and not at.dry_run
    status_scan = _badge("Кэш" if from_cache else "Live", "ok" if from_cache else "warn")
    status_trade = (
        _badge("LIVE", "bad") if trader_live else (_badge("Dry-run", "warn") if trader_on else _badge("Выкл", "muted"))
    )

    last_trade = trade_state.get("last_trade") or {}
    last_close = trade_state.get("last_close_at")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="refresh" content="900" />
  <title>Forecast — Сканер и торговля</title>
  <style>
    :root {{
      --bg: #070b14;
      --surface: #0f1628;
      --surface2: #141e34;
      --border: #24304d;
      --text: #e8edff;
      --muted: #8b96b8;
      --accent: #5b8cff;
      --accent2: #22d3a8;
      --long: #22c55e;
      --short: #ef4444;
      --warn: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #1a2450 0%, transparent 50%),
                  radial-gradient(900px 500px at 90% 0%, #0d3d35 0%, transparent 45%),
                  var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1440px; margin: 0 auto; padding: 20px 16px 48px; }}
    header {{
      display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
      gap: 16px; margin-bottom: 24px;
    }}
    h1 {{ margin: 0; font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; }}
    .subtitle {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
    .pills {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .badge {{
      display: inline-block; padding: 4px 10px; border-radius: 999px;
      font-size: 0.72rem; font-weight: 600; letter-spacing: 0.04em;
    }}
    .badge-long {{ background: rgba(34,197,94,.15); color: var(--long); border: 1px solid rgba(34,197,94,.35); }}
    .badge-short {{ background: rgba(239,68,68,.15); color: var(--short); border: 1px solid rgba(239,68,68,.35); }}
    .badge-ok {{ background: rgba(34,211,168,.12); color: var(--accent2); border: 1px solid rgba(34,211,168,.3); }}
    .badge-warn {{ background: rgba(245,158,11,.12); color: var(--warn); border: 1px solid rgba(245,158,11,.35); }}
    .badge-bad {{ background: rgba(239,68,68,.15); color: #fca5a5; border: 1px solid rgba(239,68,68,.4); }}
    .badge-muted {{ background: var(--surface2); color: var(--muted); border: 1px solid var(--border); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; }}
    .btn {{
      border: 1px solid var(--border); background: var(--surface2); color: var(--text);
      padding: 8px 14px; border-radius: 10px; text-decoration: none; font-size: 0.85rem;
      transition: background .15s, border-color .15s;
    }}
    .btn:hover {{ background: #1c2844; border-color: var(--accent); }}
    .btn-primary {{ background: linear-gradient(135deg, #4f7cff, #3b5bdb); border-color: transparent; }}
    .stats {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 12px; margin-bottom: 20px;
    }}
    .stat {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
      padding: 14px 16px;
    }}
    .stat label {{ display: block; font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }}
    .stat strong {{ font-size: 1.25rem; display: block; margin-top: 4px; }}
    .balance-section h2 .hint {{ font-size: 0.72rem; color: var(--muted); font-weight: 400; }}
    .balance-error {{ color: #fca5a5; margin: 0; }}
    .balance-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px;
    }}
    .balance-card {{
      background: var(--surface2); border: 1px solid var(--border); border-radius: 14px; padding: 16px;
    }}
    .balance-card label {{ font-size: 0.72rem; color: var(--muted); text-transform: uppercase; }}
    .balance-card strong {{ font-size: 1.35rem; display: block; margin-top: 6px; }}
    .balance-card small {{ font-size: 0.75rem; color: var(--muted); font-weight: 400; }}
    .balance-total {{
      background: linear-gradient(145deg, #1a2d5c 0%, #141e34 100%);
      border-color: #3d5a9f;
    }}
    .balance-total strong {{ font-size: 1.6rem; color: var(--accent2); }}
    .pnl-pos {{ color: var(--long) !important; }}
    .pnl-neg {{ color: var(--short) !important; }}
    .chip {{
      display: inline-block; background: var(--surface2); border: 1px solid var(--border);
      padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; margin: 2px 4px 2px 0;
    }}
    .skip-reasons {{ margin: 12px 0 0; font-size: 0.82rem; }}
    .muted {{ color: var(--muted); }}
    section {{
      background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
      padding: 18px 20px; margin-bottom: 20px;
    }}
    section h2 {{
      margin: 0 0 14px; font-size: 1.05rem; font-weight: 600;
      display: flex; align-items: center; gap: 8px;
    }}
    section h2::before {{
      content: ""; width: 4px; height: 1.1em; background: var(--accent); border-radius: 2px;
    }}
    .hero {{
      background: linear-gradient(145deg, #152040 0%, #0f1628 60%);
      border: 1px solid #2a3f6f; border-radius: 14px; padding: 18px 20px;
    }}
    .hero.empty {{ color: var(--muted); text-align: center; padding: 32px; }}
    .hero-top {{ display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .hero-symbol {{ font-size: 1.6rem; font-weight: 700; }}
    .hero-score {{ margin-left: auto; color: var(--accent2); font-weight: 600; }}
    .hero-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 12px;
    }}
    .hero-grid label {{ font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }}
    .hero-why {{ margin: 14px 0 0; color: var(--muted); font-size: 0.88rem; }}
    .hero-note {{ margin: 8px 0 0; font-size: 0.82rem; color: var(--accent2); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; }}
    .stop {{ color: var(--short); }}
    .tp {{ color: var(--long); }}
    .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
    .cfg-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px;
    }}
    .cfg-card {{
      background: var(--surface2); border-radius: 10px; padding: 10px 12px; border: 1px solid var(--border);
    }}
    .cfg-card span {{ display: block; font-size: 0.68rem; color: var(--muted); text-transform: uppercase; }}
    .cfg-card strong {{ font-size: 0.9rem; margin-top: 4px; display: block; }}
    .pos-card {{ border-radius: 12px; padding: 14px; border: 1px solid var(--border); }}
    .pos-open {{ background: rgba(34,211,168,.06); border-color: rgba(34,211,168,.25); }}
    .pos-empty {{ background: var(--surface2); color: var(--muted); }}
    .pos-card h3 {{ margin: 0 0 10px; font-size: 0.95rem; }}
    .pos-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px;
    }}
    .pos-grid label {{ font-size: 0.68rem; color: var(--muted); display: block; }}
    .table-wrap {{ overflow-x: auto; border-radius: 10px; border: 1px solid var(--border); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ background: var(--surface2); color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: .04em; position: sticky; top: 0; }}
    tr:hover td {{ background: rgba(91,140,255,.06); }}
    tr.rank-1 td {{ background: rgba(91,140,255,.08); }}
    td.sym {{ font-weight: 600; color: var(--accent); }}
    td.why {{ max-width: 220px; color: var(--muted); }}
    td.reason {{ color: var(--muted); max-width: 200px; }}
    .trend-up {{ color: var(--long); }}
    .trend-down {{ color: var(--short); }}
    .trend-range {{ color: var(--warn); }}
    .empty-cell {{ text-align: center; color: var(--muted); padding: 24px !important; }}
    .settings-section .settings-hint {{ color: var(--muted); font-size: 0.85rem; margin: 0 0 14px; }}
    .save-banner {{ padding: 10px 14px; border-radius: 10px; margin-bottom: 14px; font-size: 0.9rem; }}
    .save-banner.ok {{ background: rgba(34,211,168,.12); border: 1px solid rgba(34,211,168,.35); color: var(--accent2); }}
    .settings-form .form-group-title {{ margin: 16px 0 10px; font-size: 0.9rem; color: var(--muted); }}
    .field-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px;
    }}
    .field {{ display: flex; flex-direction: column; gap: 6px; }}
    .field span {{ font-size: 0.75rem; color: var(--muted); }}
    .field input[type="text"] {{
      background: var(--surface2); border: 1px solid var(--border); border-radius: 8px;
      color: var(--text); padding: 8px 10px; font-size: 0.88rem;
    }}
    .bool-field {{ flex-direction: row; align-items: center; gap: 8px; }}
    .bool-field input {{ width: auto; }}
    .btn-save {{ margin-top: 16px; cursor: pointer; border: none; }}
    .btn-close {{
      background: rgba(239,68,68,.15); border-color: rgba(239,68,68,.45); color: #fca5a5;
      cursor: pointer; font-size: 0.8rem; padding: 6px 12px;
    }}
    .btn-close:hover {{ background: rgba(239,68,68,.28); }}
    .pos-stack {{ display: flex; flex-direction: column; gap: 12px; }}
    .pos-summary {{ margin: 0 0 4px; font-size: 0.85rem; color: var(--muted); }}
    .pos-head {{
      display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 10px;
    }}
    .pos-head h3 {{ margin: 0; flex: 1; min-width: 120px; }}
    .pos-close-form {{ margin: 0; }}
    footer {{ text-align: center; color: var(--muted); font-size: 0.78rem; margin-top: 24px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Forecast Dashboard</h1>
        <p class="subtitle">Сканер рынка · Futures auto-trade · обновление каждые 15 мин</p>
      </div>
      <div class="pills">
        {status_scan}
        {status_trade}
        {_badge("Futures", "ok")}
      </div>
    </header>

    <div class="actions">
      <a class="btn btn-primary" href="{refresh_url}">Обновить</a>
      <a class="btn" href="{live_url}">Live-скан</a>
      <a class="btn" href="{json_url}" target="_blank">JSON API</a>
      <a class="btn" href="/trader/status" target="_blank">Trader API</a>
    </div>

    <div class="stats">
      <div class="stat"><label>Последний скан</label><strong>{_fmt_ts(updated_at)}</strong></div>
      <div class="stat"><label>Вселенная</label><strong>{int(report.get('universe_size', 0))}</strong></div>
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
        {_open_positions_section(trade_state, at, return_q=base_q)}
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
            <thead><tr><th>Время</th><th>Топ пара</th><th>Dir</th><th>Score</th><th>Кандидаты</th></tr></thead>
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
      · <a class="btn" href="/legacy" style="display:inline-block;margin-top:8px">Старый forecast UI</a>
    </footer>
  </div>
</body>
</html>"""
