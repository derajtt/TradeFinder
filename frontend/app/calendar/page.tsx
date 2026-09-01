'use client';
import { usePolling } from '../../lib/api';

export default function CalendarPage() {
  const [c] = usePolling<any>('/api/calendar', 300000);
  if (!c) return <div className="skel" style={{ height: 300, marginTop: 20 }} />;
  const byDate: Record<string, any[]> = {};
  for (const e of c.earnings ?? []) byDate[e.date] = [...(byDate[e.date] ?? []), e];
  return (
    <>
      <div className="sect"><h2>Market Calendar</h2>
        <span className="meta">earnings next 7 days · exchange holidays &amp; half-days (scanner adjusts automatically)</span></div>
      <div className="cards" style={{ gridTemplateColumns: '2fr 1fr' }}>
        <div className="card">
          <h3>Earnings (next 7 days)</h3>
          {Object.entries(byDate).slice(0, 7).map(([d, rows]) => (
            <div key={d} style={{ margin: '6px 0' }}>
              <b className="dim" style={{ fontSize: 11 }}>{d}</b>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 3 }}>
                {rows.slice(0, 20).map((e: any, i: number) => (
                  <span key={i} className="badge neutral" title={`EPS est ${e.eps_est ?? '—'}`}>{e.symbol}</span>
                ))}
                {rows.length > 20 && <span className="faint" style={{ fontSize: 10 }}>+{rows.length - 20} more</span>}
              </div>
            </div>
          ))}
          {!Object.keys(byDate).length && <div className="faint">no earnings data in window{c.earnings_quality ? ` (${c.earnings_quality})` : ''}</div>}
        </div>
        <div className="card">
          <h3>Market closures</h3>
          {(c.holidays ?? []).map((h: string) => <div key={h} className="dim" style={{ fontSize: 12.5, padding: '2px 0' }}>🛑 {h} — closed</div>)}
          {(c.half_days ?? []).map((h: string) => <div key={h} className="dim" style={{ fontSize: 12.5, padding: '2px 0' }}>🕐 {h} — 1:00 PM close</div>)}
        </div>
      </div>
    </>
  );
}
