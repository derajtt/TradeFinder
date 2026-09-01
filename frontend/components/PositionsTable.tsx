'use client';
import { usePolling } from '../lib/api';
import { fmtEtDate, fmtPrice } from '../lib/format';

interface Pos {
  symbol: string; status: string; opened_at: string; entry_fill: number;
  stop: number | null; target1: number | null; target2: number | null;
  remaining_frac: number; realized_r: number; exit_reason: string;
  closed_at: string | null; strategy_version: string;
}

export default function PositionsTable({ onSelect, profile = '' }: { onSelect: (s: string) => void; profile?: string }) {
  const [resp] = usePolling<{ rows: Pos[] }>(`/api/positions?profile=${profile}`, 20000);
  const rows = resp?.rows ?? [];
  return (
    <>
      <div className="sect"><h2>Paper Positions</h2>
        <span className="meta">primary frozen policy · entries at ask+slippage, exits at bid−slippage · never an order</span></div>
      {rows.length === 0 ? (
        <div className="tbl-wrap"><div className="empty"><b>No paper positions yet</b>
          A position opens automatically when a signal reaches ACTIONABLE BUY with a simulated fill.</div></div>
      ) : (
        <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 760 }}>
          <thead><tr>
            <th className="l">Symbol</th><th className="l">Status</th><th>Entry fill</th>
            <th>Stop</th><th>T1/T2</th><th>Remaining</th><th>Realized R</th>
            <th className="l">Exit reason</th><th className="l">Opened</th>
          </tr></thead>
          <tbody>{rows.map((p, i) => (
            <tr key={i} onClick={() => onSelect(p.symbol)}>
              <td className="l"><span className="sym">{p.symbol}</span></td>
              <td className="l"><span className={`badge ${p.status === 'open' ? 'buy' : 'neutral'}`}>{p.status}</span></td>
              <td>{fmtPrice(p.entry_fill)}</td>
              <td className="dim">{fmtPrice(p.stop)}</td>
              <td className="dim">{fmtPrice(p.target1)} / {fmtPrice(p.target2)}</td>
              <td>{(p.remaining_frac * 100).toFixed(0)}%</td>
              <td className={p.realized_r >= 0 ? 'pos' : 'neg'}>{p.realized_r.toFixed(2)}R</td>
              <td className="l dim" style={{ fontSize: 11 }}>{p.exit_reason || '—'}</td>
              <td className="l dim" style={{ fontSize: 11 }}>{fmtEtDate(p.opened_at)}</td>
            </tr>))}</tbody>
        </table></div>
      )}
    </>
  );
}
