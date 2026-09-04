'use client';
import { useMemo, useState } from 'react';
import { fmtCompact, fmtPct, fmtPrice } from '../lib/format';
import type { RadarRow } from '../lib/types';
import { DataTable, type Column } from './ui/DataTable';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill } from './ui/StatusPill';

export type { RadarRow } from '../lib/types';

const TOP = 24;

/** Advanced-only, hidden when empty: movers across the full exchange universe
 *  that are next in line for a closer look. */
export default function RadarTable({ rows, onSelect, loaded = true }: {
  rows: RadarRow[]; onSelect: (symbol: string) => void; loaded?: boolean;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = useMemo(() => (showAll ? rows : rows.slice(0, TOP)), [rows, showAll]);

  const columns = useMemo<Column<RadarRow>[]>(() => [
    { key: 'symbol', header: 'Stock', align: 'l', sortValue: (r) => r.symbol,
      cell: (r) => (
        <span className="stock-cell">
          <span className="sym">{r.symbol}</span>
          {r.name ? <span className="co-name">{r.name}</span> : null}
        </span>
      ) },
    { key: 'price', header: 'Price', sortValue: (r) => r.price, cell: (r) => fmtPrice(r.price) },
    { key: 'gap_pct', header: 'Gap', term: 'gap', sortValue: (r) => r.gap_pct,
      cell: (r) => <span className={(r.gap_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(r.gap_pct)}</span> },
    { key: 'volume', header: 'Day volume', sortValue: (r) => r.volume, cell: (r) => fmtCompact(r.volume) },
    { key: 'market_cap', header: 'Market cap', term: 'mkt_cap', sortValue: (r) => r.market_cap,
      cell: (r) => (r.market_cap != null ? '$' + fmtCompact(r.market_cap) : '—') },
    { key: 'has_news', header: 'News', align: 'l', sortValue: (r) => (r.has_news ? 1 : 0),
      cell: (r) => (r.has_news ? <StatusPill size="sm" tone="accent" label="News" /> : '—') },
    { key: 'exchange', header: 'Exchange', align: 'l', sortValue: (r) => r.exchange,
      cell: (r) => <span className="faint">{r.exchange || '—'}</span> },
  ], []);

  if (!loaded || !rows.length) return null;
  return (
    <section aria-labelledby="radar-title">
      <SectionHeader id="radar" title={<span id="radar-title">Radar</span>} count={rows.length}
        question="Which other movers are next in line for a closer look?"
        caption="All models · movers across the full exchange universe · not yet checked against the rules"
        right={rows.length > TOP ? (
          <button type="button" className="btn sm" onClick={() => setShowAll((v) => !v)}>
            {showAll ? `Show top ${TOP}` : `Show all ${rows.length}`}
          </button>
        ) : undefined} />
      <DataTable<RadarRow>
        rows={visible} columns={columns} rowKey={(r) => r.symbol}
        onRowClick={(r) => onSelect(r.symbol)} defaultSort={{ key: 'gap_pct', dir: 'desc' }}
        loaded={loaded} minWidth={760} />
    </section>
  );
}
