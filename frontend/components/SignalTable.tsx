'use client';
import { useMemo } from 'react';
import { fmtEtShort, fmtPct, fmtPrice } from '../lib/format';
import { useMode } from '../lib/mode';
import { useAppSettings, useMarketPhase } from '../lib/status';
import type { SignalRow } from '../lib/types';
import { OUTCOME, SIGNAL_STATUS, catalystLabel } from '../lib/vocab';
import Freshness from './Freshness';
import { DataTable, type Column } from './ui/DataTable';
import { EmptyState } from './ui/EmptyState';
import { ScorePill } from './ui/ScorePill';
import { StatusPill, pillFor } from './ui/StatusPill';
import { WhatsMissing } from './ui/WhatsMissing';

export interface SignalTableProps {
  rows: SignalRow[]; onSelect: (r: SignalRow) => void;
  /** 'watch' = Today's Watching table · 'buy' = buys only · 'mixed' = Picks pages (Buy/Watch pill). Default 'mixed'. */
  variant?: 'watch' | 'buy' | 'mixed';
  /** "{name} model" — the scope the parent's SectionHeader caption carries. */
  scope?: string;
  /** false → skeleton rows; nothing is ever "0" or empty before the first response. Default true. */
  loaded?: boolean;
  cap?: number; marketClosed?: boolean; minScoreForBuy?: number;
  showWhatsMissing?: boolean;          // default true for variant 'watch' in Simple
  emptyState?: React.ReactNode;
  /** false when the parent SectionHeader already carries the Tracked chip — one chip per table (spec §7.2). Default true. */
  evidenceChip?: boolean;
  /** @deprecated ignored — the column set is mode-driven now */
  compact?: boolean;
}

function signCls(v: number | null | undefined, onlyWhenNonzero = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'dim';
  if (onlyWhenNonzero && v === 0) return 'dim';
  return v >= 0 ? 'pos' : 'neg';
}
function isEarlyWindow(r: SignalRow): boolean {
  return r.lifecycle === 'EARLY_WATCH' || r.best_lifecycle === 'EARLY_WATCH';
}
function hasPlan(r: SignalRow): boolean {
  return r.stop != null || r.target1 != null || r.target2 != null;
}

const EMPTY: Record<NonNullable<SignalTableProps['variant']>, { headline: string; reason: string }> = {
  watch: { headline: 'Nothing being watched right now', reason: 'No stock has qualified for tracking in the current scan.' },
  buy: { headline: 'No Buy picks', reason: 'A stock becomes a Buy pick only when every check passes.' },
  mixed: { headline: 'No picks recorded', reason: 'This strategy has not flagged any stock yet.' },
};

/** Tracked picks on `DataTable` (spec §2.4 / §4.10). Simple shows 7 plain columns;
 *  Advanced shows all 14 with "Early pop?" words instead of WIN/LOSS enums. */
