'use client';
import { useMemo, useState } from 'react';
import { fmtCompact, fmtNum, fmtPct, fmtPrice } from '../lib/format';
import type { CandidateRow } from '../lib/types';
import Freshness from './Freshness';
import Score from './Score';

type SortKey = keyof CandidateRow | 'score';

const COLS: { key: SortKey; label: string; left?: boolean }[] = [
  { key: 'symbol', label: 'Symbol', left: true },
  { key: 'score', label: 'Score' },
  { key: 'price', label: 'Price' },
  { key: 'gap_pct', label: 'Gap%' },
  { key: 'rvol', label: 'RVOL' },
  { key: 'pm_volume', label: 'PM Vol' },
  { key: 'pm_dollar_volume', label: 'PM $Vol' },
  { key: 'float_shares', label: 'Float' },
  { key: 'market_cap', label: 'Mkt Cap' },
  { key: 'spread_pct', label: 'Spread' },
  { key: 'catalyst_type', label: 'Catalyst', left: true },
  { key: 'filing_forms', label: 'Filings', left: true },
  { key: 'hard_blocks', label: 'Status', left: true },
];

export default function CandidateTable({ rows, updatedSyms, onSelect }: {
  rows: CandidateRow[]; updatedSyms: Set<string>;
  onSelect: (symbol: string) => void;
}) {
  const [sort, setSort] = useState<SortKey>('score');
  const [dir, setDir] = useState<1 | -1>(-1);
  const [filter, setFilter] = useState('');

  const sorted = useMemo(() => {
    const f = filter.trim().toUpperCase();
    let r = rows;
    if (f) r = r.filter((x) => x.symbol.includes(f) || x.name?.toUpperCase().includes(f)
      || x.catalyst_type?.toUpperCase().includes(f));
    return [...r].sort((a, b) => {
      const av = a[sort as keyof CandidateRow];
      const bv = b[sort as keyof CandidateRow];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, sort, dir, filter]);

  const click = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === 1 ? -1 : 1));
    else { setSort(k); setDir(-1); }
  };

  if (!rows.length) {
    return (
      <div className="tbl-wrap"><div className="empty">
        <b>No candidates yet</b>
        The scanner surfaces symbols here as soon as premarket movers pass the universe gates.
      </div></div>
    );
  }

  return (
    <>
      <div style={{ margin: '0 0 8px' }}>
        <input aria-label="Filter candidates" placeholder="Filter symbol / name / catalyst…"
          value={filter} onChange={(e) => setFilter(e.target.value)}
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)',
                   borderRadius: 8, padding: '7px 12px', width: 280, fontSize: 13 }} />
      </div>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr>
            {COLS.map((c) => (
              <th key={String(c.key)} className={c.left ? 'l' : ''} onClick={() => click(c.key)}
                  aria-sort={sort === c.key ? (dir === 1 ? 'ascending' : 'descending') : undefined}>
                {c.label}{sort === c.key ? (dir === 1 ? ' ▲' : ' ▼') : ''}
              </th>
            ))}
          </tr></thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.symbol} className={updatedSyms.has(r.symbol) ? 'row-updated' : ''}
                  onClick={() => onSelect(r.symbol)} tabIndex={0} role="button"
                  onKeyDown={(e) => e.key === 'Enter' && onSelect(r.symbol)}
                  aria-label={`Open ${r.symbol} details`}>
                <td className="l">
                  <div className="sym">{r.symbol} {r.buy && <span className="badge buy">BUY</span>}</div>
                  <div className="co-name">{r.name}</div>
                </td>
                <td><Score v={r.score} /></td>
                <td>{fmtPrice(r.price)} <Freshness ts={r.provider_ts} fresh={r.quote_fresh} /></td>
                <td className={(r.gap_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(r.gap_pct)}</td>
                <td>{r.rvol != null ? <>{fmtNum(r.rvol, 1)}x {r.rvol_estimated && <span className="badge est" title="Estimated vs avg daily volume curve — baseline history still accumulating">EST</span>}</> : <span className="faint" title="Baseline coverage insufficient">—</span>}</td>
                <td>{fmtCompact(r.pm_volume)}</td>
                <td>{r.pm_dollar_volume != null ? '$' + fmtCompact(r.pm_dollar_volume) : '—'}</td>
                <td>{fmtCompact(r.float_shares)}</td>
                <td>{r.market_cap != null ? '$' + fmtCompact(r.market_cap) : '—'}</td>
                <td className={r.spread_pct != null && r.spread_pct > 5 ? 'neg' : ''}>{fmtPct(r.spread_pct, false)}</td>
                <td className="l">
                  {r.catalyst_type
                    ? <span className={`badge ${r.catalyst_direction === 'positive' ? 'buy' : r.catalyst_direction === 'negative' ? 'risk' : 'neutral'}`}>{r.catalyst_type}</span>
                    : <span className="faint">none</span>}
                </td>
                <td className="l faint" style={{ fontSize: 11 }}>{r.filing_forms?.slice(0, 3).join(' · ') || '—'}</td>
                <td className="l">
                  {r.hard_blocks?.length
                    ? <span className="badge risk" title={r.hard_blocks.join(', ')}>blocked</span>
                    : r.gates_failed?.length
                      ? <span className="badge warn" title={r.gates_failed.join(', ')}>gated</span>
                      : r.buy ? <span className="badge buy">qualified</span>
                      : <span className="badge neutral">watching</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
