export const colors = {
  bg: "#140a18",
  bg2: "#1f0f28",
  card: "rgba(42, 22, 52, 0.92)",
  border: "rgba(255, 182, 220, 0.28)",
  text: "#fff0f8",
  muted: "#c9a8d4",
  accent: "#ff9ecf",
  accent2: "#e9b8ff",
  long: "#b8f5d4",
  short: "#ff9eb8",
  warn: "#ffd89a",
};

export function fmtNum(value: number | undefined, digits = 4): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1000) return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  if (Math.abs(value) >= 1) return value.toFixed(digits);
  return value.toPrecision(4);
}

export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
