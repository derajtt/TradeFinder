'use client';
import { useMemo, useState } from 'react';
import SignalTable from '../../components/SignalTable';
import DetailDrawer from '../../components/DetailDrawer';
import { API_BASE, usePolling, withKey } from '../../lib/api';
import ProfileTabs from '../../components/ProfileTabs';
import { useProfile } from '../../lib/profile';
import type { SignalRow } from '../../lib/types';

export default function SignalsPage() {
  const [profile] = useProfile();
  const [sort, setSort] = useState<'score' | 'change' | 'time' | 'symbol'>('score');
  const [dedupe, setDedupe] = useState(true);
  // Demo rows were included by default and, sorted by score, floated to the
  // top of the real signal history. Off unless asked for.
  const [demo, setDemo] = useState(false);
  const [resp] = usePolling<{ rows: SignalRow[] }>(
    `/api/signals?include_demo=${demo}&limit=500&profile=${profile}`
    + `&dedupe=${dedupe ? 1 : 0}&sort=${sort}`, 30000);
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
      <ProfileTabs />
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
      <div className="row" style={{ gap: 7, marginBottom: 10, alignItems: 'center' }}>
        <span className="meta">Sort</span>
        {(['score', 'change', 'time', 'symbol'] as const).map((k) => (
          <button key={k} className={`tab ${sort === k ? 'on' : ''}`} onClick={() => setSort(k)}>
            {k === 'change' ? 'move %' : k}
          </button>
        ))}
        <button className={`tab ${demo ? 'on' : ''}`} onClick={() => setDemo((d) => !d)}
          title="Demo/seed rows are synthetic and never count toward any statistic.">
          {demo ? '✓ demo rows shown' : 'show demo rows'}
        </button>
        <button className={`tab ${dedupe ? 'on' : ''}`} onClick={() => setDedupe((d) => !d)}
          title="One lifecycle row is stored per profile, per state, per day, so a raw list repeats the same symbol many times. This keeps the newest row for each.">
          {dedupe ? '✓ one row per symbol' : 'show every record'}
        </button>
        <span className="meta" style={{ marginLeft: 'auto' }}>{rows.length} rows</span>
      </div>
      <SignalTable rows={rows} onSelect={(s) => setSelected(s.symbol)} />
      {selected && <DetailDrawer symbol={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
