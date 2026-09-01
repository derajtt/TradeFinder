'use client';
import { usePolling } from '../lib/api';

interface Canon {
  lifecycle_counts: Record<string, number>;
  totals: Record<string, number>;
  actionable_buy_performance: { open_positions: number; closed_trades: number;
    wins: number; losses: number; win_rate: number | null; win_rate_lb: number | null;
    avg_r: number | null; calibration: string; note: string };
  versions: Record<string, string>;
  reconciliation: { equals_total: boolean };
}

const ORDER = ['DISCOVERED', 'EARLY_WATCH', 'QUALIFIED_WATCH', 'ACTIONABLE_BUY',
  'REJECTED', 'INVALIDATED', 'EXPIRED', 'CLOSED'];
const COLOR: Record<string, string> = {
  ACTIONABLE_BUY: 'var(--buy)', EARLY_WATCH: 'var(--early)',
  QUALIFIED_WATCH: 'var(--warn)', REJECTED: 'var(--risk)',
  INVALIDATED: 'var(--text-faint)', EXPIRED: 'var(--text-faint)',
};

export default function FunnelStrip() {
  const [c] = usePolling<Canon>('/api/report/canonical', 30000);
  if (!c) return null;
  const perf = c.actionable_buy_performance;
  return (
    <div className="tbl-wrap" style={{ padding: '12px 16px', marginBottom: 18 }}>
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center', fontSize: 12 }}>
        <b style={{ fontSize: 11, letterSpacing: 1.2, color: 'var(--text-faint)' }}
           title="Every total on this page comes from one canonical analytics source — counts always reconcile.">
          CANONICAL FUNNEL {c.reconciliation.equals_total ? '✓' : '⚠ MISMATCH'}
        </b>
        {ORDER.filter((k) => c.lifecycle_counts[k]).map((k) => (
          <span key={k} className="tb-item">
            <span className="dot" style={{ background: COLOR[k] ?? 'var(--text-dim)' }} />
            {k.replace(/_/g, ' ').toLowerCase()} <b>{c.lifecycle_counts[k]}</b>
          </span>
        ))}
        <span className="tb-item">rejected shadow-log <b>{c.totals.rejected_candidates}</b></span>
        <span className="spacer" style={{ flex: 1 }} />
        <span className="tb-item" title={perf.note || 'Actionable paper-trade record (live_paper cohort)'}>
          paper trades <b>{perf.closed_trades}</b>
          {perf.win_rate != null && <> · WR <b>{(perf.win_rate * 100).toFixed(0)}%</b>
            <span className="faint">(LB {(perf.win_rate_lb! * 100).toFixed(0)}%)</span></>}
          <span className="badge warn" style={{ marginLeft: 6 }}>{perf.calibration}</span>
        </span>
        <span className="faint" style={{ fontSize: 10 }}>{c.versions.strategy_version}·{c.versions.filter_version}</span>
      </div>
    </div>
  );
}
