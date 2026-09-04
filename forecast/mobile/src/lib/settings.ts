import * as SecureStore from "expo-secure-store";

import { encode as base64Encode } from "base-64";

import type { ServerSettings } from "../api/types";

const KEY = "forecast.settings";

const DEFAULTS: ServerSettings = {
  baseUrl: "http://127.0.0.1:8000",
  username: "admin",
  password: "",
  notifyEnabled: false,
};

export async function loadSettings(): Promise<ServerSettings> {
  try {
    const raw = await SecureStore.getItemAsync(KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<ServerSettings>;
    return {
      baseUrl: (parsed.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, ""),
      username: parsed.username || DEFAULTS.username,
      password: parsed.password || "",
      notifyEnabled: Boolean(parsed.notifyEnabled),
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export async function saveSettings(settings: ServerSettings): Promise<void> {
  const payload: ServerSettings = {
    baseUrl: settings.baseUrl.replace(/\/+$/, ""),
    username: settings.username.trim(),
    password: settings.password,
    notifyEnabled: settings.notifyEnabled,
  };
  await SecureStore.setItemAsync(KEY, JSON.stringify(payload));
}

export function authHeader(username: string, password: string): string {
  return `Basic ${base64Encode(`${username}:${password}`)}`;
}
