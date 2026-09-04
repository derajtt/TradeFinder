/** The single mapping from backend enums / keys to plain English (spec §4.12).
 *  Simple mode shows only these labels; the raw enum is Advanced-only.
 *  Unknown values are never swallowed — callers use `pillFor`, which renders
 *  "Unknown" with the raw string in <code>. */
import type { CandidateRow } from './types';

export type Tone = 'buy' | 'risk' | 'warn' | 'early' | 'accent' | 'neutral' | 'backtest';
export interface Label { label: string; tone: Tone }
const L = (label: string, tone: Tone = 'neutral'): Label => ({ label, tone });

export const PHASE: Record<string, Label> = {
  premarket: L('Premarket', 'early'),
  regular: L('Market open', 'buy'),
  open: L('Market open', 'buy'),
  afterhours: L('After hours'),
  closed: L('Market closed'),
  prep: L('Getting ready'),
};

export const SIGNAL_STATUS: Record<string, Label> = {
  active: L('Open', 'buy'), closed: L('Closed'), invalidated: L('Dropped'),
};

export const OUTCOME: Record<string, Label> = {
  win: L('Popped', 'buy'), loss: L("Didn't", 'risk'), neutral: L('Flat'), pending: L('Pending'),
};

export const LIFECYCLE: Record<string, Label> = {
  DISCOVERED: L('Found'), EARLY_WATCH: L('Early watch', 'early'),
  QUALIFIED_WATCH: L('Watching', 'early'), ACTIONABLE_BUY: L('Buy pick', 'buy'),
  REJECTED: L('Blocked', 'risk'), INVALIDATED: L('Dropped'), EXPIRED: L('Expired'),
  CLOSED: L('Closed'), DATA_ERROR: L('Data error', 'risk'),
};

export const MODEL_STATUS: Record<string, Label> = {
  LIVE: L('Paper trading', 'accent'),
  'PAPER LIVE': L('Paper trading', 'accent'),
  PAPER_LIVE: L('Paper trading', 'accent'),
  WAITING: L('Waiting for a setup'),
  NO_DATA: L('No data', 'warn'),
  OFFLINE: L('Offline', 'risk'),
  ERROR: L('Error', 'risk'),
  DISABLED: L('Off'),
  UNKNOWN: L('Unknown'),
};

/** MODEL_STATUS with the two context rules: "Live" only when real money, and
 *  WAITING with trades > 0 reads "Between scans". */
export function modelStatus(raw: string | null | undefined,
                            opts: { paperMode?: boolean; trades?: number } = {}): Label {
  const key = (raw ?? '').toUpperCase();
  if (key === 'LIVE' && opts.paperMode === false) return L('Live', 'buy');
  if (key === 'WAITING' && (opts.trades ?? 0) > 0) return L('Between scans');
  return MODEL_STATUS[key] ?? L('Unknown');
}

export const LANE_STATE: Record<string, Label> = {
  RUNNING: L('Running', 'buy'), OPEN: L('Market open', 'buy'),
  'RUNNING 24/7': L('Running around the clock', 'buy'),
  'DONE TODAY': L('Done for today'), SCHEDULED: L('Scheduled'),
  'DAILY MODELS ONLY': L('Daily models only'), 'IDLE (no session)': L('Idle until market'),
  CLOSED: L('Market closed'), HIGH_RISK: L('High risk', 'risk'),
  // the regime-controller lane reports the regime state upper-cased
  TREND: L('Trending', 'buy'), RANGE: L('Range-bound', 'accent'),
  EVENT: L('Event-driven', 'warn'), UNCERTAIN: L('Uncertain', 'warn'),
};

export const REGIME: Record<string, Label> = {
  trend: L('Trending', 'buy'), range: L('Range-bound', 'accent'), event: L('Event-driven', 'warn'),
  high_risk: L('High risk', 'risk'), uncertain: L('Uncertain', 'warn'),
};

export const NOON_CLASS: Record<string, Label> = {
  WIN_10_TOUCH: L('+10% touch', 'buy'), WIN_NOON_GREEN: L('green at noon', 'buy'),
  LOSS_NOON_RED: L('red at noon', 'risk'), FLAT: L('flat'), INCOMPLETE: L('incomplete'),
};

/** Templates: {n} is the trade count. Use `sampleLabel` to fill it. */
export const SAMPLE_LABEL: Record<string, string> = {
  'INSUFFICIENT DATA': 'too few trades ({n} of 30 needed)',
  EARLY: 'early ({n} of 100)',
  'MODERATE SAMPLE': 'moderate sample',
};
export function sampleLabel(raw: string | null | undefined, n: number | null | undefined): string {
  const key = (raw ?? '').toUpperCase();
  const t = SAMPLE_LABEL[key];
  if (!t) return key ? humanKey(key.toLowerCase()).toLowerCase() : '—';
  return t.replace('{n}', String(n ?? 0));
}

