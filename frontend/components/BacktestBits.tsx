'use client';
import { wilsonLower } from '../lib/evidence';

/** One metrics block as emitted by `metrics()` in backend/app/bt/tournament.py
 *  (shared by /api/backtest/report splits, walk-forward, holdout, fleet
 *  `by_model[]` and the exit tournament). Rates are 0..1; percentages are %. */
export interface BtMetric {
  n: number;
  n_signals?: number;
  wins?: number; losses?: number; neutral?: number;
  ambiguous?: number; no_fill?: number;
  ambiguous_rate?: number | null; fill_rate?: number | null;
  win_rate: number | null;
  win_rate_lb?: number | null;
  expectancy_pct: number | null;
  expectancy_r?: number | null;
  avg_win_pct?: number | null; avg_loss_pct?: number | null;
  payoff?: number | null;
  profit_factor: number | null;
  median_pct?: number | null;
  /** basis: sum of trade % (spec §7.5) */
  max_drawdown_pct: number | null;
  avg_exposure_min?: number | null;
  ret_per_min?: number | null;
  top5_share?: number | null;
  /** fleet only — expectancy in the first / second half of the sessions */
  first_half_exp?: number | null; second_half_exp?: number | null;
}

/** 0..1 → "34%" */
export function ratePct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (v * 100).toFixed(digits) + '%';
}

/** already-a-percent → "+1.47%" */
export function pctSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(digits) + '%';
}

export function pfText(v: number | null | undefined, n?: number | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v === 0 && !n) return '—';
  return v.toFixed(2);
}

/** Conservative floor (Wilson lower bound), 0..1. Computed client-side from
 *  wins/n whenever both exist; the API's `win_rate_lb` only when they do not. */
export function floorOf(m: Pick<BtMetric, 'wins' | 'n' | 'win_rate_lb'> | null | undefined): number | null {
  if (!m) return null;
  if (m.wins !== undefined && m.wins !== null && m.n) return wilsonLower(m.wins, m.n);
  return m.win_rate_lb ?? null;
}

/** "n=35 · 20% won · floor 10% · exp −1.09% · PF 0.26 · DD 38.0% sum of trade %" */
export function MetricLine({ m }: { m: BtMetric | null | undefined }) {
  if (!m || m.n === null || m.n === undefined) return <span className="dim">Not in this report</span>;
  const dd = m.max_drawdown_pct;
  return (
    <span className="mono metric-line">
      n={m.n} · {ratePct(m.win_rate)} won · floor {ratePct(floorOf(m))} · exp {pctSigned(m.expectancy_pct)}
      {' '}· PF {pfText(m.profit_factor, m.n)}
      {' '}· DD {dd === null || dd === undefined ? '—' : `${Math.abs(dd).toFixed(1)}% sum of trade %`}
    </span>
  );
}

/** Gray one-liner for a section whose data object is absent from the payload. */
export function NotInReport({ what }: { what: string }) {
  return <p className="note">{what}: not in this report.</p>;
}
