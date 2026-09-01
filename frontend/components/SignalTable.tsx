'use client';
import { fmtEtDate, fmtPct, fmtPrice } from '../lib/format';
import Freshness from './Freshness';
import type { SignalRow } from '../lib/types';
import { TERMS } from '../lib/terms';
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
          <th className="l" title={TERMS.signal_type}>Symbol</th>
          <th title={TERMS.score}>Score</th>
          <th title={TERMS.buy_price}>Found @</th>
          <th title={TERMS.current}>Current</th>
          <th title="The paper sell plan recorded at signal time: protective stop / first target (50% off + stop to breakeven) / second target">Stop · T1 · T2</th>
          <th title={TERMS.change}>Change</th>
          <th title={TERMS.day_hilo}>Day Hi/Lo</th>
          <th title={TERMS.since_hilo}>Since Hi/Lo</th>
          <th title={TERMS.max_gain}>Max Gain</th>
          <th title={TERMS.max_dd}>Max DD</th>
          <th className="l" title={TERMS.outcome}>Result</th>
          {!compact && <th className="l">Catalyst</th>}
          <th className="l">Initiated</th><th className="l">Status</th>
        </tr></thead>
        <tbody>
          {rows.map((s) => {
            const chg = s.change_pct;
            return (
              <tr key={s.signal_uid} onClick={() => onSelect(s)} tabIndex={0} role="button"
                  style={s.status === 'invalidated' ? { opacity: 0.38 } : undefined}
                  title={s.status === 'invalidated' ? 'Invalidated (duplicate) — prices frozen, excluded from the scoreboard' : undefined}
                  onKeyDown={(e) => e.key === 'Enter' && onSelect(s)}>
                <td className="l"><span className="sym">{s.symbol}</span>
                  <span className={`badge ${s.signal_type === 'watch' ? 'early' : 'buy'}`}
                        style={{ marginLeft: 6 }}
                        title={TERMS.signal_type}>{s.signal_type === 'watch' ? 'WATCH' : 'BUY'}</span>
                  {s.is_demo && <span className="badge warn" style={{ marginLeft: 6 }}>DEMO</span>}</td>
                <td><Score v={s.score} /></td>
                <td title={`Immutable initiation price · ${s.price_source}`}><b>{fmtPrice(s.buy_price)}</b></td>
                <td>{fmtPrice(s.current)} <Freshness ts={s.current_ts} /></td>
                <td style={{ fontSize: 11.5 }}>
                  <span className="neg">{fmtPrice(s.stop)}</span>
                  <span className="faint"> · </span>
                  <span className="pos">{fmtPrice(s.target1)}</span>
                  <span className="faint"> · </span>
                  <span className="pos">{fmtPrice(s.target2)}</span>
                </td>
                <td className={chg == null ? '' : chg >= 0 ? 'pos' : 'neg'}>{fmtPct(chg)}</td>
                <td className="dim">{fmtPrice(s.day_high)} / {fmtPrice(s.day_low)}</td>
                <td className="dim">{fmtPrice(s.since_high)} / {fmtPrice(s.since_low)}</td>
                <td className="pos">{fmtPct(s.max_gain_pct)}</td>
                <td className="neg">{fmtPct(s.max_drawdown_pct)}</td>
                <td className="l">
                  {s.outcome === 'win' && <span className="badge buy">WIN</span>}
                  {s.outcome === 'loss' && <span className="badge risk">LOSS</span>}
                  {s.outcome === 'neutral' && <span className="badge neutral">NEUTRAL</span>}
                  {(s.outcome === 'pending' || !s.outcome) && <span className="badge neutral" style={{ opacity: 0.6 }}>pending</span>}
                </td>
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
