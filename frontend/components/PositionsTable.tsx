'use client';
import { useMemo } from 'react';
import { fmtEtShort, fmtPrice, fmtR } from '../lib/format';
import { useMarketPhase } from '../lib/status';
import type { Position } from '../lib/types';
import { POSITION_STATUS, humanKey } from '../lib/vocab';
import Freshness from './Freshness';
import s from './today.module.css';
import { DataTable, type Column } from './ui/DataTable';
import { Details } from './ui/Details';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill, pillFor } from './ui/StatusPill';

export type LiveQuote = { current: number | null; current_ts: string | null };

export interface PositionsTableProps {
  rows: Position[];
  loaded: boolean;
  scopeLabel: string;
  onSelect: (symbol: string) => void;
  /** latest price per symbol taken from the signal rows — "Now" is never fetched separately */
  liveBySymbol: Record<string, LiveQuote>;
}

function rCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return 'dim';
  return v > 0 ? 'pos' : 'neg';
}

/** Does the paper account hold anything right now? */
export default function PositionsTable({ rows, loaded, scopeLabel, onSelect, liveBySymbol }: PositionsTableProps) {
  const phase = useMarketPhase();
  const marketOpen = phase.isOpen || phase.isPremarket;

  const sorted = useMemo(() => [...rows].sort((a, b) => {
    if (a.status !== b.status) return a.status === 'open' ? -1 : 1;
    return b.opened_at.localeCompare(a.opened_at);
  }), [rows]);
  const openCount = rows.filter((p) => p.status === 'open').length;
  const closedRows = useMemo(() => sorted.filter((p) => p.status !== 'open'), [sorted]);

  const columns = useMemo<Column<Position>[]>(() => [
    { key: 'symbol', header: 'Stock', align: 'l', simple: true, sortValue: (r) => r.symbol,
      cell: (r) => <span className="sym">{r.symbol}</span> },
    { key: 'entry_fill', header: 'Bought at', simple: true, sortValue: (r) => r.entry_fill,
      cell: (r) => fmtPrice(r.entry_fill) },
    { key: 'now', header: 'Now', simple: true,
      isEmpty: (r) => liveBySymbol[r.symbol]?.current == null,
      cell: (r) => {
        const q = liveBySymbol[r.symbol];
        if (!q || q.current == null) return '—';
        return <>{fmtPrice(q.current)} <Freshness ts={q.current_ts} marketOpen={marketOpen} /></>;
      } },
    { key: 'stop', header: 'Stop', simple: true, sortValue: (r) => r.stop,
      cell: (r) => <span className={r.stop != null ? 'neg' : 'dim'}>{fmtPrice(r.stop)}</span> },
    { key: 'targets', header: 'Targets (T1 / T2)', simple: true,
      isEmpty: (r) => r.target1 == null && r.target2 == null,
      cell: (r) => <span className="pos">{fmtPrice(r.target1)} / {fmtPrice(r.target2)}</span> },
    { key: 'realized_r', header: 'Result so far', term: 'r_multiple', simple: true, sortValue: (r) => r.realized_r,
      cell: (r) => <span className={rCls(r.realized_r)}>{fmtR(r.realized_r)}</span> },
    { key: 'status', header: 'Status', align: 'l', simple: true, sortValue: (r) => r.status,
      cell: (r) => <StatusPill size="sm" {...pillFor(POSITION_STATUS, r.status)} /> },
    // ── Advanced-only ──
    { key: 'remaining_frac', header: 'Remaining', sortValue: (r) => r.remaining_frac,
      cell: (r) => `${Math.round((r.remaining_frac ?? 0) * 100)}%` },
    { key: 'exit_reason', header: 'Exit reason', align: 'l',
      cell: (r) => (r.exit_reason ? humanKey(r.exit_reason) : '—') },
    { key: 'opened_at', header: 'Opened', align: 'l', sortValue: (r) => r.opened_at,
      cell: (r) => fmtEtShort(r.opened_at) },
    { key: 'strategy_version', header: 'Engine', align: 'l',
      cell: (r) => (r.strategy_version ? <code className="pill-raw">v{r.strategy_version.replace(/^v/i, '')}</code> : '—') },
  ], [liveBySymbol, marketOpen]);

  const table = (tableRows: Position[]) => (
    <DataTable<Position>
      rows={tableRows} columns={columns} rowKey={(r) => `${r.symbol}-${r.opened_at}`}
      onRowClick={(r) => onSelect(r.symbol)} loaded={loaded} minWidth={760}
      empty={<div className={s.oneLine}>No paper trades recorded for {scopeLabel}.</div>}
    />
  );

  return (
    <section aria-labelledby="positions-title">
      <SectionHeader id="positions" title={<span id="positions-title">Open paper trades</span>}
        count={loaded ? openCount : undefined}
        question="Does the paper account hold anything right now?"
        caption={`Paper account · ${scopeLabel}`} evidence="PAPER" />
      {!loaded ? table([]) : openCount > 0 ? table(sorted) : (
        <>
          <div className={s.oneLine}>No open paper trades for {scopeLabel}.</div>
          {closedRows.length ? (
            <Details summary={`Show ${closedRows.length} closed paper trade${closedRows.length === 1 ? '' : 's'}`}>
              {table(closedRows)}
            </Details>
          ) : null}
        </>
      )}
    </section>
  );
}
