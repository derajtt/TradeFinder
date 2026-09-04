'use client';
import { useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { EmptyState, SectionHeader, StatusPill } from '../../components/ui';
import { apiDelete, apiPostBody, apiPut, usePollingState } from '../../lib/api';
import { fmtEtShort, fmtPrice } from '../../lib/format';
import { useAppSettings } from '../../lib/status';
import type { AlertRow, WatchlistRow } from '../../lib/types';

export default function WatchlistsPage() {
  const wl = usePollingState<{ rows: WatchlistRow[] }>('/api/watchlists', 60000);
  const al = usePollingState<{ rows: AlertRow[] }>('/api/alerts', 30000);
  const { settings } = useAppSettings();
  const [listId, setListId] = useState<number | null>(null);
  const [sym, setSym] = useState('');
  const [aSym, setASym] = useState('');
  const [aPx, setAPx] = useState('');
  const [aCond, setACond] = useState<'above' | 'below'>('above');
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const lists = wl.data?.rows ?? [];
  const list = lists.find((l) => l.id === listId) ?? lists[0] ?? null;
  const alerts = (al.data?.rows ?? []).filter((a) => a.active);
  const every = settings?.scan_interval_sec;

  const run = async (fn: () => Promise<unknown>, after: () => void) => {
    setErr(null);
    try { await fn(); after(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const addSym = () => {
    const s = sym.trim().toUpperCase();
    if (!list || !s) return;
    if (list.symbols.includes(s)) { setSym(''); return; }
    run(() => apiPut(`/api/watchlists/${list.id}`, { symbols: [...list.symbols, s] }), () => { setSym(''); wl.reload(); });
  };
  const rmSym = (s: string) => {
    if (!list) return;
    run(() => apiPut(`/api/watchlists/${list.id}`, { symbols: list.symbols.filter((x) => x !== s) }), () => wl.reload());
  };
  const addAlert = () => {
    const s = aSym.trim().toUpperCase();
    const p = Number(aPx);
    if (!s || !aPx.trim() || !(p > 0)) return;
    run(() => apiPostBody('/api/alerts', { symbol: s, condition: aCond, price: p }), () => { setASym(''); setAPx(''); al.reload(); });
  };
  const rmAlert = (id: number) => run(() => apiDelete(`/api/alerts/${id}`), () => al.reload());

  return (
    <>
      <SectionHeader level={1} title="My watchlist & alerts"
        question="Which stocks am I following, and which price alerts are set?"
        caption={`In-app price alerts, checked every scan${every ? ` (about every ${every}s while the market is open)` : ''} — no SMS or email.`} />
      {err ? <div className="err-box">{err}</div> : null}

      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))' }}>
        <section className="card" style={{ minHeight: 200 }}>
          <div className="row" style={{ gap: 10, justifyContent: 'space-between' }}>
            <h3 style={{ margin: 0 }}>{list?.name ?? 'Watchlist'}</h3>
            {lists.length > 1 ? (
              <label className="switch">
                <span>List</span>
                <select className="input sans" value={list?.id ?? ''} onChange={(e) => setListId(Number(e.target.value))} aria-label="Watchlist">
                  {lists.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </label>
            ) : null}
          </div>
          <form className="row" style={{ gap: 8, margin: '8px 0', alignItems: 'flex-end' }}
            onSubmit={(e) => { e.preventDefault(); addSym(); }}>
            <div className="field" style={{ flex: 1 }}>
              <label htmlFor="wl-symbol">Symbol</label>
              <input id="wl-symbol" value={sym} onChange={(e) => setSym(e.target.value)} placeholder="e.g. NVDA" autoComplete="off" />
            </div>
            <button type="submit" className="btn" disabled={!list || !sym.trim()}>Add</button>
          </form>
          {!wl.loaded ? (
            <EmptyState compact loaded={false} headline="Loading watchlist" reason={null} />
          ) : !list ? (
            <EmptyState compact headline="No watchlist" reason={wl.err?.message ?? 'The server returned no watchlist rows.'} />
          ) : list.symbols.length === 0 ? (
            <EmptyState compact headline="Empty watchlist" reason="No symbols have been added yet." next="Add a symbol above to follow it here." />
          ) : (
            <div className="chips">
              {list.symbols.map((s) => (
                <span key={s} className="chip" style={{ gap: 4 }}>
                  <button type="button" className="chip-btn" onClick={() => setSel(s)}>{s}</button>
                  <button type="button" className="chip-x" aria-label={`Remove ${s}`} onClick={() => rmSym(s)}>✕</button>
                </span>
              ))}
            </div>
          )}
        </section>

        <section className="card" style={{ minHeight: 200 }}>
          <h3>Price alerts</h3>
          <form className="row" style={{ gap: 8, margin: '8px 0', alignItems: 'flex-end' }}
            onSubmit={(e) => { e.preventDefault(); addAlert(); }}>
            <div className="field" style={{ width: 110 }}>
              <label htmlFor="al-symbol">Symbol</label>
              <input id="al-symbol" value={aSym} onChange={(e) => setASym(e.target.value)} placeholder="e.g. SPY" autoComplete="off" />
            </div>
            <div className="field">
              <label htmlFor="al-cond">Alert me when price…</label>
              <select id="al-cond" value={aCond} onChange={(e) => setACond(e.target.value as 'above' | 'below')}>
                <option value="above">rises above</option>
                <option value="below">falls below</option>
              </select>
            </div>
            <div className="field" style={{ width: 120 }}>
              <label htmlFor="al-price">Price</label>
              <input id="al-price" value={aPx} onChange={(e) => setAPx(e.target.value)} inputMode="decimal" placeholder="0.00" />
            </div>
            <button type="submit" className="btn" disabled={!aSym.trim() || !(Number(aPx) > 0)}>Set alert</button>
          </form>
          {!al.loaded ? (
            <EmptyState compact loaded={false} headline="Loading alerts" reason={null} />
          ) : alerts.length === 0 ? (
            <EmptyState compact headline="No alerts yet — set one above" reason="You have not set any price alerts." />
          ) : (
            <div className="timeline" style={{ gap: 6 }}>
              {alerts.map((a) => (
                <div key={a.id} className="tl-item" style={{ alignItems: 'center', padding: '8px 12px' }}>
                  <span className="sym">{a.symbol}</span>
                  <span className="dim">{a.condition === 'above' ? 'rises above' : a.condition === 'below' ? 'falls below' : a.condition} {fmtPrice(a.price)}</span>
                  {a.fired_at
                    ? <StatusPill size="sm" tone="buy" label={`Fired ${fmtEtShort(a.fired_at)} at ${fmtPrice(a.fired_price)}`} />
                    : <StatusPill size="sm" tone="neutral" label="Armed" />}
                  <span style={{ flex: 1 }} />
                  <button type="button" className="btn sm" onClick={() => rmAlert(a.id)}>Remove</button>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
      {sel ? <DetailDrawer symbol={sel} onClose={() => setSel(null)} /> : null}

      <style href="tf-watchlists" precedence="default">{`
        .chip-btn { background: none; border: none; padding: 0; color: inherit; font: inherit; cursor: pointer; }
        .chip-btn:hover { color: var(--accent); }
        .chip-x { background: none; border: none; padding: 0 2px; color: var(--text-faint); font-size: var(--fs-note); cursor: pointer; line-height: 1; }
        .chip-x:hover { color: var(--risk); }
      `}</style>
    </>
  );
}
