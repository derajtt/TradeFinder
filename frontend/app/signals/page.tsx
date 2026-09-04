'use client';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import DetailDrawer from '../../components/DetailDrawer';
import { Seg, Switch } from '../../components/Controls';
import { Advanced, EmptyState, SectionHeader, SignalTable, StrategyScope, useProfiles, useScopeLabel } from '../../components/ui';
import { API_BASE, usePollingState, withKey } from '../../lib/api';
import { useMode } from '../../lib/mode';
import { useProfile } from '../../lib/profile';
import type { SignalRow } from '../../lib/types';
import { SIGNAL_STATUS, titleCase } from '../../lib/vocab';
import c from '../controls.module.css';

/* Spec §3.3 — Picks. URL deep link: /signals?type=buy&status=active (from Today's Buy picks). */

type TypeFilter = 'buy' | 'watch' | 'both';
type StatusFilter = 'active' | 'closed' | 'invalidated' | 'all';

const TYPE_OPTS: { key: TypeFilter; label: string }[] = [
  { key: 'buy', label: 'Buys' }, { key: 'watch', label: 'Watches' }, { key: 'both', label: 'Both' },
];
const STATUS_KEYS: StatusFilter[] = ['active', 'closed', 'invalidated', 'all'];
const statusLabel = (s: StatusFilter) => (s === 'all' ? 'All' : SIGNAL_STATUS[s]?.label ?? titleCase(s));

function parseType(v: string | null): TypeFilter {
  return v === 'buy' || v === 'watch' || v === 'both' ? v : 'both';
}
function parseStatus(v: string | null): StatusFilter {
  // Default is All (spec); "open" is accepted as the plain-English alias of active.
  if (v === 'open') return 'active';
  return v === 'active' || v === 'closed' || v === 'invalidated' || v === 'all' ? v : 'all';
}

function PicksPage() {
  const params = useSearchParams();
  const [profile] = useProfile();
  const { profiles } = useProfiles();
  const scopeLabel = useScopeLabel();
  const { advanced } = useMode();
  const profileName = profiles[profile]?.name ?? titleCase(profile);

  const [type, setType] = useState<TypeFilter>(() => parseType(params.get('type')));
  const [status, setStatus] = useState<StatusFilter>(() => parseStatus(params.get('status')));
  const [dedupe, setDedupe] = useState(true);
  // Demo/seed rows are synthetic and never count toward any statistic; the switch is Advanced-only.
  const [demo, setDemo] = useState(false);
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<string | null>(null);

  // Keep the URL shareable without a router round-trip.
  useEffect(() => {
    try {
      const u = new URL(window.location.href);
      if (type === 'both') u.searchParams.delete('type'); else u.searchParams.set('type', type);
      if (status === 'all') u.searchParams.delete('status'); else u.searchParams.set('status', status);
      window.history.replaceState(null, '', u.toString());
    } catch { /* ignore */ }
  }, [type, status]);

  const includeDemo = advanced && demo;
  // sort=time → the API returns the newest 500 records; the table headers re-sort client-side.
  const { data, loaded } = usePollingState<{ rows: SignalRow[] }>(
    `/api/signals?include_demo=${includeDemo}&limit=500&profile=${encodeURIComponent(profile)}`
    + `&dedupe=${dedupe ? 1 : 0}&sort=time`, 30000);

  const all = data?.rows ?? [];
  const rows = useMemo(() => {
    let r = all;
    const u = q.trim().toUpperCase();
    if (u) r = r.filter((s) => s.symbol.includes(u));
    if (type !== 'both') r = r.filter((s) => (s.signal_type ?? 'buy') === type);
    if (status !== 'all') r = r.filter((s) => s.status === status);
    return r;
  }, [all, q, type, status]);

  const counter = !loaded ? 'Loading…'
    : dedupe ? `${rows.length} stocks (one row per stock)` : `${rows.length} records`;

  const emptyReason = all.length === 0
    ? 'This strategy has not flagged any stock yet.'
    : `${all.length} picks are recorded for ${profileName}, but none match these filters.`;

  return (
    <>
      <StrategyScope />
      <SectionHeader level={1} title={`Picks — ${profileName}`}
        question="What has this strategy picked, and how did each pick turn out?"
        caption="Every stock this strategy flagged. Buys passed all checks; Watches did not (yet)."
        note={`${scopeLabel} · newest first · click a column heading to change the order`}
        right={<a className="btn sm" href={withKey(`${API_BASE}/api/signals/export.csv`)}>Export CSV</a>} />

      <div className={c.bar}>
        <div className={c.row}>
          <label className={c.group}>
            <span className={c.label}>Find a symbol</span>
            <input className="input" value={q} placeholder="e.g. NVDA" aria-label="Find a symbol"
              style={{ width: 130 }} onChange={(e) => setQ(e.target.value.toUpperCase())} />
          </label>
          <Seg<TypeFilter> label="Show" value={type} options={TYPE_OPTS} onChange={setType} />
          <Seg<StatusFilter> label="Status" value={status}
            options={STATUS_KEYS.map((k) => ({ key: k, label: statusLabel(k) }))} onChange={setStatus} />
        </div>
        <div className={c.row}>
          <Switch label="One row per stock" checked={dedupe} onChange={setDedupe} />
          <Advanced>
            <Switch label="Include demo rows" checked={demo} onChange={setDemo}
              hint="synthetic seed rows — never counted in any statistic" />
          </Advanced>
          <span className={c.counter} aria-live="polite">{counter}</span>
        </div>
      </div>

      <SignalTable variant="mixed" scope={scopeLabel} rows={rows} loaded={loaded}
        onSelect={(s) => setSelected(s.symbol)}
        emptyState={<EmptyState compact headline="No picks match" reason={emptyReason} />} />

      {selected && <DetailDrawer symbol={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

/** useSearchParams needs a Suspense boundary in the app router. */
export default function SignalsPage() {
  return (
    <Suspense fallback={<div className="skel" style={{ height: 320, marginTop: 20 }} />}>
      <PicksPage />
    </Suspense>
  );
}