export function SignalTable(props: SignalTableProps) {
  const { rows, onSelect, variant = 'mixed', loaded = true, cap, marketClosed,
    minScoreForBuy, showWhatsMissing, emptyState, evidenceChip = true } = props;
  const { advanced } = useMode();
  const phase = useMarketPhase();
  const { settings } = useAppSettings();
  // Settings are read, not hard-coded (spec §7.11); callers may still override.
  const closed = marketClosed ?? !(phase.isOpen || phase.isPremarket);
  const minBuy = minScoreForBuy ?? settings?.min_score_for_buy ?? 75;
  const freshSec = settings?.quote_freshness_sec;
  const wm = showWhatsMissing ?? (variant === 'watch' && !advanced);
  const sinceLabel = variant === 'watch' ? 'Since spotted' : 'Since pick';
  const firstLabel = variant === 'buy' ? 'Buy price' : 'First seen';

  const columns = useMemo<Column<SignalRow>[]>(() => {
    const cols: Column<SignalRow>[] = [
      { key: 'symbol', header: 'Stock', align: 'l', simple: true, sortValue: (r) => r.symbol,
        cell: (r) => (
          <span className="stock-cell">
            <span className="sym">{r.symbol}</span>
            {variant === 'mixed'
              ? (r.signal_type === 'watch'
                ? <StatusPill size="sm" label="Watch" tone="early" />
                : <StatusPill size="sm" label="Buy" tone="buy" />)
              : null}
            {variant !== 'mixed' && isEarlyWindow(r) ? <StatusPill size="sm" label="Early watch" tone="early" /> : null}
            {r.is_demo ? <StatusPill size="sm" label="Demo" tone="warn" /> : null}
            {r.name ? <span className="co-name">{r.name}</span> : null}
          </span>
        ) },
      { key: 'score', header: 'Score', term: 'score_plain', simple: true, sortValue: (r) => r.score,
        cell: (r) => <ScorePill value={r.score} minBuy={minBuy} /> },
      { key: 'buy_price', header: firstLabel, term: 'buy_price', simple: true, sortValue: (r) => r.buy_price,
        cell: (r) => <b>{fmtPrice(r.buy_price)}</b> },
      { key: 'current', header: 'Now', simple: true, sortValue: (r) => r.current,
        cell: (r) => (
          <>
            {fmtPrice(r.current)}{' '}
            <Freshness ts={r.current_ts} marketOpen={!closed} dot={!closed} thresholdSec={freshSec} />
          </>
        ) },
      { key: 'change_pct', header: sinceLabel, simple: true, sortValue: (r) => r.change_pct,
        cell: (r) => <span className={signCls(r.change_pct)}>{fmtPct(r.change_pct)}</span> },
    ];
    if (wm) {
      cols.push({ key: 'missing', header: "What's missing", term: 'whats_missing', align: 'l', simple: true,
        cell: (r) => (r.status === 'active' ? <WhatsMissing lazy symbol={r.symbol} /> : '—'),
        isEmpty: (r) => r.status !== 'active' });
    }
    cols.push(
      { key: 'initiated_at', header: 'Picked', align: 'l', simple: true, sortValue: (r) => r.initiated_at,
        cell: (r) => fmtEtShort(r.initiated_at) },
      // ── Advanced-only from here ──
      { key: 'plan', header: 'Stop / Target 1 / Target 2', isEmpty: (r) => !hasPlan(r),
        cell: (r) => (hasPlan(r) ? (
          <>
            <span className="neg">{fmtPrice(r.stop)}</span><span className="faint"> · </span>
            <span className="pos">{fmtPrice(r.target1)}</span><span className="faint"> · </span>
            <span className="pos">{fmtPrice(r.target2)}</span>
          </>
        ) : '—') },
      { key: 'day', header: 'Day high / low', term: 'day_hilo', isEmpty: (r) => r.day_high == null && r.day_low == null,
        cell: (r) => <span className="dim">{fmtPrice(r.day_high)} / {fmtPrice(r.day_low)}</span> },
      { key: 'since', header: 'Since high / low', term: 'since_hilo', isEmpty: (r) => r.since_high == null && r.since_low == null,
        cell: (r) => <span className="dim">{fmtPrice(r.since_high)} / {fmtPrice(r.since_low)}</span> },
      { key: 'max_gain_pct', header: 'Max gain', term: 'max_gain', sortValue: (r) => r.max_gain_pct,
        cell: (r) => <span className={signCls(r.max_gain_pct, true)}>{fmtPct(r.max_gain_pct)}</span> },
      { key: 'max_drawdown_pct', header: 'Max drop', term: 'max_dd', sortValue: (r) => r.max_drawdown_pct,
        cell: (r) => <span className={signCls(r.max_drawdown_pct, true)}>{fmtPct(r.max_drawdown_pct)}</span> },
      { key: 'outcome', header: 'Early pop?', term: 'early_pop', align: 'l', simple: variant === 'mixed',
        sortValue: (r) => r.outcome ?? 'pending',
        cell: (r) => <StatusPill size="sm" {...pillFor(OUTCOME, r.outcome || 'pending')} /> },
      { key: 'catalyst_type', header: 'News type', align: 'l',
        isEmpty: (r) => !r.catalyst_type || r.catalyst_type === 'none' || r.catalyst_type === 'unclassified',
        cell: (r) => catalystLabel(r.catalyst_type) },
      { key: 'status', header: 'Status', align: 'l', sortValue: (r) => r.status,
        cell: (r) => <StatusPill size="sm" {...pillFor(SIGNAL_STATUS, r.status)} /> },
    );
    return cols;
  }, [variant, wm, minBuy, closed, freshSec, sinceLabel, firstLabel]);

  const defaultSort = variant === 'watch'
    ? { key: 'score', dir: 'desc' as const }
    : { key: 'initiated_at', dir: 'desc' as const };
  const e = EMPTY[variant];

  return (
    <DataTable<SignalRow>
      rows={rows} columns={columns} rowKey={(r) => r.signal_uid} onRowClick={onSelect}
      defaultSort={defaultSort} cap={cap} loaded={loaded} evidence={evidenceChip ? 'TRACKED' : undefined}
      note={closed ? 'Market is closed — prices are from the last session' : undefined}
      rowClassName={(r) => (r.status === 'invalidated' ? 'row-dropped' : undefined)}
      empty={emptyState ?? <EmptyState compact headline={e.headline} reason={e.reason} />}
    />
  );
}
export default SignalTable;
