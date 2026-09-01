'use client';
import { useMemo, useState } from 'react';
import { fmtCompact, fmtNum, fmtPct, fmtPrice } from '../lib/format';
import type { CandidateRow } from '../lib/types';
import { TERMS } from '../lib/terms';
import Freshness from './Freshness';
import Score from './Score';

type SortKey = keyof CandidateRow | 'score';

const COLS: { key: SortKey; label: string; left?: boolean; tip?: string }[] = [
  { key: 'symbol', label: 'Symbol', left: true },
  { key: 'score', label: 'Score', tip: TERMS.score },
  { key: 'price', label: 'Price', tip: TERMS.price },
  { key: 'gap_pct', label: 'Gap%', tip: TERMS.gap },
  { key: 'rvol', label: 'RVOL', tip: TERMS.rvol },
  { key: 'pm_volume', label: 'PM Vol', tip: TERMS.pm_vol },
  { key: 'pm_dollar_volume', label: 'PM $Vol', tip: TERMS.pm_dvol },
  { key: 'float_shares', label: 'Float', tip: TERMS.float },
  { key: 'float_rotation' as SortKey, label: 'Rot%', tip: TERMS.float_rot },
  { key: 'market_cap', label: 'Mkt Cap', tip: TERMS.mkt_cap },
  { key: 'spread_pct', label: 'Spread', tip: TERMS.spread },
  { key: 'catalyst_type', label: 'Catalyst', left: true, tip: TERMS.catalyst },
  { key: 'filing_forms', label: 'Filings', left: true, tip: TERMS.filings },
  { key: 'hard_blocks', label: 'Status', left: true, tip: TERMS.status },
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
                  title={c.tip}
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
                  <div className="sym">{r.symbol} {r.buy && <span className="badge buy">BUY</span>}{!r.buy && r.early && <span className="badge early" title="All gates pass except the broker premarket window — BUY confirms when it opens">EARLY</span>}</div>
                  <div className="co-name">{r.name}</div>
                </td>
                <td title={Object.entries(r.components || {}).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join('\n') + (r.penalties?.length ? '\npenalties: ' + r.penalties.map((p) => `${p.type} ${p.points}`).join(', ') : '')}>
                  <Score v={r.score} /></td>
                <td title={r.price_indicative ? 'Indicative bid/ask mid — no fresh trade print yet; BUY stays blocked until one prints' : undefined}>
                  {r.price_indicative ? '~' : ''}{fmtPrice(r.price)} <Freshness ts={r.provider_ts} fresh={r.quote_fresh || r.price_indicative} /></td>
                <td className={(r.gap_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(r.gap_pct)}</td>
                <td>{r.rvol != null ? <>{fmtNum(r.rvol, 1)}x {r.rvol_estimated && <span className="badge est" title="Estimated vs avg daily volume curve — baseline history still accumulating">EST</span>}</> : <span className="faint" title="Baseline coverage insufficient">—</span>}</td>
                <td>{fmtCompact(r.pm_volume)}</td>
                <td>{r.pm_dollar_volume != null ? '$' + fmtCompact(r.pm_dollar_volume) : '—'}</td>
                <td>{fmtCompact(r.float_shares)}{r.float_shares != null && r.float_shares < 10_000_000 && <span className="badge lowfloat" title="Float under 10M shares — prone to explosive moves">LOW</span>}</td>
                <td className={(r as any).float_rotation >= 0.2 ? 'pos' : ''}>{(r as any).float_rotation != null ? ((r as any).float_rotation * 100).toFixed(1) + '%' : '—'}</td>
                <td>{r.market_cap != null ? '$' + fmtCompact(r.market_cap) : '—'}</td>
                <td className={r.spread_pct != null && r.spread_pct > 5 ? 'neg' : ''}>{fmtPct(r.spread_pct, false)}</td>
                <td className="l">
                  {r.catalyst_type
                    ? <span className={`badge ${r.catalyst_direction === 'positive' ? 'buy' : r.catalyst_direction === 'negative' ? 'risk' : 'neutral'}`} title={r.catalyst_summary || undefined}>{r.catalyst_type}</span>
                    : <span className="faint">none</span>}
                  {(r.catalyst_sources?.news ?? 0) > 0 && <span className="badge src" title={`${r.catalyst_sources!.news} news item(s) found for this symbol`}>news ×{r.catalyst_sources!.news}</span>}
                </td>
                <td className="l" style={{ fontSize: 11 }}>
                  {r.filing_links?.length
                    ? r.filing_links.slice(0, 3).map((f, i) => (
                        <a key={i} className="badge src link" href={f.url} target="_blank" rel="noreferrer"
                           onClick={(e) => e.stopPropagation()} title="Open on SEC EDGAR">{f.form}↗</a>
                      ))
                    : <span className="faint">{r.filing_forms?.slice(0, 3).join(' · ') || '—'}</span>}
                </td>
                <td className="l" style={{ maxWidth: 200 }}>
                  {r.hard_blocks?.length
                    ? <span className="badge risk" title={'Hard blocks (BUY impossible): ' + r.hard_blocks.join(', ')}>blocked</span>
                    : r.gates_failed?.length
                      ? <span className="badge warn" title={(r.gate_reasons ?? r.gates_failed).join('\n')}>gated</span>
                      : r.buy ? <span className="badge buy">qualified</span>
                      : r.early ? <span className="badge early">early watch</span>
                      : <span className="badge neutral">watching</span>}
                  {(r.gate_reasons?.length || r.hard_blocks?.length) ? (
                    <div className="gate-why">{(r.hard_blocks?.length ? r.hard_blocks.map((b) => b.replace(/_/g, ' ')) : r.gate_reasons)?.slice(0, 2).join(' · ')}</div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
