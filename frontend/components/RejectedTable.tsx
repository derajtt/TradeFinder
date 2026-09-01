'use client';
import { useState } from 'react';
import { usePolling } from '../lib/api';
import { fmtPct, fmtPrice } from '../lib/format';

interface Rej {
  symbol: string; session_date: string; rejected_at: string; reason: string;
  failed_gates: string[]; price: number | null; score: number | null;
  shadow_high: number | null; shadow_low: number | null;
  missed_move_pct: number | null;
}

export default function RejectedTable({ onSelect }: { onSelect: (s: string) => void }) {
  const [resp] = usePolling<{ rows: Rej[] }>('/api/rejected', 60000);
  const [open, setOpen] = useState(false);
  const rows = resp?.rows ?? [];
  if (!rows.length) return null;
  return (
    <>
      <div className="sect" style={{ marginTop: 30 }}>
        <h2>Rejected Candidates</h2>
        <span className="meta">shadow-tracked false-negative log — never converted into signals retroactively</span>
        <span className="spacer" />
        <button className="btn" onClick={() => setOpen((o) => !o)}>{open ? 'Hide' : `Show ${rows.length}`}</button>
      </div>
      {open && (
        <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 820 }}>
          <thead><tr>
            <th className="l">Symbol</th><th>Price@reject</th><th>Score</th>
            <th className="l">Failed gates</th><th title="Highest price seen after rejection — did the filter cost us a winner?">Shadow high</th>
            <th>Missed move</th>
          </tr></thead>
          <tbody>{rows.slice(0, 60).map((r, i) => (
            <tr key={i} onClick={() => onSelect(r.symbol)}>
              <td className="l"><span className="sym">{r.symbol}</span>
                <div className="co-name">{r.session_date}</div></td>
              <td>{fmtPrice(r.price)}</td>
              <td>{r.score?.toFixed(0) ?? '—'}</td>
              <td className="l" style={{ fontSize: 11, whiteSpace: 'normal', maxWidth: 320 }}>
                {r.failed_gates.map((g, j) => <span key={j} className="badge risk" style={{ margin: '1px 3px 1px 0', fontSize: 9 }}>{g.replace(/_/g, ' ')}</span>)}
              </td>
              <td className="dim">{fmtPrice(r.shadow_high)}</td>
              <td className={r.missed_move_pct != null && r.missed_move_pct > 10 ? 'warn-txt pos' : 'dim'}>{fmtPct(r.missed_move_pct)}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </>
  );
}
