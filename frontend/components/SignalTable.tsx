'use client';
import { fmtEtDate, fmtPct, fmtPrice } from '../lib/format';
import type { SignalRow } from '../lib/types';
import Score from './Score';

export default function SignalTable({ rows, onSelect, compact }: {
  rows: SignalRow[]; onSelect: (r: SignalRow) => void; compact?: boolean;
}) {
  if (!rows.length) {
    return (
      <div className="tbl-wrap"><div className="empty">
        <b>No BUY signals yet</b>
        A signal is created only when every gate passes — score, catalyst, RVOL, freshness, spread and risk checks.
      </div></div>
    );
  }
  return (
    <div className="tbl-wrap">
      <table className="tbl">
        <thead><tr>
          <th className="l">Symbol</th><th>Score</th><th>BUY Price</th><th>Current</th>
          <th>Change</th><th>Day Hi/Lo</th><th>Since Hi/Lo</th>
          <th>Max Gain</th><th>Max DD</th>
          {!compact && <th className="l">Catalyst</th>}
          <th className="l">Initiated</th><th className="l">Status</th>
        </tr></thead>
        <tbody>
          {rows.map((s) => {
            const chg = s.change_pct;
            return (
              <tr key={s.signal_uid} onClick={() => onSelect(s)} tabIndex={0} role="button"
                  onKeyDown={(e) => e.key === 'Enter' && onSelect(s)}>
                <td className="l"><span className="sym">{s.symbol}</span>
                  {s.is_demo && <span className="badge warn" style={{ marginLeft: 6 }}>DEMO</span>}</td>
                <td><Score v={s.score} /></td>
                <td title={`Immutable initiation price · ${s.price_source}`}><b>{fmtPrice(s.buy_price)}</b></td>
                <td>{fmtPrice(s.current)}</td>
                <td className={chg == null ? '' : chg >= 0 ? 'pos' : 'neg'}>{fmtPct(chg)}</td>
                <td className="dim">{fmtPrice(s.day_high)} / {fmtPrice(s.day_low)}</td>
                <td className="dim">{fmtPrice(s.since_high)} / {fmtPrice(s.since_low)}</td>
                <td className="pos">{fmtPct(s.max_gain_pct)}</td>
                <td className="neg">{fmtPct(s.max_drawdown_pct)}</td>
                {!compact && <td className="l">{s.catalyst_type ? <span className="badge neutral">{s.catalyst_type}</span> : <span className="faint">—</span>}</td>}
                <td className="l dim" style={{ fontSize: 12 }}>{fmtEtDate(s.initiated_at)}</td>
                <td className="l"><span className={`badge ${s.status === 'active' ? 'buy' : 'neutral'}`}>{s.status}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
