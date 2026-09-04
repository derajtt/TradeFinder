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
export type ExplainItem = NonNullable<CandidateRow['explain']>[number];

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
  outcome?: string; post7_high?: number | null; post7_low?: number | null;
  stop?: number | null; target1?: number | null; target2?: number | null;
  /** lifecycle state (DISCOVERED | EARLY_WATCH | QUALIFIED_WATCH | ACTIONABLE_BUY | …) */
  lifecycle?: string; profile?: string;
  /** present only when the endpoint deduped rows */
  record_count?: number; best_lifecycle?: string;
  name?: string;
}

export interface Outcomes {
  win: number; neutral: number; loss: number; pending: number;
  win_rate: number | null; tracked?: number;
}

export interface StatusPayload {
  et_time: string; phase: string;
  outcomes?: Outcomes;
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
  /** outcome judged on the first N minutes of tradability */
  early_window_min: number;
  /** up this much inside the window = WIN */
  early_win_gain_pct: number;
}

export interface SettingsPayload {
  settings: AppSettings; defaults: AppSettings;
  env_status: Record<string, boolean>; strategy_version: string;
}

/* ── /api/ops ─────────────────────────────────────────────────────────────── */
export interface Ops {
  now_et: string; phase: string; regime_text?: string; quiet_reason?: string | null;
  lanes: { lane: string; state: string; detail: string }[];
  upcoming: { event: string; at_et: string }[];
  not_running: { what: string; why: string }[];
}

/* ── /api/report/canonical ────────────────────────────────────────────────── */
export interface Canonical {
  generated_at: string; profile: string;
  versions: Record<string, string> & { strategy_version?: string; filter_version?: string };
  lifecycle_counts: Record<string, number>;
  totals: {
    signals_total_records: number; actionable_buys: number; early_watches: number;
    qualified_watches: number; invalidated: number; rejected_candidates: number;
  } & Record<string, number>;
  actionable_buy_performance: {
    cohort: string; note: string; open_positions: number; closed_trades: number;
    wins: number; losses: number; win_rate: number | null; win_rate_lb: number | null;
    avg_r: number | null; calibration: string;
  };
  watch_outcomes_info_only?: { win: number; loss: number; neutral: number; pending: number; note: string };
  shadow_exit_policies?: Record<string, { n: number; wins: number; avg_r: number | null; win_rate: number | null }>;
  reconciliation: { sum_lifecycles: number; equals_total: boolean };
}

/* ── /api/outcomes/noon ───────────────────────────────────────────────────── */
export interface Noon {
  policy: string; counts: Record<string, number>;
  call_win_rate: number | null; win_rate_lb: number | null; denominator: number;
  note: string;
  rows: { symbol: string; class: string; call_price: number; reference: number | null; quality: string }[];
}

/* ── /api/digest, /api/brief ──────────────────────────────────────────────── */
export interface Digest {
  line: string; regime_text: string;
  today?: { buys?: number; early?: number; watches?: number } & Record<string, number | undefined>;
  [k: string]: unknown;
}
export interface Brief {
  available: boolean; session_date?: string;
  content?: { headline?: string; top_rejection_reasons?: [string, number][]; [k: string]: unknown } | null;
}

/* ── /api/competition, /api/models ────────────────────────────────────────── */
export interface CompetitionCard {
  model_id: string; name: string; color: string; experimental: boolean;
  season: number; equity: number; cash: number; return_pct: number; realized_pnl: number;
  max_drawdown_pct: number; trades: number; wins: number; win_rate: number | null;
  spark?: number[]; status?: string; idle_reason?: string | null; last_marked_at?: string | null;
  symbols_scanned?: number; has_traded?: boolean;
}
export interface Competition { cards: CompetitionCard[]; leaderboards: Record<string, CompetitionCard[]>; note: string }

export interface ModelAccount {
  cash: number; equity: number; realized_pnl: number; return_pct: number;
  max_drawdown_pct: number; trades_closed: number; wins: number;
}
export interface ModelInfo {
  id: string; name: string; color: string; edge: string;
  universe: string; horizon: string; cadence: string; asset_classes: string[];
  experimental?: boolean; data_notes?: string; hypothesis?: string; enabled: boolean;
  custom?: boolean; requires?: string[]; engine?: string; ledger_profile?: string;
  account: ModelAccount;
  signals: Record<string, number>;
}
export interface Regime { state: string; why?: string; [k: string]: unknown }
export interface ModelsPayload {
  models: ModelInfo[]; regime: Regime | null;
  research_only: { id: string; name: string; why_not: string }[];
  universes?: Record<string, string[]>;
}

