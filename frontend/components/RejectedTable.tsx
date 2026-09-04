'use client';
import { useMemo } from 'react';
import { fmtDateLabel, fmtPct, fmtPrice } from '../lib/format';
import type { Rejected } from '../lib/types';
import { gateLabel } from '../lib/vocab';
import { DataTable, type Column } from './ui/DataTable';
import { Details } from './ui/Details';
import { ScorePill } from './ui/ScorePill';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill } from './ui/StatusPill';

const MAX_ROWS = 60;
/** /api/rejected returns at most this many rows (its default `limit`). */
const API_CAP = 100;

/** Advanced-only, collapsed: stocks the scanner looked at and blocked today,
 *  with what happened to the price afterwards — so the filters can be audited. */
export default function RejectedTable({ rows, loaded, onSelect }: {
  rows: Rejected[]; loaded: boolean; onSelect: (symbol: string) => void;
}) {
  const columns = useMemo<Column<Rejected>[]>(() => [
    { key: 'symbol', header: 'Stock', align: 'l', sortValue: (r) => r.symbol,
      cell: (r) => (
        <span className="stock-cell">
          <span className="sym">{r.symbol}</span>
          <span className="co-name">{fmtDateLabel(r.session_date)}</span>
        </span>
      ) },
    { key: 'price', header: 'Price at block', sortValue: (r) => r.price, cell: (r) => fmtPrice(r.price) },
    { key: 'score', header: 'Score', term: 'score_plain', sortValue: (r) => r.score,
      cell: (r) => <ScorePill value={r.score} /> },
    { key: 'failed_gates', header: 'Blocked by', align: 'l',
      cell: (r) => (
        <span className="chips">
          {(r.failed_gates ?? []).map((g, i) => <StatusPill key={i} size="sm" tone="risk" label={gateLabel(g)} raw={g} />)}
        </span>
      ) },
    { key: 'shadow_high', header: 'High after block', sortValue: (r) => r.shadow_high,
      cell: (r) => <span className="dim">{fmtPrice(r.shadow_high)}</span> },
    { key: 'missed_move_pct', header: 'Move after block', sortValue: (r) => r.missed_move_pct,
      cell: (r) => <span className={r.missed_move_pct != null && r.missed_move_pct > 0 ? 'pos' : 'dim'}>{fmtPct(r.missed_move_pct)}</span> },
  ], []);

  const shown = rows.slice(0, MAX_ROWS);
  return (
    <section aria-labelledby="blocked-title">
      <SectionHeader id="blocked" title={<span id="blocked-title">Blocked today</span>}
        count={loaded ? rows.length : undefined}
        question="Did the filters block anything that then moved?"
        caption={`Stocks the scanner looked at and blocked today — kept so we can check the filters aren't wrong${rows.length >= API_CAP ? ` · only the most recent ${API_CAP} are returned` : ''}`}
        evidence="TRACKED" />
      {loaded && rows.length > 0 ? (
        <Details summary={`Show ${rows.length} blocked stock${rows.length === 1 ? '' : 's'}`}>
          <DataTable<Rejected>
            rows={shown} columns={columns} rowKey={(r) => `${r.symbol}-${r.rejected_at}`}
            onRowClick={(r) => onSelect(r.symbol)} defaultSort={{ key: 'missed_move_pct', dir: 'desc' }}
            loaded={loaded} evidence="TRACKED" minWidth={820}
            note={rows.length > MAX_ROWS ? `Showing the first ${MAX_ROWS} of ${rows.length}` : undefined} />
        </Details>
      ) : null}
    </section>
  );
}
