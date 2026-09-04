import type { SetupsResponse, Setup } from "../api/types";
import { authHeader } from "../lib/settings";

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
    risk_reward: plan.risk_reward as number | undefined,
    entry: plan.entry as number | undefined,
    stop: plan.stop as number | undefined,
    target_1: plan.target_1 as number | undefined,
    target_2: plan.target_2 as number | undefined,
    why_selected: row.why_selected as string | undefined,
    regime: row.regime as string | undefined,
    hot: score > threshold,
  };
}

function fromScannerJson(raw: Record<string, unknown>, threshold: number): SetupsResponse {
  if (Array.isArray(raw.setups)) {
    return raw as unknown as SetupsResponse;
  }
  const setups = ((raw.top_setups as Record<string, unknown>[]) || [])
    .map((row) => normalizeSetup(row, threshold))
    .sort((a, b) => b.score - a.score);
  return {
    updated_at: raw.updated_at as string | undefined,
    timeframe: raw.timeframe as string | undefined,
    candidates_found: Number(raw.candidates_found ?? 0),
    symbols_scanned: Number(raw.symbols_scanned ?? raw.universe_size ?? 0),
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

export async function fetchSetups(
  baseUrl: string,
  username: string,
  password: string,
): Promise<SetupsResponse> {
  const mobile = await requestJson<SetupsResponse>(
    `${baseUrl}/m/api/setups`,
    username,
    password,
  );
  if (mobile.setups?.length) return mobile;

  const scanner = await requestJson<Record<string, unknown>>(
    `${baseUrl}/scanner/json`,
    username,
    password,
  );
  const threshold = Number(scanner.alert_min_score ?? mobile.alert_min_score ?? 35);
  return fromScannerJson(scanner, threshold);
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
