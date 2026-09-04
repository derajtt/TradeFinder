'use client';
import { useMemo, useState } from 'react';
import { fmtCompact, fmtEtShort, fmtMult, fmtPct, fmtPrice } from '../lib/format';
import { useMode } from '../lib/mode';
import type { PhaseKey } from '../lib/status';
import type { CandidateRow } from '../lib/types';
import { candidateStatus, catalystLabel } from '../lib/vocab';
import s from './today.module.css';
import { plainProse } from './todayShared';
import { DataTable, type Column } from './ui/DataTable';
import { Details } from './ui/Details';
import { EmptyState } from './ui/EmptyState';
import { ScorePill } from './ui/ScorePill';
import { StatusPill } from './ui/StatusPill';
import { WhatsMissing } from './ui/WhatsMissing';

export interface CandidateTableProps {
  rows: CandidateRow[];
  updatedSyms: Set<string>;
  onSelect: (symbol: string) => void;
  loaded: boolean;
  minScoreForBuy?: number;
  phaseKey: PhaseKey;
  quietReason: string | null | undefined;     // ops.quiet_reason (from the page's useOps)
  lastCycleAt: string | null | undefined;     // status.scanner.last_cycle_at
}

function signCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'dim';
  return v >= 0 ? 'pos' : 'neg';
}

/** What stocks are being checked right now, and what is blocking them?
 *  Simple: 7 plain columns, full table only while the scanner runs; otherwise a
 *  one-line summary with the last results behind "Show last results". */
