'use client';
import { useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { usePolling } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';

export default function FeedPage() {
  const [form, setForm] = useState('');
  const [symbol, setSymbol] = useState('');
  const [sort, setSort] = useState<'time' | 'symbol' | 'kind'>('time');
  const [kind, setKind] = useState<'' | 'news' | 'filing'>('');
  const [resp] = usePolling<{ rows: any[]; forms: string[] }>(
    `/api/feed?form=${form}&symbol=${symbol}&sort=${sort}&kind=${kind}`, 60000);
  const [sel, setSel] = useState<string | null>(null);
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
          <button key={k} className={`tab ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>{l}</button>
        ))}
        <span className="meta" style={{ marginLeft: 6 }}>Sort</span>
        {(['time', 'symbol', 'kind'] as const).map((k) => (
          <button key={k} className={`tab ${sort === k ? 'on' : ''}`} onClick={() => setSort(k)}>{k}</button>
        ))}
        <span className="meta" style={{ marginLeft: 'auto' }}>{(resp?.rows ?? []).length} items</span>
      </div>
      <div className="timeline">
        {(resp?.rows ?? []).map((r, i) => (
          <div className="tl-item" key={i}>
            <span className="tl-time">{fmtEtDate(r.ts)}</span>
            <span className={`badge ${r.kind === 'filing' ? 'neutral' : 'src'}`}>{r.kind === 'filing' ? r.form : 'news'}</span>
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
