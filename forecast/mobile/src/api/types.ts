export type FeedKind = "scan" | "swing" | "stocks";

export type Setup = {
  symbol?: string;
  score: number;
  direction?: string;
  pattern?: string;
  trend?: string;
  probability_pct?: number;
  probability_is_heuristic?: boolean;
  risk_reward?: number;
  entry?: number;
  stop?: number;
  target_1?: number;
  target_2?: number;
  why_selected?: string;
  regime?: string;
  stock_name?: string;
  ticker?: string;
  hot: boolean;
};

export type SetupsResponse = {
  updated_at?: string | null;
  timeframe?: string;
  candidates_found?: number;
  symbols_scanned?: number;
  alert_min_score?: number;
  hot_count?: number;
  setups: Setup[];
};

export type PaperTrade = {
  id?: string;
  symbol?: string;
  side?: string;
  status: "open" | "closed" | string;
  win?: boolean | null;
  r?: number | null;
  entry?: number;
  last_price?: number;
  stop?: number;
  tp?: number;
  score?: number;
  reason?: string;
};

export type PaperSummary = {
  updated_at?: string | null;
  min_score?: number;
  open?: number;
  closed?: number;
  win_rate_pct?: number;
  total_r?: number;
  pnl_usdt?: number;
  unrealized_r?: number;
};

export type PaperResponse = {
  summary: PaperSummary;
  trades: PaperTrade[];
};

export type ServerSettings = {
  baseUrl: string;
  username: string;
  password: string;
  notifyEnabled: boolean;
};
