'use client';
import { usePolling } from '../lib/api';
import { fmtPrice } from '../lib/format';

interface Noon { policy: string; counts: Record<string, number>;
  call_win_rate: number | null; win_rate_lb: number | null; denominator: number;
  note: string; rows: { symbol: string; class: string; call_price: number;
    reference: number | null; quality: string }[]; }

const CLS: Record<string, [string, string]> = {
  WIN_10_TOUCH: ['+10% touch', 'buy'], WIN_NOON_GREEN: ['noon green', 'buy'],
  LOSS_NOON_RED: ['noon red', 'risk'], FLAT: ['flat', 'neutral'],
  INCOMPLETE: ['incomplete', 'neutral'],
};

export default function NoonCard() {
  const [n] = usePolling<Noon>('/api/outcomes/noon', 60000);
  if (!n || !n.rows?.length) return null;
  return (
    <>
      <div className="sect" style={{ marginTop: 30 }}>
        <h2>Locked Noon Outcomes</h2>
        <span className="meta">{n.policy} — call accuracy from the immutable call price, 7:00 AM→noon; separate from paper P&amp;L</span>
      </div>
      <div className="tbl-wrap" style={{ padding: '12px 16px' }}>
        <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center', fontSize: 12.5 }}>
          <span className="tb-item">call win rate <b>{n.call_win_rate != null ? (n.call_win_rate * 100).toFixed(0) + '%' : '—'}</b>
            {n.win_rate_lb != null && <span className="faint">(LB {(n.win_rate_lb * 100).toFixed(0)}%)</span>}
            <span className="faint">n={n.denominator}</span></span>
          {Object.entries(n.counts).filter(([, v]) => v > 0).map(([k, v]) => (
            <span key={k} className={`badge ${CLS[k]?.[1] ?? 'neutral'}`}>{CLS[k]?.[0] ?? k} ×{v}</span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
          {n.rows.slice(0, 12).map((r, i) => (
            <span key={i} className={`badge ${CLS[r.class]?.[1] ?? 'neutral'}`}
              title={`call ${fmtPrice(r.call_price)} → ref ${fmtPrice(r.reference)} (${r.quality})`}>
              {r.symbol} · {CLS[r.class]?.[0] ?? r.class}
            </span>
          ))}
        </div>
        <p className="faint" style={{ fontSize: 10.5, marginTop: 8 }}>{n.note}</p>
      </div>
    </>
  );
}