export default function CandidateTable(props: CandidateTableProps) {
  const { rows, updatedSyms, onSelect, loaded, minScoreForBuy, phaseKey, quietReason, lastCycleAt } = props;
  const { advanced } = useMode();
  const [filter, setFilter] = useState('');

  const filtered = useMemo(() => {
    const f = filter.trim().toUpperCase();
    if (!f) return rows;
    return rows.filter((x) => x.symbol.includes(f) || x.name?.toUpperCase().includes(f));
  }, [rows, filter]);

  const columns = useMemo<Column<CandidateRow>[]>(() => [
    { key: 'symbol', header: 'Stock', align: 'l', simple: true, sortValue: (r) => r.symbol,
      cell: (r) => (
        <span className="stock-cell">
          <span className="sym">{r.symbol}</span>
          {r.name ? <span className="co-name">{r.name}</span> : null}
        </span>
      ) },
    { key: 'score', header: 'Score', term: 'score_plain', simple: true, sortValue: (r) => r.score,
      cell: (r) => <ScorePill value={r.score} minBuy={minScoreForBuy} /> },
    { key: 'price', header: 'Price', simple: true, sortValue: (r) => r.price,
      cell: (r) => <>{fmtPrice(r.price)}{r.price_indicative ? <span className={s.chipGray}>indicative</span> : null}</> },
    { key: 'gap_pct', header: 'Gap', term: 'gap', simple: true, sortValue: (r) => r.gap_pct,
      cell: (r) => <span className={signCls(r.gap_pct)}>{fmtPct(r.gap_pct)}</span> },
    { key: 'rvol', header: 'Volume vs normal', term: 'rvol', simple: true, sortValue: (r) => r.rvol,
      cell: (r) => (r.rvol == null ? '—' : <>{fmtMult(r.rvol)} normal{r.rvol_estimated ? <span className={s.chipGray}>estimate</span> : null}</>) },
    // ── Advanced-only ──
    { key: 'pm_volume', header: 'Premarket volume', term: 'pm_vol', sortValue: (r) => r.pm_volume,
      cell: (r) => fmtCompact(r.pm_volume) },
    { key: 'pm_dollar_volume', header: 'Premarket $ volume', term: 'pm_dvol', sortValue: (r) => r.pm_dollar_volume,
      cell: (r) => (r.pm_dollar_volume != null ? '$' + fmtCompact(r.pm_dollar_volume) : '—') },
    { key: 'float_shares', header: 'Float', term: 'float', sortValue: (r) => r.float_shares,
      cell: (r) => fmtCompact(r.float_shares) },
    { key: 'float_rotation', header: 'Float traded today', term: 'float_rot', sortValue: (r) => r.float_rotation ?? null,
      cell: (r) => (r.float_rotation != null ? (r.float_rotation * 100).toFixed(1) + '%' : '—') },
    { key: 'market_cap', header: 'Market cap', term: 'mkt_cap', sortValue: (r) => r.market_cap,
      cell: (r) => (r.market_cap != null ? '$' + fmtCompact(r.market_cap) : '—') },
    { key: 'spread_pct', header: 'Spread', term: 'spread', sortValue: (r) => r.spread_pct,
      cell: (r) => <span className={r.spread_pct != null && r.spread_pct > 5 ? 'neg' : ''}>{fmtPct(r.spread_pct, false)}</span> },
    { key: 'catalyst_type', header: 'News type', term: 'catalyst', align: 'l', sortValue: (r) => r.catalyst_type,
      cell: (r) => (
        <>
          {catalystLabel(r.catalyst_type)}
          {(r.catalyst_sources?.news ?? 0) > 0 ? <span className={s.chipGray}>news ×{r.catalyst_sources!.news}</span> : null}
        </>
      ) },
    { key: 'filings', header: 'Filings', term: 'filings', align: 'l',
      isEmpty: (r) => !(r.filing_links?.length || r.filing_forms?.length),
      cell: (r) => (r.filing_links?.length
        ? r.filing_links.slice(0, 3).map((f, i) => (
            <a key={i} className="chip" href={f.url} target="_blank" rel="noreferrer"
               onClick={(e) => e.stopPropagation()}>{f.form} ↗</a>
          ))
        : (r.filing_forms?.slice(0, 3).join(' · ') || '—')) },
    // ── Simple again ──
    { key: 'missing', header: "What's missing", term: 'whats_missing', align: 'l', simple: true,
      isEmpty: () => false,
      cell: (r) => <WhatsMissing explain={r.explain} hardBlocks={r.hard_blocks} /> },
    { key: 'status', header: 'Status', align: 'l', simple: true, sortValue: (r) => candidateStatus(r).label,
      cell: (r) => <StatusPill size="sm" {...candidateStatus(r)} /> },
    // ── Advanced-only: the raw gate strings ──
    { key: 'gate_why', header: 'Why (raw)', align: 'l',
      isEmpty: (r) => !(r.hard_blocks?.length || r.gate_reasons?.length),
      cell: (r) => {
        const raw = [...(r.hard_blocks ?? []), ...(r.gate_reasons ?? [])];
        return raw.length ? <span className={s.raw}>{raw.join(' · ')}</span> : '—';
      } },
  ], [minScoreForBuy]);

  const table = (
    <>
      <div className={s.toolbar}>
        <label>
          Find a symbol
          <input className={s.find} value={filter} onChange={(e) => setFilter(e.target.value)}
            placeholder="e.g. ABCD" autoComplete="off" spellCheck={false} />
        </label>
        {filter && loaded ? <span className="dim">{filtered.length} of {rows.length}</span> : null}
      </div>
      <DataTable<CandidateRow>
        rows={filtered} columns={columns} rowKey={(r) => r.symbol}
        onRowClick={(r) => onSelect(r.symbol)}
        defaultSort={{ key: 'score', dir: 'desc' }}
        loaded={loaded} minWidth={advanced ? 1280 : 900}
        rowClassName={(r) => (updatedSyms.has(r.symbol) ? 'row-updated' : undefined)}
        empty={<EmptyState compact headline="No candidates right now"
          reason={quietReason ? plainProse(quietReason) : null}
          next="Stocks appear here as soon as the premarket scan finds movers that pass the basic rules." />}
      />
    </>
  );

  const scanning = phaseKey === 'premarket' || phaseKey === 'open';
  if (!advanced && loaded && !scanning) {
    return (
      <div>
        <div className={s.panelLine}>
          <span>The premarket stock scan runs 4:00–9:30 AM ET. The last scanner cycle (<b>{fmtEtShort(lastCycleAt)}</b>) found <b>{rows.length}</b> candidate{rows.length === 1 ? '' : 's'}.</span>
        </div>
        {rows.length ? <Details summary="Show last results">{table}</Details> : null}
      </div>
    );
  }
  return table;
}
