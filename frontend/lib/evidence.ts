/** Evidence classes, sample-size thresholds and the helpers every stat uses.
 *  Honesty rules (spec §7) are enforced through these — never re-implement them. */

export type Evidence = 'BACKTEST' | 'PAPER' | 'TRACKED' | 'LIVE';
export type BacktestSplit = 'DEV' | 'VALIDATION' | 'WALK_FORWARD' | 'HOLDOUT';

/** dim: value dimmed + warning becomes headline · judge: amber "too few" line ·
 *  rank: minimum trades to appear on a leaderboard · calibrated / early: backend
 *  calibration thresholds (30/100 mirror the accuracy endpoint's sample labels). */
export const SAMPLE = { dim: 10, judge: 30, rank: 5, calibrated: 50, early: 100 } as const;
export const PROMOTION_MIN_TRADES_FALLBACK = 100;

export type SampleClass = 'none' | 'tiny' | 'small' | 'ok';

/** 0/null → none · <10 → tiny · <30 → small · else ok */
export function sampleClass(n: number | null | undefined): SampleClass {
  if (n === null || n === undefined || !Number.isFinite(n) || n <= 0) return 'none';
  if (n < SAMPLE.dim) return 'tiny';
  if (n < SAMPLE.judge) return 'small';
  return 'ok';
}

function singular(unit: string): string {
  return unit.endsWith('s') ? unit.slice(0, -1) : unit;
}

/** "Too few to judge (7 trades)" | "No trades yet" | null when the sample is fine. */
export function sampleNote(n: number | null | undefined, unit = 'trades'): string | null {
  const c = sampleClass(n);
  if (c === 'none') return `No ${unit} yet`;
  if (c === 'ok') return null;
  const count = n as number;
  return `Too few to judge (${count} ${count === 1 ? singular(unit) : unit})`;
}

/** Wilson score lower bound (0..1). null when n === 0. Labelled "Conservative floor" in the UI. */
export function wilsonLower(wins: number, n: number, z = 1.96): number | null {
  if (!n || n <= 0 || !Number.isFinite(n)) return null;
  const p = Math.min(1, Math.max(0, wins / n));
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const centre = p + z2 / (2 * n);
  const margin = z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return Math.max(0, (centre - margin) / denom);
}

export const SPLIT_LABEL: Record<BacktestSplit, string> = {
  DEV: 'Dev', VALIDATION: 'Validation', WALK_FORWARD: 'Walk-forward', HOLDOUT: 'Holdout',
};

/** "Backtest · Holdout", "Paper", "Tracked", "Live (real money)".
 *  LIVE only reads "Live" when paperMode === false; otherwise it is Paper. */
export function evidenceLabel(e: Evidence, split?: BacktestSplit, paperMode?: boolean): string {
  switch (e) {
    case 'BACKTEST': return split ? `Backtest · ${SPLIT_LABEL[split]}` : 'Backtest';
    case 'PAPER': return 'Paper';
    case 'TRACKED': return 'Tracked';
    case 'LIVE': return paperMode === false ? 'Live (real money)' : 'Paper';
  }
}

export type DrawdownBasis = 'account' | 'trade_sum';

/** A drawdown is never shown without its basis word. */
export function drawdownLabel(basis: DrawdownBasis): string {
  return basis === 'account' ? 'of $10,000 account' : 'sum of trade %';
}

/** Basis per backend field (spec §7.5). Anything not listed defaults to trade_sum. */
export const DRAWDOWN_BASIS: Record<string, DrawdownBasis> = {
  'competition.cards.max_drawdown_pct': 'account',
  'models.account.max_drawdown_pct': 'account',
  'accuracy.max_drawdown_pct': 'account',
  'backtest.report.max_drawdown_pct': 'trade_sum',
  'backtest.reports.fleet.by_model.max_drawdown_pct': 'trade_sum',
  'backtest.tournament.baseline.max_drawdown_pct': 'trade_sum',
};
