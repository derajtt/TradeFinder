'use client';
import { useMemo, useState } from 'react';
import SignalTable from '../../components/SignalTable';
import DetailDrawer from '../../components/DetailDrawer';
import { API_BASE, usePolling, withKey } from '../../lib/api';
import type { SignalRow } from '../../lib/types';

export default function SignalsPage() {
  const [resp] = usePolling<{ rows: SignalRow[] }>('/api/signals?include_demo=true&limit=500', 30000);
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('active');
  const [selected, setSelected] = useState<string | null>(null);

  const rows = useMemo(() => {
    let r = resp?.rows ?? [];
    if (q) r = r.filter((s) => s.symbol.includes(q.toUpperCase()));
    if (status !== 'all') r = r.filter((s) => s.status === status);
    return r;
  }, [resp, q, status]);

  return (
    <>
      <div className="sect">
        <h2>Signal History</h2>
        <span className="meta">immutable chronological record — corrections are new events, never edits</span>
        <span className="spacer" />
        <a className="btn" href={withKey(`${API_BASE}/api/signals/export.csv`)}>Export CSV</a>
      </div>
      <div style={{ display: 'flex', gap: 10, margin: '0 0 10px' }}>
        <input aria-label="Filter by symbol" placeholder="Symbol…" value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)',
                   borderRadius: 8, padding: '7px 12px', width: 160, fontSize: 13 }} />
        <select aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}
          style={{ background: 'var(--bg-panel)', border: '1px solid var(--line)', color: 'var(--text)',
                   borderRadius: 8, padding: '7px 12px', fontSize: 13 }}>
          <option value="active">Active</option>
          <option value="all">All statuses</option>
          <option value="closed">Closed</option>
          <option value="invalidated">Invalidated</option>
        </select>
      </div>
      <SignalTable rows={rows} onSelect={(s) => setSelected(s.symbol)} />
      {selected && <DetailDrawer symbol={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
