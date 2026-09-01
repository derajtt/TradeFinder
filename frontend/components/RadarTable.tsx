'use client';
import { useMemo, useState } from 'react';
import { fmtCompact, fmtPct, fmtPrice } from '../lib/format';

export interface RadarRow {
  symbol: string; name: string; exchange: string;
  price: number | null; gap_pct: number | null; volume: number | null;
  market_cap: number | null; has_news: boolean; provider_ts: string | null;
}

export default function RadarTable({ rows, onSelect }: {
  rows: RadarRow[]; onSelect: (symbol: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = useMemo(() => (showAll ? rows : rows.slice(0, 24)), [rows, showAll]);
  if (!rows.length) return null;
  return (
    <>
      <div className="sect" style={{ marginTop: 34 }}>
        <h2>Radar</h2>
        <span className="meta">
          {rows.length} more movers across the full exchange universe — next in line for enrichment
        </span>
        <span className="spacer" />
        {rows.length > 24 && (
          <button className="btn" onClick={() => setShowAll((s) => !s)}>
            {showAll ? 'Show top 24' : `Show all ${rows.length}`}
          </button>
        )}
      </div>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 760 }}>
          <thead><tr>
            <th className="l">Symbol</th><th>Price</th><th>Gap%</th>
            <th>Day Vol</th><th>Mkt Cap</th><th className="l">News</th><th className="l">Exch</th>
          </tr></thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.symbol} onClick={() => onSelect(r.symbol)} tabIndex={0} role="button"
                  onKeyDown={(e) => e.key === 'Enter' && onSelect(r.symbol)}
                  aria-label={`Open ${r.symbol} details`}>
                <td className="l">
                  <span className="sym">{r.symbol}</span>
                  <div className="co-name">{r.name}</div>
                </td>
                <td>{fmtPrice(r.price)}</td>
                <td className={(r.gap_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(r.gap_pct)}</td>
                <td>{fmtCompact(r.volume)}</td>
                <td>{r.market_cap != null ? '$' + fmtCompact(r.market_cap) : '—'}</td>
                <td className="l">{r.has_news ? <span className="badge src">news</span> : <span className="faint">—</span>}</td>
                <td className="l faint" style={{ fontSize: 11 }}>{r.exchange}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