/* ── /api/profiles ────────────────────────────────────────────────────────── */
export interface ProfileCfg { name: string; enabled: boolean; color: string; description: string; overrides?: Record<string, number | string> }
export interface ProfilesPayload { profiles: Record<string, ProfileCfg> }

/* ── /api/positions ───────────────────────────────────────────────────────── */
export interface Position {
  symbol: string; status: string; opened_at: string; entry_fill: number;
  stop: number | null; target1: number | null; target2: number | null;
  remaining_frac: number; realized_r: number; exit_reason: string;
  closed_at: string | null; strategy_version: string; size_usd?: number;
}

/* ── /api/rejected ────────────────────────────────────────────────────────── */
export interface Rejected {
  symbol: string; session_date: string; rejected_at: string; reason: string;
  failed_gates: string[]; price: number | null; score: number | null;
  shadow_high: number | null; shadow_low: number | null;
  missed_move_pct: number | null; lifecycle?: string;
}

/* ── /api/candidates radar ────────────────────────────────────────────────── */
export interface RadarRow {
  symbol: string; name: string; exchange: string;
  price: number | null; gap_pct: number | null; volume: number | null;
  market_cap: number | null; has_news: boolean; provider_ts: string | null;
}

/* ── /api/feed ────────────────────────────────────────────────────────────── */
export interface FeedRow {
  ts: string; kind: string; form: string | null; items: string | null; symbol: string;
  title: string; url: string; source: string; accession?: string | null;
}

/* ── /api/alerts, /api/watchlists, /api/journal ───────────────────────────── */
export interface AlertRow {
  id: number; symbol: string; condition: string; price: number; active: boolean;
  fired_at: string | null; fired_price: number | null; note?: string;
}
export interface WatchlistRow { id: number; name: string; symbols: string[] }
export interface JournalEntry {
  id: number; created_at: string; symbol: string; signal_uid: string;
  note: string; tags: string[]; rules_followed: boolean; review: string;
}

/* ── /api/accuracy ────────────────────────────────────────────────────────── */
export interface AccuracyRow {
  id: string; name: string; color: string; version: string | null;
  paper_trades: number; backtest_win_rate: number | null; oos_win_rate: number | null;
  oos_sample?: number | null; oos_expectancy_r?: number | null;
  paper_win_rate: number | null; expectancy_r: number | null; profit_factor: number | null;
  max_drawdown_pct: number | null; avg_r: number | null; sample: string; risk_model: string;
  equity?: number | null;
}
export interface AccuracyPayload { rows: AccuracyRow[]; sortable: string[]; note: string }

/* ── /api/performance ─────────────────────────────────────────────────────── */
export interface PerfGroup {
  n: number; win_rate: number | null; avg_change_pct: number | null;
  avg_max_gain_pct: number | null; avg_max_drawdown_pct: number | null;
  win?: number; neutral?: number; loss?: number;
}
export interface PerfPayload {
  total_signals: number;
  groups: Record<string, Record<string, PerfGroup>>;
  outcomes?: Outcomes & { by_type?: Record<string, Record<string, number>> };
}

/* ── /api/health/detail, /api/health/strategies ───────────────────────────── */
export interface HealthDetail {
  env_status: Record<string, boolean>;
  backup?: { status: string; latest?: string; age_hours?: number; size_mb?: number; count?: number; note?: string };
  entitlements: Record<string, { ok: boolean; status: number }>;
  endpoints: { provider: string; endpoint: string; calls: number; ok: number;
    last_status: number; last_ts: string; avg_latency_ms: number; last_count: number }[];
  events: { ts: string; level: string; component: string; message: string }[];
  runs: { id: number; started: string; finished: string | null; phase: string; status: string;
    universe: number; shortlisted: number; enriched: number; api_calls: number; error: string }[];
  scheduler: { phase: string; cycles: number; last_error: string } | null;
}
export interface StrategyHealthRow {
  id: string; name: string; engine: string; cadence: string | null; color: string | null;
  asset_classes: string[] | null; risk_model: string; own_worker: boolean; status: string;
  last_scan_at: string | null; last_seen_at: string | null;
  symbols_scanned: number; symbols_with_data: number; signals_today: number; errors: number;
  skip_reason: string | null; universe?: string | null; equity: number | null; trades_closed: number;
  insider_cache?: { clusters: number; head: string[]; age_s: number | null };
}
export interface StrategyHealth {
  strategies: StrategyHealthRow[];
  counts: Record<string, number>;
  scheduler: { phase: string | null; cycles: number | null; last_cycle_at: string | null;
    last_cycle_ok: boolean; last_error: string | null; next_run_at: string | null };
  regime: Regime | null;
  stale_after_seconds: number;
  legend: Record<string, string>;
}