/** explain[] keys, gate names and hard blocks → plain words. `*_gate` is stripped. */
export const GATE: Record<string, string> = {
  score: 'Score', rvol: 'Volume vs normal', pm_volume: 'Premarket volume',
  pm_dollar: 'Premarket $ volume', pm_dollar_volume: 'Premarket $ volume',
  volume: 'Volume', spread: 'Spread', fresh: 'Fresh price', freshness: 'Fresh price',
  stale_quote: 'Fresh price', catalyst: 'Verified news', confirm: 'Price confirmation',
  price_confirmation: 'Price confirmation', window: 'Broker premarket window',
  blocks: 'Hard blocks', no_hard_block: 'Hard blocks',
  spread_above_max: 'Spread too wide', incomplete_live_volume: 'Volume data incomplete',
  critical_data_disagreement: 'Data sources disagree',
  severe_actionable_dilution: 'Severe dilution', unresolved_halt: 'Trading halt',
  price_range: 'Price range', market_cap_range: 'Market cap range', float_range: 'Float range',
  shares_outstanding_range: 'Shares outstanding range', min_pm_volume: 'Premarket volume',
  min_pm_dollar_volume: 'Premarket $ volume',
};
export function gateLabel(k: string | null | undefined): string {
  if (!k) return '—';
  const key = String(k).replace(/_gate$/, '');
  return GATE[key] ?? humanKey(key);
}

export const BOARD: Record<string, Label> = {
  return: L('Highest return'), win_rate: L('Most often right'), drawdown: L('Smallest worst dip'),
};

export const POSITION_STATUS: Record<string, Label> = {
  open: L('Open', 'buy'), closed: L('Closed'),
};

/** 8-K item codes → what they mean. */
export const ITEM_CODES: Record<string, string> = {
  '1.01': 'New agreement', '1.02': 'Ended agreement', '1.03': 'Bankruptcy',
  '1.04': 'Mine safety', '1.05': 'Cybersecurity incident',
  '2.01': 'Asset deal', '2.02': 'Results of operations', '2.03': 'New debt',
  '2.04': 'Debt trigger', '2.05': 'Restructuring', '2.06': 'Impairment',
  '3.01': 'Listing notice', '3.02': 'Unregistered sale', '3.03': 'Holder rights change',
  '4.01': 'Auditor change', '4.02': 'Restatement', '5.01': 'Control change',
  '5.02': 'Executive/board change', '5.03': 'Bylaw change', '5.04': 'Benefit-plan trading blackout',
  '5.05': 'Code of ethics change', '5.06': 'Shell status change', '5.07': 'Shareholder vote',
  '5.08': 'Director nominations', '6.01': 'ABS informational', '6.02': 'Servicer change',
  '6.03': 'Credit enhancement change', '6.04': 'Missed distribution', '6.05': 'ABS updating disclosure',
  '7.01': 'Regulation FD disclosure', '8.01': 'Other event', '9.01': 'Exhibits',
};
/** "2.02,9.01" → ["Results of operations", "Exhibits"]; unknown codes pass through. */
export function itemCodes(items: string | null | undefined): string[] {
  if (!items) return [];
  return String(items).split(/[,\s]+/).filter(Boolean).map((c) => ITEM_CODES[c] ?? c);
}

/** Catalyst categories from the backend enum (app/strategy/catalyst.py). */
export const CATALYST: Record<string, string> = {
  fda_regulatory: 'FDA / regulatory', government_contract: 'Government contract',
  merger_acquisition: 'Merger or acquisition', commercial_agreement: 'Commercial agreement',
  clinical_result: 'Clinical result', earnings_surprise: 'Earnings surprise',
  legal_patent: 'Legal or patent', strategic_financing: 'Strategic financing',
  customer_order: 'Customer order', partnership: 'Partnership', guidance_raise: 'Raised guidance',
  product_launch: 'Product launch', insider_ownership: 'Insider buying',
  sec_filing_positive: 'Positive SEC filing', vague_pr: 'Vague press release',
  conference: 'Conference', corporate_update: 'Corporate update', recycled: 'Recycled news',
  rumor_promo: 'Rumor or promotion', non_binding_mou: 'Non-binding agreement',
  filing_housekeeping: 'Routine filing', compliance_notice: 'Compliance notice',
  reverse_split: 'Reverse split', dilution_negative: 'Dilution', other_negative: 'Negative news',
  none: 'No news found',
};
export function catalystLabel(t: string | null | undefined): string {
  if (!t || t === 'none' || t === 'unclassified') return 'No news found';
  return CATALYST[t] ?? titleCase(t);
}

export function titleCase(s: string): string {
  return String(s).replace(/[_-]+/g, ' ').trim().replace(/\b\w/g, (c) => c.toUpperCase());
}

export function phaseLabel(p: string | null | undefined): Label {
  if (!p) return L('Unknown');
  return PHASE[p] ?? L(titleCase(p));
}
export const phase = phaseLabel;

export function regime(state: string | null | undefined): Label {
  if (!state) return L('Unknown');
  return REGIME[state] ?? L(titleCase(state));
}

