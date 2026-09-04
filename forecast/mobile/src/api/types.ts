export type Setup = {
  symbol?: string;
  score: number;
  direction?: string;
  pattern?: string;
  trend?: string;
  probability_pct?: number;
  risk_reward?: number;
  entry?: number;
  stop?: number;
  target_1?: number;
  target_2?: number;
  why_selected?: string;
  regime?: string;
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

export type ServerSettings = {
  baseUrl: string;
  username: string;
  password: string;
  notifyEnabled: boolean;
};
