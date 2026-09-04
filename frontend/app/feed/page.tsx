'use client';
import { useEffect, useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { API_BASE, usePolling, withKey } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';

export default function FeedPage() {
  const [form, setForm] = useState('');
  const [symbol, setSymbol] = useState('');
  const [sort, setSort] = useState<'time' | 'symbol' | 'kind'>('time');
  const [kind, setKind] = useState<'' | 'news' | 'filing'>('');
  // Picking a form means "show me those filings" — mixing 80 news rows back in
  // made the click look like it did nothing.
  const effKind = form ? 'filing' : kind;
  const [resp, , refetch] = usePolling<{ rows: any[]; forms: string[] }>(
    `/api/feed?form=${form}&symbol=${symbol}&sort=${sort}&kind=${effKind}`, 30000);
  const [sel, setSel] = useState<string | null>(null);
  const [lastLive, setLastLive] = useState<{ t: number; symbol: string; form: string } | null>(null);
  // Live: the backend polls EDGAR's newest-filings feed every ~20s and pushes
  // each new filing over the event stream; refetch the moment one arrives.
  useEffect(() => {
    let es: EventSource | null = null;
    try {
      es = new EventSource(withKey(`${API_BASE}/api/stream`));
      es.addEventListener('filing', (ev: MessageEvent) => {
        try { const d = JSON.parse(ev.data); setLastLive({ t: Date.now(), symbol: d.symbol, form: d.form }); } catch { /* ignore */ }
        refetch();
      });
    } catch { /* no SSE in this environment */ }
    return () => { es?.close(); };
  }, [refetch]);
  return (
    <>
      <div className="sect"><h2>News &amp; Filings</h2>
        <span className="meta">unified stream with source timestamps — news publication time, SEC acceptance time</span></div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <input placeholder="Symbol filter…" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          style={{ width: 140, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '7px 10px', fontFamily: 'var(--mono)' }} />
        <select value={form} onChange={(e) => setForm(e.target.value)}
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '7px 10px' }}>
          <option value="">All SEC forms</option>
          {(resp?.forms ?? []).map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <span className="meta" style={{ marginLeft: 6 }}>Show</span>
        {([['', 'All'], ['news', 'News'], ['filing', 'Filings']] as const).map(([k, l]) => (
          <button key={k} className={`tab ${effKind === k ? 'on' : ''}`} disabled={!!form && k !== 'filing'}
            title={form ? 'A form filter shows filings only' : undefined} onClick={() => setKind(k)}>{l}</button>
        ))}
        <span className="meta" style={{ marginLeft: 6 }}>Sort</span>
        {(['time', 'symbol', 'kind'] as const).map((k) => (
          <button key={k} className={`tab ${sort === k ? 'on' : ''}`} onClick={() => setSort(k)}>{k}</button>
        ))}
        <span className="meta" style={{ marginLeft: 'auto' }}>
          <span className="st st-live" title="EDGAR newest-filings feed, polled every ~20s; new filings push instantly">LIVE</span>
          {lastLive && <span style={{ marginLeft: 8 }}>last: {lastLive.form} {lastLive.symbol} · {Math.round((Date.now() - lastLive.t) / 1000)}s ago</span>}
          <span style={{ marginLeft: 8 }}>{(resp?.rows ?? []).length} items</span>
        </span>
      </div>
      <div className="timeline">
        {(resp?.rows ?? []).map((r, i) => (
          <div className="tl-item" key={i}>
            <span className="tl-time">{fmtEtDate(r.ts)}</span>
            <span className={`badge ${r.kind === 'filing' ? 'neutral' : 'src'}`}>{r.kind === 'filing' ? r.form : 'news'}</span>
            {r.kind === 'filing' && r.items ? <span className="faint" style={{ fontSize: 10.5, marginRight: 6 }} title="8-K item codes">items {r.items}</span> : null}
            <span style={{ flex: 1 }}>
              <span className="sym" style={{ cursor: 'pointer', marginRight: 8 }} onClick={() => setSel(r.symbol)}>{r.symbol}</span>
              <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
              <span className="faint"> — {r.source}</span>
            </span>
          </div>
        ))}
        {!(resp?.rows ?? []).length && <div className="empty"><b>No items match</b></div>}
      </div>
      {sel && <DetailDrawer symbol={sel} onClose={() => setSel(null)} />}
    </>
  );
}
