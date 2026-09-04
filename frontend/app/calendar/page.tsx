'use client';
import { useMemo, useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { Details, EmptyState, SectionHeader } from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { fmtDateLabel } from '../../lib/format';
import { useMode } from '../../lib/mode';
import type { CandidateRow, SignalRow, WatchlistRow } from '../../lib/types';

/* /api/calendar (backend/app/routes/api.py calendar) */
interface Earning { symbol: string | null; date: string | null; eps_est: number | null; rev_est?: number | null }
interface Calendar { earnings: Earning[]; holidays: string[]; half_days: string[]; earnings_quality?: string }

const MAX_CHIPS = 8;
const DEFAULT_DAYS = 60;
/** 5-letter tickers ending in F (foreign ordinary) or Y (ADR) */
const isForeign = (s: string) => /^[A-Z]{5}$/.test(s) && /[FY]$/.test(s);

function daysFromToday(ymd: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return Number.POSITIVE_INFINITY;
  const d = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const now = new Date();
  const t = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((d - t) / 86400000);
}
function dateLabel(ymd: string): string {
  const base = fmtDateLabel(ymd);
  const y = ymd.slice(0, 4);
  return y && y !== String(new Date().getFullYear()) ? `${base} ${y}` : base;
}

export default function CalendarPage() {
  const cal = usePollingState<Calendar>('/api/calendar', 300000);
  const cands = usePollingState<{ rows: CandidateRow[] }>('/api/candidates', 60000);
  const sigs = usePollingState<{ rows: SignalRow[] }>('/api/signals?active_only=true&limit=200', 60000);
  const wl = usePollingState<{ rows: WatchlistRow[] }>('/api/watchlists', 300000);
  const { advanced } = useMode();
  const [sel, setSel] = useState<string | null>(null);
  const [allClosures, setAllClosures] = useState(false);

  const hot = useMemo(() => {
    const s = new Set<string>();
    for (const r of cands.data?.rows ?? []) s.add(r.symbol);
    for (const r of sigs.data?.rows ?? []) s.add(r.symbol);
    for (const l of wl.data?.rows ?? []) for (const x of l.symbols ?? []) s.add(x);
    return s;
  }, [cands.data, sigs.data, wl.data]);

  const holidays = new Set(cal.data?.holidays ?? []);
  const byDate = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const e of cal.data?.earnings ?? []) {
      if (!e.symbol || !e.date) continue;
      const list = m.get(e.date) ?? [];
      if (!list.includes(e.symbol)) list.push(e.symbol);
      m.set(e.date, list);
    }
    return Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [cal.data]);
  const epsOf = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of cal.data?.earnings ?? []) if (e.symbol && e.eps_est != null) m.set(`${e.date}|${e.symbol}`, e.eps_est);
    return m;
  }, [cal.data]);

  const closures = useMemo(() => {
    const rows = [
      ...(cal.data?.holidays ?? []).map((d) => ({ d, kind: 'closed' as const })),
      ...(cal.data?.half_days ?? []).map((d) => ({ d, kind: 'half' as const })),
    ].sort((a, b) => a.d.localeCompare(b.d));
    return rows;
  }, [cal.data]);
  const nearClosures = closures.filter((c) => daysFromToday(c.d) <= DEFAULT_DAYS);
  const shownClosures = allClosures ? closures : nearClosures;

  const chip = (date: string, s: string) => {
    const eps = epsOf.get(`${date}|${s}`);
    return (
      <button key={s} type="button" className={`chip${hot.has(s) ? ' chip--early' : ''}`} onClick={() => setSel(s)}>
        {s}{advanced && eps !== undefined ? <span className="dim">est. EPS {eps}</span> : null}
      </button>
    );
  };

  return (
    <>
      <SectionHeader level={1} title="Calendar"
        question="Which companies report earnings soon, and when is the market closed?"
        caption="Earnings for the next 7 days from the data provider · exchange holidays and half-days (the scanner adjusts automatically). Highlighted symbols are in today's scan, an open pick, or your watchlist." />

      <div className="cards" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)' }}>
        <section className="card">
          <h3>Earnings (next 7 days)</h3>
          {!cal.loaded ? (
            <EmptyState compact loaded={false} headline="Loading earnings" reason={null} />
          ) : cal.data?.earnings_quality === 'UNAVAILABLE' ? (
            <EmptyState compact tone="warn" headline="Earnings dates unavailable"
              reason="The data provider did not return the earnings calendar (reported: unavailable)." />
          ) : byDate.length === 0 ? (
            <EmptyState compact headline="No earnings in the next 7 days"
              reason={cal.err?.message ?? 'The provider returned no scheduled reports for this window.'} />
          ) : byDate.map(([date, syms]) => {
            const foreign = syms.filter(isForeign).sort();
            const domestic = syms.filter((s) => !isForeign(s));
            const ordered = [...domestic.filter((s) => hot.has(s)).sort(), ...domestic.filter((s) => !hot.has(s)).sort()];
            const shown = ordered.slice(0, MAX_CHIPS);
            const rest = ordered.slice(MAX_CHIPS);
            return (
              <div key={date} style={{ margin: '10px 0' }}>
                <div className="eyebrow">{dateLabel(date)}{holidays.has(date) ? ' (market closed)' : ''} · {syms.length}</div>
                <div className="chips" style={{ marginTop: 6 }}>
                  {shown.map((s) => chip(date, s))}
                </div>
                {rest.length ? (
                  <Details summary={`+${rest.length} more`}>
                    <div className="chips">{rest.map((s) => chip(date, s))}</div>
                  </Details>
                ) : null}
                {foreign.length ? (
                  <Details summary={`+${foreign.length} foreign/OTC`}>
                    <div className="chips">{foreign.map((s) => chip(date, s))}</div>
                  </Details>
                ) : null}
              </div>
            );
          })}
        </section>

        <section className="card">
          <h3>Market closures</h3>
          {!cal.loaded ? (
            <EmptyState compact loaded={false} headline="Loading closures" reason={null} />
          ) : shownClosures.length === 0 ? (
            <EmptyState compact headline={allClosures ? 'No closures listed' : `No closures in the next ${DEFAULT_DAYS} days`}
              reason="The exchange calendar has no holidays or half-days in this window." />
          ) : (
            <div className="timeline" style={{ gap: 6 }}>
              {shownClosures.map((c) => (
                <div key={`${c.kind}-${c.d}`} className="tl-item" style={{ padding: '8px 12px' }}>
                  <span className="tl-time">{dateLabel(c.d)}</span>
                  <span>{c.kind === 'closed' ? 'Market closed' : 'Early close — 1:00 PM ET'}</span>
                </div>
              ))}
            </div>
          )}
          {cal.loaded && closures.length > nearClosures.length ? (
            <button type="button" className="showall" onClick={() => setAllClosures((v) => !v)}>
              {allClosures ? `Show next ${DEFAULT_DAYS} days` : `Show all ${closures.length} listed`}
            </button>
          ) : null}
        </section>
      </div>
      {sel ? <DetailDrawer symbol={sel} onClose={() => setSel(null)} /> : null}
    </>
  );
}
