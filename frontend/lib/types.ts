export interface CandidateRow {
  symbol: string; name: string; exchange: string;
  score: number; buy: boolean; early?: boolean;
  price: number | null; price_indicative?: boolean; gap_pct: number | null;
  rvol: number | null; rvol_confidence: number | null;
  pm_volume: number | null; pm_dollar_volume: number | null;
  float_shares: number | null; float_rotation?: number | null;
  shares_outstanding: number | null;
  market_cap: number | null; spread_pct: number | null;
  vwap: number | null; above_vwap: boolean | null;
  catalyst_type: string; catalyst_direction: string; catalyst_summary: string;
  filing_forms: string[]; filing_links?: { form: string; url: string }[];
  catalyst_sources?: { news: number; filings: number };
  hard_blocks: string[]; gates_failed: string[];
  gates: Record<string, boolean>; components: Record<string, number>;
  gate_reasons?: string[];
  explain?: { key: string; label: string; pass: boolean; actual: string | number | null; required: string }[];
  penalties: { type: string; points: number }[];
  quote_fresh: boolean | null; provider_ts: string | null;
  sector: string; ts: string;
  rvol_estimated?: boolean;
}

export interface SignalRow {
  signal_uid: string; symbol: string; session_date: string;
  strategy_version: string; initiated_at: string;
  buy_price: number; price_source: string;
  current: number | null; current_ts: string | null;
  day_high: number | null; day_low: number | null;
  since_high: number | null; since_low: number | null;
  status: string; is_demo: boolean; signal_type?: string; score: number | null;
  catalyst_type: string;
  checkpoints: Record<string, { price: number; pct: number }>;
  change_abs: number | null; change_pct: number | null;
  max_gain_pct: number | null; max_drawdown_pct: number | null;
}

export interface StatusPayload {
  et_time: string; phase: string;
  scanner: { phase: string; last_cycle_at: string | null; last_cycle_ok: boolean | null;
             next_run_at: string | null; cycles: number; last_error: string;
             candidates: number; paused?: boolean };
  next_scan_start: string; active_signals: number; api_calls_24h: number;
  ai_usage_month: { calls: number; est_cost_usd: number };
  strategy_version: string; paper_mode: boolean;
  api_calls_per_min?: number; api_throttles_1h?: number;
}

export interface AppSettings {
  price_min: number; price_max: number | null;
  market_cap_min: number | null; market_cap_max: number | null;
  float_min: number | null; float_max: number | null;
  shares_outstanding_min: number | null; shares_outstanding_max: number | null;
  min_pm_volume: number; min_pm_dollar_volume: number;
  max_spread_pct: number; preferred_spread_pct: number;
  min_rvol_for_buy: number; min_score_for_buy: number;
  min_catalyst_confidence: number; max_extension_from_pm_high_pct: number;
  quote_freshness_sec: number; scan_interval_sec: number; enrich_top_n: number;
  universe_sweep_per_cycle: number;
  reentry_cooldown_min: number; include_otc: boolean; momentum_only_mode: boolean;
  allow_estimated_rvol: boolean; est_rvol_buy_multiplier: number;
  buy_confirm_after_et: string;
  openai_monthly_budget_usd: number; paused: boolean;
}
