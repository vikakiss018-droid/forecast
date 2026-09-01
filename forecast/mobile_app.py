"""Phone PWA: выгодные позиции сканера + уведомления при score выше порога."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .push_alerts import alert_min_score, flatten_setup
from .scan_cache import load_scan_result, report_from_cache

STATIC_MOBILE_DIR = Path(__file__).resolve().parent / "static" / "mobile"


def mobile_icon_file(name: str) -> Path | None:
    path = STATIC_MOBILE_DIR / name
    return path if path.is_file() else None


def setups_payload() -> dict[str, Any]:
    threshold = alert_min_score()
    cached = load_scan_result()
    report, updated_at = report_from_cache(cached)
    setups = [flatten_setup(row, threshold) for row in (report.get("top_setups") or [])]
    setups.sort(key=lambda s: (-float(s.get("score") or 0), str(s.get("symbol") or "")))
    return {
        "updated_at": updated_at,
        "timeframe": report.get("timeframe"),
        "candidates_found": report.get("candidates_found") or 0,
        "symbols_scanned": report.get("symbols_scanned") or report.get("universe_size") or 0,
        "alert_min_score": threshold,
        "hot_count": sum(1 for s in setups if s.get("hot")),
        "setups": setups,
    }


def mobile_manifest_json() -> str:
    return json.dumps(
        {
            "name": "Forecast — выгодные позиции",
            "short_name": "Forecast",
            "description": "Сетапы сканера и уведомления, если score выше 35",
            "start_url": "/scanner?mobile=1",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#140a18",
            "theme_color": "#140a18",
            "lang": "ru",
            "icons": [
                {
                    "src": "/m/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/m/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/m/icon-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def render_mobile_app() -> str:
    threshold = alert_min_score()
    html = _MOBILE_HTML.replace("%%THRESHOLD%%", json.dumps(float(threshold)))
    return html


_MOBILE_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#140a18" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="Forecast" />
  <title>Forecast — позиции</title>
  <link rel="manifest" href="/manifest.webmanifest" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <link rel="icon" type="image/png" href="/m/icon-192.png" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&family=Quicksand:wght@600;700&display=swap" rel="stylesheet" />
  <style>
    :root {
      --bg: #140a18;
      --bg2: #1f0f28;
      --card: rgba(42, 22, 52, 0.78);
      --glass: rgba(255, 240, 248, 0.06);
      --border: rgba(255, 182, 220, 0.28);
      --text: #fff0f8;
      --muted: #c9a8d4;
      --accent: #ff9ecf;
      --accent2: #e9b8ff;
      --long: #b8f5d4;
      --short: #ff9eb8;
      --warn: #ffd89a;
      --safe-top: env(safe-area-inset-top, 0px);
      --safe-bot: env(safe-area-inset-bottom, 0px);
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
    html, body { margin: 0; min-height: 100%; }
    body {
      font-family: Nunito, system-ui, sans-serif;
      color: var(--text);
      background:
        radial-gradient(ellipse 520px 340px at 12% -8%, rgba(255, 158, 207, 0.28), transparent 55%),
        radial-gradient(ellipse 420px 280px at 100% 0%, rgba(167, 139, 250, 0.22), transparent 50%),
        linear-gradient(165deg, var(--bg2), var(--bg) 48%, #0f0812);
      padding: calc(12px + var(--safe-top)) 14px calc(28px + var(--safe-bot));
    }
    header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
    h1 {
      margin: 0;
      font-family: Quicksand, Nunito, sans-serif;
      font-size: 1.45rem;
      background: linear-gradient(92deg, #fff8fc, #ffc8e8 45%, #e9d5ff);
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .sub { color: var(--muted); font-size: 0.78rem; margin-top: 4px; }
    .notify-btn {
      border: 1px solid var(--border); background: var(--glass); color: var(--text);
      border-radius: 999px; padding: 8px 12px; font-size: 0.78rem; font-weight: 700;
    }
    .notify-btn.on { background: linear-gradient(135deg, #ff9ecf, #c4b5fd); color: #2a1020; border-color: transparent; }
    .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 14px; }
    .stat {
      background: var(--glass); border: 1px solid var(--border); border-radius: 16px; padding: 10px 10px 12px;
    }
    .stat span { display: block; font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
    .stat strong { display: block; margin-top: 4px; font-family: Quicksand, sans-serif; font-size: 1.05rem; }
    .filters { display: flex; gap: 8px; margin-bottom: 12px; }
    .chip {
      border: 1px solid var(--border); background: var(--glass); color: var(--text);
      border-radius: 999px; padding: 7px 12px; font-size: 0.78rem; font-weight: 700;
    }
    .chip.active { background: linear-gradient(135deg, #ff9ecf, #c4b5fd); color: #2a1020; border-color: transparent; }
    .banner {
      display: none; border-radius: 14px; padding: 10px 12px; font-size: 0.82rem; margin-bottom: 12px;
      background: rgba(255, 216, 154, 0.12); border: 1px solid rgba(255, 216, 154, 0.35); color: var(--warn);
    }
    .banner.show { display: block; }
    .list { display: flex; flex-direction: column; gap: 12px; }
    .card {
      background: var(--card); border: 1px solid var(--border); border-radius: 20px; padding: 14px;
      backdrop-filter: blur(10px);
    }
    .card.hot {
      border-color: rgba(255, 158, 207, 0.65);
      box-shadow: 0 0 28px rgba(255, 158, 207, 0.18);
      background: linear-gradient(145deg, rgba(255, 182, 220, 0.16), rgba(167, 139, 250, 0.1));
    }
    .card-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .sym { font-family: Quicksand, sans-serif; font-weight: 700; font-size: 1.12rem; }
    .score { margin-left: auto; font-weight: 800; font-size: 1.2rem; color: var(--accent2); }
    .card.hot .score { color: #ffd6ec; }
    .badge { font-size: 0.68rem; font-weight: 800; letter-spacing: .04em; padding: 3px 9px; border-radius: 999px; }
    .badge-long { background: rgba(184, 245, 212, 0.15); color: var(--long); border: 1px solid rgba(184, 245, 212, 0.45); }
    .badge-short { background: rgba(255, 158, 184, 0.18); color: var(--short); border: 1px solid rgba(255, 158, 184, 0.45); }
    .badge-hot { background: rgba(255, 158, 207, 0.22); color: #ffd6ec; border: 1px solid rgba(255, 158, 207, 0.5); }
    .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 12px; margin-top: 12px; }
    .grid label { display: block; font-size: 0.62rem; color: var(--muted); text-transform: uppercase; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.88rem; }
    .stop { color: var(--short); }
    .tp { color: var(--long); }
    .why { margin: 12px 0 0; color: var(--muted); font-size: 0.8rem; line-height: 1.4; }
    .empty {
      text-align: center; color: var(--muted); padding: 36px 16px;
      border: 1px dashed var(--border); border-radius: 20px;
    }
    .hint { margin-top: 18px; color: var(--muted); font-size: 0.75rem; text-align: center; line-height: 1.45; }
    .hint a { color: var(--accent); text-decoration: none; }
    .toast {
      position: fixed; left: 14px; right: 14px; bottom: calc(18px + var(--safe-bot));
      background: #2a1634; border: 1px solid var(--accent); color: var(--text);
      border-radius: 16px; padding: 12px 14px; font-size: 0.85rem; display: none; z-index: 20;
      box-shadow: 0 10px 30px rgba(255, 120, 180, 0.25);
    }
    .toast.show { display: block; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Выгодные позиции</h1>
      <div class="sub" id="subtitle">Скан Forecast</div>
    </div>
    <button class="notify-btn" id="notifyBtn" type="button">Уведомления</button>
  </header>

  <div class="stats">
    <div class="stat"><span>Score &gt; порога</span><strong id="hotCount">—</strong></div>
    <div class="stat"><span>Всего сетапов</span><strong id="allCount">—</strong></div>
    <div class="stat"><span>Обновлено</span><strong id="updatedAt">—</strong></div>
  </div>

  <div class="filters">
    <button class="chip active" data-filter="hot" type="button">Выгодные</button>
    <button class="chip" data-filter="all" type="button">Все</button>
  </div>
  <div class="banner" id="httpsNote">Системные уведомления работают по HTTPS (или localhost). На HTTP список всё равно обновляется.</div>
  <div id="list" class="list"></div>
  <p class="hint">
    Порог уведомления: score &gt; <span id="thrLabel"></span>.
    На iPhone: Поделиться → На экран «Домой».
    <a href="/scanner?desktop=1">Полная панель</a>
  </p>
  <div class="toast" id="toast"></div>

<script>
const ALERT_MIN = %%THRESHOLD%%;
const POLL_MS = 20000;
let filter = "hot";
let lastNotifiedKey = localStorage.getItem("forecastNotifiedKey") || "";
let lastUpdatedAt = "";

function fmtNum(v, digits) {
  const x = Number(v);
  if (v == null || Number.isNaN(x)) return "—";
  if (Math.abs(x) >= 1000) return x.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  if (Math.abs(x) >= 1) return x.toFixed(digits);
  return x.toPrecision(4);
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2800);
}
function dirBadge(d) {
  const k = (d || "").toLowerCase();
  if (k === "long") return '<span class="badge badge-long">LONG</span>';
  if (k === "short") return '<span class="badge badge-short">SHORT</span>';
  return '<span class="badge">' + (d || "—") + "</span>";
}
function cardHtml(s) {
  const hot = s.hot ? " hot" : "";
  const pill = s.hot ? '<span class="badge badge-hot">ВЫГОДНО</span>' : "";
  return `<article class="card${hot}">
    <div class="card-top">
      <div class="sym">${s.symbol || "—"}</div>
      ${dirBadge(s.direction)}
      ${pill}
      <div class="score">${fmtNum(s.score, 1)}</div>
    </div>
    <div class="grid">
      <div><label>Паттерн</label><div>${s.pattern || "—"}</div></div>
      <div><label>R:R</label><div>${fmtNum(s.risk_reward, 2)}</div></div>
      <div><label>Вход</label><div class="mono">${fmtNum(s.entry, 4)}</div></div>
      <div><label>Стоп</label><div class="mono stop">${fmtNum(s.stop, 4)}</div></div>
      <div><label>TP1</label><div class="mono tp">${fmtNum(s.target_1, 4)}</div></div>
      <div><label>TP2</label><div class="mono tp">${fmtNum(s.target_2, 4)}</div></div>
    </div>
    ${s.why_selected ? `<p class="why">${s.why_selected}</p>` : ""}
  </article>`;
}
function render(data) {
  const setups = data.setups || [];
  const hot = setups.filter((s) => s.hot);
  document.getElementById("hotCount").textContent = String(hot.length);
  document.getElementById("allCount").textContent = String(setups.length);
  document.getElementById("updatedAt").textContent = fmtTime(data.updated_at);
  document.getElementById("subtitle").textContent =
    (data.timeframe || "—") + " · порог " + ALERT_MIN;
  const shown = filter === "hot" ? hot : setups;
  const list = document.getElementById("list");
  if (!shown.length) {
    list.innerHTML = filter === "hot"
      ? '<div class="empty">Пока нет позиций со score выше ' + ALERT_MIN + "</div>"
      : '<div class="empty">В последнем скане нет сетапов</div>';
    return;
  }
  list.innerHTML = shown.map(cardHtml).join("");
}
function notifyKey(data) {
  const hot = (data.setups || []).filter((s) => s.hot);
  return (data.updated_at || "") + "|" + hot.map((s) => s.symbol + ":" + s.score).join(",");
}
async function maybeNotify(data) {
  const hot = (data.setups || []).filter((s) => s.hot);
  const key = notifyKey(data);
  if (!hot.length || key === lastNotifiedKey) return;
  const isNewScan = lastUpdatedAt && data.updated_at && data.updated_at !== lastUpdatedAt;
  lastNotifiedKey = key;
  localStorage.setItem("forecastNotifiedKey", key);
  if (!isNewScan) return;
  const title = hot.length === 1 ? "Выгодная позиция" : hot.length + " выгодные позиции";
  const body = hot.slice(0, 3).map((s) => s.symbol + " " + (s.direction || "") + " · " + s.score).join(" · ");
  toast(title + ": " + body);
  try { navigator.vibrate && navigator.vibrate([80, 40, 120]); } catch (e) {}
  if (Notification.permission !== "granted") return;
  try {
    const reg = await navigator.serviceWorker.ready;
    await reg.showNotification(title, {
      body,
      icon: "/m/icon-192.png",
      badge: "/m/icon-192.png",
      tag: "forecast-hot",
      renotify: true,
      data: { url: "/scanner?mobile=1" },
    });
  } catch (e) {}
}
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
function notifyEnabled() {
  return localStorage.getItem("forecastNotifyOn") === "1" && Notification.permission === "granted";
}
function paintNotifyBtn() {
  const btn = document.getElementById("notifyBtn");
  const on = notifyEnabled();
  btn.classList.toggle("on", on);
  btn.textContent = on ? "Уведомления вкл" : "Уведомления";
}
async function enableNotifications() {
  if (!("Notification" in window)) {
    toast("Браузер не поддерживает уведомления");
    return;
  }
  const perm = await Notification.requestPermission();
  if (perm !== "granted") {
    toast("Разрешите уведомления в настройках телефона");
    return;
  }
  localStorage.setItem("forecastNotifyOn", "1");
  paintNotifyBtn();
  try {
    const vapidRes = await fetch("/m/api/vapid", { credentials: "same-origin" });
    const vapid = await vapidRes.json();
    if (vapid.publicKey && navigator.serviceWorker) {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapid.publicKey),
      });
      await fetch("/m/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(sub),
      });
    }
  } catch (e) {}
  toast("Буду писать, если score выше " + ALERT_MIN);
}
function fromScannerJson(raw) {
  if (raw && Array.isArray(raw.setups)) return raw;
  const setups = (raw.top_setups || []).map((row) => {
    const plan = row.setup || {};
    const score = Number(row.score || 0);
    let direction = String(plan.direction || "");
    if (direction.toLowerCase() === "long") direction = "Long";
    if (direction.toLowerCase() === "short") direction = "Short";
    return {
      symbol: row.symbol,
      score: Math.round(score * 10) / 10,
      direction,
      pattern: row.pattern,
      trend: row.trend || plan.trend,
      probability_pct: plan.probability_pct,
      risk_reward: plan.risk_reward,
      entry: plan.entry,
      stop: plan.stop,
      target_1: plan.target_1,
      target_2: plan.target_2,
      why_selected: row.why_selected,
      hot: score > ALERT_MIN,
    };
  }).sort((a, b) => b.score - a.score);
  return {
    updated_at: raw.updated_at,
    timeframe: raw.timeframe,
    candidates_found: raw.candidates_found || 0,
    setups,
  };
}
async function loadSetups() {
  const r = await fetch("/scanner/json", { credentials: "same-origin" });
  if (!r.ok) throw new Error("load");
  const data = fromScannerJson(await r.json());
  render(data);
  await maybeNotify(data);
  lastUpdatedAt = data.updated_at || lastUpdatedAt;
}
document.querySelectorAll(".chip").forEach((btn) => {
  btn.addEventListener("click", () => {
    filter = btn.dataset.filter;
    document.querySelectorAll(".chip").forEach((b) => b.classList.toggle("active", b === btn));
    loadSetups().catch(() => {});
  });
});
document.getElementById("notifyBtn").addEventListener("click", enableNotifications);
document.getElementById("thrLabel").textContent = String(ALERT_MIN);
if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
  document.getElementById("httpsNote").classList.add("show");
}
paintNotifyBtn();
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}
loadSetups().catch(() => {
  document.getElementById("list").innerHTML = '<div class="empty">Не удалось загрузить скан</div>';
});
setInterval(() => loadSetups().catch(() => {}), POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadSetups().catch(() => {});
});
</script>
</body>
</html>
"""
