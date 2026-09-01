'use client';
import { useState } from 'react';
import { API_BASE, API_KEY, usePolling } from '../../lib/api';
import DetailDrawer from '../../components/DetailDrawer';
import { fmtEtDate, fmtPrice } from '../../lib/format';

const H = { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) };

export default function WatchlistsPage() {
  const [wl, , reloadWl] = usePolling<{ rows: any[] }>('/api/watchlists', 60000);
  const [alerts, , reloadAl] = usePolling<{ rows: any[] }>('/api/alerts', 30000);
  const [sym, setSym] = useState('');
  const [aSym, setASym] = useState(''); const [aPx, setAPx] = useState('');
  const [aCond, setACond] = useState<'above' | 'below'>('above');
  const [sel, setSel] = useState<string | null>(null);
  const list = wl?.rows?.[0];
  const addSym = async () => {
    if (!list || !sym.trim()) return;
    await fetch(`${API_BASE}/api/watchlists/${list.id}`, { method: 'PUT', headers: H,
      body: JSON.stringify({ symbols: [...list.symbols, sym.toUpperCase()] }) });
    setSym(''); reloadWl();
  };
  const rmSym = async (s: string) => {
    if (!list) return;
    await fetch(`${API_BASE}/api/watchlists/${list.id}`, { method: 'PUT', headers: H,
      body: JSON.stringify({ symbols: list.symbols.filter((x: string) => x !== s) }) });
    reloadWl();
  };
  const addAlert = async () => {
    if (!aSym.trim() || !aPx.trim()) return;
    await fetch(`${API_BASE}/api/alerts`, { method: 'POST', headers: H,
      body: JSON.stringify({ symbol: aSym, condition: aCond, price: Number(aPx) }) });
    setASym(''); setAPx(''); reloadAl();
  };
  return (
    <>
      <div className="sect"><h2>Watchlists &amp; Alerts</h2>
        <span className="meta">in-app price alerts checked every tracking cycle — no SMS/email dependencies</span></div>
      <div className="cards" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="card" style={{ minHeight: 200 }}>
          <h3>{list?.name ?? 'Watchlist'}</h3>
          <div style={{ display: 'flex', gap: 6, margin: '8px 0' }}>
            <input placeholder="Add symbol…" value={sym} onChange={(e) => setSym(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addSym()}
              style={{ flex: 1, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '6px 10px', fontFamily: 'var(--mono)' }} />
            <button className="btn" onClick={addSym}>Add</button>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {(list?.symbols ?? []).map((s: string) => (
              <span key={s} className="badge neutral" style={{ cursor: 'pointer', fontSize: 12 }}>
                <span onClick={() => setSel(s)}>{s}</span>
                <span onClick={() => rmSym(s)} style={{ marginLeft: 5, color: 'var(--risk)' }}>✕</span>
              </span>
            ))}
            {!(list?.symbols ?? []).length && <span className="faint">empty — add symbols to track them here</span>}
          </div>
        </div>
        <div className="card" style={{ minHeight: 200 }}>
          <h3>Price alerts</h3>
          <div style={{ display: 'flex', gap: 6, margin: '8px 0', flexWrap: 'wrap' }}>
            <input placeholder="SYM" value={aSym} onChange={(e) => setASym(e.target.value)}
              style={{ width: 80, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '6px 8px', fontFamily: 'var(--mono)' }} />
            <select value={aCond} onChange={(e) => setACond(e.target.value as any)}
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '6px 8px' }}>
              <option value="above">rises above</option><option value="below">falls below</option>
            </select>
            <input placeholder="price" value={aPx} onChange={(e) => setAPx(e.target.value)} inputMode="decimal"
              style={{ width: 90, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '6px 8px', fontFamily: 'var(--mono)' }} />
            <button className="btn" onClick={addAlert}>Set</button>
          </div>
          {(alerts?.rows ?? []).filter((a) => a.active).map((a) => (
            <div key={a.id} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '4px 0', alignItems: 'center' }}>
              <span className="sym">{a.symbol}</span>
              <span className="dim">{a.condition} {fmtPrice(a.price)}</span>
              {a.fired_at
                ? <span className="badge buy" title={`fired ${fmtEtDate(a.fired_at)} @ ${fmtPrice(a.fired_price)}`}>FIRED @{fmtPrice(a.fired_price)}</span>
                : <span className="badge warn">armed</span>}
              <span className="spacer" style={{ flex: 1 }} />
              <button className="btn" style={{ padding: '2px 8px', fontSize: 10 }}
                onClick={async () => { await fetch(`${API_BASE}/api/alerts/${a.id}`, { method: 'DELETE', headers: H }); reloadAl(); }}>remove</button>
            </div>
          ))}
        </div>
      </div>
      {sel && <DetailDrawer symbol={sel} onClose={() => setSel(null)} />}
    </>
  );
}
