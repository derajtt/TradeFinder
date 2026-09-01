'use client';
import { useState } from 'react';
import { apiGet, usePolling } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';
import { API_BASE, API_KEY } from '../../lib/api';

interface Entry { id: number; created_at: string; symbol: string; signal_uid: string;
  note: string; tags: string[]; rules_followed: boolean; review: string; }

export default function JournalPage() {
  const [resp, , reload] = usePolling<{ rows: Entry[] }>('/api/journal', 60000);
  const [note, setNote] = useState('');
  const [symbol, setSymbol] = useState('');
  const [tags, setTags] = useState('');
  const [rules, setRules] = useState(true);
  const save = async () => {
    if (!note.trim()) return;
    await fetch(`${API_BASE}/api/journal`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) },
      body: JSON.stringify({ note, symbol, rules_followed: rules,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean) }) });
    setNote(''); setSymbol(''); setTags(''); reload();
  };
  return (
    <>
      <div className="sect"><h2>Trade Journal</h2>
        <span className="meta">notes joined to signals and symbols — review what you did vs what the rules said</span></div>
      <div className="tbl-wrap" style={{ padding: '14px 16px', marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input placeholder="Symbol (optional)" value={symbol} onChange={(e) => setSymbol(e.target.value)}
            style={{ width: 120, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '7px 10px' }} />
          <input placeholder="tags, comma,separated" value={tags} onChange={(e) => setTags(e.target.value)}
            style={{ width: 200, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '7px 10px' }} />
          <label className="check" style={{ padding: 0 }}>
            <input type="checkbox" checked={rules} onChange={(e) => setRules(e.target.checked)} />
            <span className="dim" style={{ fontSize: 12 }}>rules followed</span>
          </label>
        </div>
        <textarea placeholder="What happened, what you saw, what you'd do differently…"
          value={note} onChange={(e) => setNote(e.target.value)} rows={3}
          style={{ width: '100%', marginTop: 8, background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)', borderRadius: 8, padding: '9px 12px', fontFamily: 'var(--sans)', fontSize: 13 }} />
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn primary" onClick={save}>Save entry</button>
        </div>
      </div>
      <div className="timeline">
        {(resp?.rows ?? []).map((e) => (
          <div className="tl-item" key={e.id}>
            <span className="tl-time">{fmtEtDate(e.created_at)}</span>
            <span style={{ flex: 1 }}>
              {e.symbol && <span className="sym" style={{ marginRight: 8 }}>{e.symbol}</span>}
              {!e.rules_followed && <span className="badge risk" style={{ marginRight: 6 }}>rules broken</span>}
              {e.tags.map((t) => <span key={t} className="badge src" style={{ marginRight: 4 }}>{t}</span>)}
              <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{e.note}</div>
            </span>
          </div>
        ))}
        {!(resp?.rows ?? []).length && <div className="empty"><b>No entries yet</b>Your first note starts the journal.</div>}
      </div>
    </>
  );
}