const UNIT_SUFFIX: [RegExp, string][] = [
  [/_pct$/, ' (%)'], [/_usd$/, ' ($)'], [/_sec$/, ' (s)'], [/_min$/, ' (min)'],
  [/_ms$/, ' (ms)'], [/_bps$/, ' (bps)'], [/_et$/, ' (ET)'], [/_r$/, ' (R)'],
];
/** snake_case → sentence case with a unit suffix: "max_total_open_risk_pct" → "Max total open risk (%)". */
export function humanKey(k: string | null | undefined): string {
  if (!k) return '—';
  let key = String(k);
  let suffix = '';
  for (const [re, unit] of UNIT_SUFFIX) {
    if (re.test(key)) { key = key.replace(re, ''); suffix = unit; break; }
  }
  const words = key.replace(/[_\-.]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (!words) return k;
  return words.charAt(0).toUpperCase() + words.slice(1) + suffix;
}

export type ScoreBand = 'Strong' | 'OK' | 'Weak';
/** ≥ minBuy (default 75) Strong · ≥ 55 OK · else Weak */
export function scoreBand(v: number | null | undefined, minBuy = 75): ScoreBand | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  if (v >= minBuy) return 'Strong';
  if (v >= 55) return 'OK';
  return 'Weak';
}

/** Scanner row status (spec §2.5). */
export function candidateStatus(
  row: Pick<CandidateRow, 'hard_blocks' | 'gates_failed' | 'early' | 'buy'>,
): Label {
  if (row.hard_blocks?.length) return L('Blocked');
  if (row.gates_failed?.length) return L('Not yet');
  if (row.early) return L('Early watch', 'early');
  if (row.buy) return L('Buy', 'buy');
  return L('Watching', 'early');
}

const GATE_WHY_RULES: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^price \$([\d.,]+) outside \$(\S+)-(\S+)$/i,
    (m) => `Price $${m[1]} is outside the $${m[2]}–${m[3] === 'inf' ? 'no cap' : '$' + m[3]} range`],
  [/^mkt cap \$(\S+) outside limits$/i, (m) => `Market cap $${m[1]} is outside the limits`],
  [/^float (\S+) outside limits$/i, (m) => `Float ${m[1]} shares is outside the limits`],
  [/^shares out (\S+) outside limits$/i, (m) => `Shares outstanding ${m[1]} is outside the limits`],
  [/^PM vol (\S+) < (\S+)$/i, (m) => `Premarket volume ${m[1]} — need ${m[2]}`],
  [/^PM \$vol \$(\S+) < \$(\S+)$/i, (m) => `Premarket $ volume $${m[1]} — need $${m[2]}`],
];
/** Rewrites a `gate_why` / `gate_reasons` string into a sentence; falls back to the raw text. */
export function plainGate(why: string | null | undefined): string {
  if (!why) return '—';
  const s = String(why).trim();
  for (const [re, fn] of GATE_WHY_RULES) {
    const m = s.match(re);
    if (m) return fn(m);
  }
  if (/^[a-z0-9_]+$/i.test(s)) return gateLabel(s);
  return s;
}

const vocab = {
  PHASE, SIGNAL_STATUS, OUTCOME, LIFECYCLE, MODEL_STATUS, LANE_STATE, REGIME, NOON_CLASS,
  SAMPLE_LABEL, GATE, BOARD, POSITION_STATUS, ITEM_CODES, CATALYST,
  phase, phaseLabel, regime, modelStatus, sampleLabel, gateLabel, itemCodes, catalystLabel,
  plainGate, humanKey, scoreBand, candidateStatus, titleCase,
};
export default vocab;

/** Feature / company keys → plain labels for `.kv .k` in the detail drawer and
 *  KV grids. Anything not listed falls back to `humanKey`. */
export const FIELD: Record<string, string> = {
  price: 'Price', price_indicative: 'Price (indicative)', gap_pct: 'Gap', rvol: 'Volume vs normal',
  pm_volume: 'Premarket volume', pm_dollar_volume: 'Premarket $ volume', vwap: 'Average price today (VWAP)',
  above_vwap: 'Above average price', pm_high: 'Premarket high', pm_low: 'Premarket low',
  spread_pct: 'Spread', bid: 'Bid', ask: 'Ask', market_cap: 'Market cap', float_shares: 'Float',
  float_rotation: 'Float traded today', shares_outstanding: 'Shares outstanding', avg_volume: 'Average daily volume',
  industry: 'Industry', sector: 'Sector', country: 'Country', exchange: 'Exchange',
  buy_price: 'Buy price', current: 'Now', change_pct: 'Since pick', stop: 'Stop', target1: 'Target 1',
  target2: 'Target 2', initiated_at: 'Picked', max_gain_pct: 'Best since pick', max_drawdown_pct: 'Worst since pick',
  post7_high: 'Early-window high', post7_low: 'Early-window low', outcome: 'Early pop?',
};
export function fieldLabel(k: string | null | undefined): string {
  if (!k) return '—';
  return FIELD[k] ?? humanKey(k);
}
