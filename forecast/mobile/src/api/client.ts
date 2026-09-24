import type { FeedKind, PaperResponse, PaperTrade, SetupsResponse, Setup } from "./types";
import { authHeader } from "../lib/settings";

const FEED_PATH: Record<FeedKind, { compact: string; fallback: string }> = {
  scan: { compact: "/m/api/setups", fallback: "/scanner/json" },
  swing: { compact: "/m/api/swing", fallback: "/swing/json" },
  stocks: { compact: "/m/api/stocks", fallback: "/stocks/json" },
};

function normalizeSetup(row: Record<string, unknown>, threshold: number): Setup {
  const plan = (row.setup as Record<string, unknown> | undefined) || {};
  const score = Number(row.score ?? 0);
  let direction = String(plan.direction ?? row.direction ?? "");
  if (direction.toLowerCase() === "long") direction = "Long";
  if (direction.toLowerCase() === "short") direction = "Short";
  return {
    symbol: row.symbol as string | undefined,
    score: Math.round(score * 10) / 10,
    direction,
    pattern: row.pattern as string | undefined,
    trend: (row.trend ?? plan.trend) as string | undefined,
    probability_pct: plan.probability_pct as number | undefined,
    probability_is_heuristic: plan.probability_is_heuristic as boolean | undefined,
    risk_reward: plan.risk_reward as number | undefined,
    entry: plan.entry as number | undefined,
    stop: plan.stop as number | undefined,
    target_1: plan.target_1 as number | undefined,
    target_2: plan.target_2 as number | undefined,
    why_selected: row.why_selected as string | undefined,
    regime: row.regime as string | undefined,
    stock_name: row.stock_name as string | undefined,
    ticker: row.ticker as string | undefined,
    hot: score > threshold,
  };
}

function fromScannerJson(raw: Record<string, unknown>, threshold: number): SetupsResponse {
  if (Array.isArray(raw.setups)) {
    return raw as unknown as SetupsResponse;
  }
  const report = (raw.report as Record<string, unknown> | undefined) || raw;
  const cfg = (raw.scan_config as Record<string, unknown> | undefined) || {};
  const setups = ((report.top_setups as Record<string, unknown>[]) || [])
    .map((row) => normalizeSetup(row, threshold))
    .sort((a, b) => b.score - a.score);
  return {
    updated_at: (raw.updated_at ?? report.updated_at) as string | undefined,
    timeframe: (report.timeframe ?? cfg.timeframe) as string | undefined,
    candidates_found: Number(report.candidates_found ?? 0),
    symbols_scanned: Number(raw.universe_count ?? report.symbols_scanned ?? raw.universe_size ?? 0),
    alert_min_score: threshold,
    hot_count: setups.filter((s) => s.hot).length,
    setups,
  };
}

async function requestJson<T>(
  url: string,
  username: string,
  password: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      Authorization: authHeader(username, password),
      ...(init?.headers || {}),
    },
  });
  if (response.status === 401) {
    throw new Error("Неверный логин или пароль");
  }
  if (!response.ok) {
    throw new Error(`Сервер ответил ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchFeed(
  kind: FeedKind,
  baseUrl: string,
  username: string,
  password: string,
): Promise<SetupsResponse> {
  const path = FEED_PATH[kind];
  try {
    const compact = await requestJson<SetupsResponse>(
      `${baseUrl}${path.compact}`,
      username,
      password,
    );
    if (compact.setups) return compact;
  } catch {
    // старый сервер без /m/api/swing|stocks — берём панельный JSON
  }
  const raw = await requestJson<Record<string, unknown>>(
    `${baseUrl}${path.fallback}`,
    username,
    password,
  );
  const threshold = Number(raw.alert_min_score ?? 35);
  return fromScannerJson(raw, threshold);
}

export async function fetchSetups(
  baseUrl: string,
  username: string,
  password: string,
): Promise<SetupsResponse> {
  return fetchFeed("scan", baseUrl, username, password);
}

function compactPaper(raw: PaperResponse): PaperResponse {
  const reasonMap: Record<string, string> = { tp: "TP", stop: "стоп", time: "таймаут" };
  const trades = (raw.trades || []).map((row) => {
    const t = row as PaperTrade & {
      unrealized_r?: number;
      r_multiple?: number;
      direction?: string;
      exit_reason?: string;
      close_reason?: string;
      target_1?: number;
    };
    if (t.r != null && (t.side === "Long" || t.side === "Short")) return t;
    const open = t.status === "open";
    let side = String(t.side || t.direction || "");
    if (side.toLowerCase() === "long") side = "Long";
    if (side.toLowerCase() === "short") side = "Short";
    const reason = String(t.exit_reason || t.close_reason || t.reason || "");
    return {
      ...t,
      side,
      r: open ? t.unrealized_r : t.r_multiple,
      tp: t.tp ?? t.target_1,
      reason: open ? "" : reasonMap[reason] || reason,
    };
  });
  const openTrades = trades.filter((t) => t.status === "open");
  const closedTrades = trades.filter((t) => t.status !== "open");
  return { summary: raw.summary, trades: [...openTrades, ...closedTrades.slice(0, 20)] };
}

export async function fetchPaper(
  baseUrl: string,
  username: string,
  password: string,
): Promise<PaperResponse> {
  try {
    const compact = await requestJson<PaperResponse>(`${baseUrl}/m/api/paper`, username, password);
    if (compact.trades) return compactPaper(compact);
  } catch {
    // старый сервер без /m/api/paper
  }
  const fallback = await requestJson<PaperResponse>(`${baseUrl}/paper/json`, username, password);
  return compactPaper(fallback);
}

export async function registerExpoToken(
  baseUrl: string,
  username: string,
  password: string,
  token: string,
  platform: string,
): Promise<void> {
  await requestJson(`${baseUrl}/m/api/expo/register`, username, password, {
    method: "POST",
    body: JSON.stringify({ token, platform }),
  });
}

export async function unregisterExpoToken(
  baseUrl: string,
  username: string,
  password: string,
  token: string,
): Promise<void> {
  await requestJson(`${baseUrl}/m/api/expo/unregister`, username, password, {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function testConnection(
  baseUrl: string,
  username: string,
  password: string,
): Promise<void> {
  await fetchSetups(baseUrl, username, password);
}
