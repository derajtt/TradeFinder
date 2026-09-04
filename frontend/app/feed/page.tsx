'use client';
import { useEffect, useMemo, useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { Seg } from '../../components/Controls';
import { Advanced, EmptyState, SectionHeader, StatusPill } from '../../components/ui';
import { usePollingState, useStream } from '../../lib/api';
import { fmtAgo, fmtEtDate } from '../../lib/format';
import type { FeedRow } from '../../lib/types';
import { itemCodes } from '../../lib/vocab';
import c from '../controls.module.css';

/* Spec §3.4 — News & filings. The stream, live push, filters, sort and drawer are kept. */

type Kind = '' | 'news' | 'filing';
type Sort = 'time' | 'symbol' | 'kind';
const FEED_CAP = 80;

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

const NAMED_ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };
/** EDGAR company names arrive HTML-escaped ("Mama&#39;s Creations"). */
function decodeEntities(s: string): string {
  return s
    .replace(/&#x([0-9a-f]+);/gi, (_, h: string) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d: string) => String.fromCodePoint(Number(d)))
    .replace(/&([a-z]+);/gi, (m, name: string) => NAMED_ENTITIES[name.toLowerCase()] ?? m);
}

/** The backend title is "{form} — {stored title}" and the stored title is
 *  "{form} — {company}", so a filing arrives as "8-K — 8-K — Acme Corp".
 *  The form is shown once as a pill; what is left is the company name. */
function cleanTitle(r: Pick<FeedRow, 'title' | 'form'>): string {
  let t = decodeEntities((r.title ?? '').trim());
  t = t.replace(/^(\S+)\s+—\s+\1\s+—\s+/, '');
  if (r.form) t = t.replace(new RegExp(`^${escapeRe(r.form)}\\s*[—–-]\\s*`), '');
  t = t.replace(/^\S*K\s+-\s+/, '');
  if (/^filing$/i.test(t)) t = '';
  return t.trim();
}

const SHOW_OPTS = (noNews: boolean, formSet: boolean) => ([
  { key: '' as Kind, label: 'All', disabled: formSet },
  { key: 'news' as Kind, label: 'News', disabled: noNews || formSet,
    note: formSet ? 'A form filter shows filings only' : noNews ? 'no news yet today' : undefined },
  { key: 'filing' as Kind, label: 'Filings' },
]);
const SORT_OPTS: { key: Sort; label: string }[] = [
  { key: 'time', label: 'Newest' }, { key: 'symbol', label: 'Symbol' }, { key: 'kind', label: 'Type' },
];

export default function FeedPage() {
  const [form, setForm] = useState('');
  const [symbol, setSymbol] = useState('');
  const [sort, setSort] = useState<Sort>('time');
  const [kind, setKind] = useState<Kind>('');
  // Picking a form means "show me those filings" — mixing news rows back in
  // made the click look like it did nothing.
  const effKind: Kind = form ? 'filing' : kind;
  const { data, loaded, reload } = usePollingState<{ rows: FeedRow[]; forms: string[] }>(
    `/api/feed?form=${encodeURIComponent(form)}&symbol=${encodeURIComponent(symbol)}`
    + `&sort=${sort}&kind=${effKind}&limit=${FEED_CAP}`, 30000);
  const [sel, setSel] = useState<string | null>(null);
  const [lastLive, setLastLive] = useState<{ at: string; symbol: string; form: string } | null>(null);
  const [, setTick] = useState(0);

  // Live: the backend polls EDGAR's newest-filings feed every ~20s and pushes each
  // new filing over the app's one EventSource; refetch the moment one arrives.
  const connected = useStream({
    filing: (d: { symbol?: string; form?: string }) => {
      setLastLive({ at: new Date().toISOString(), symbol: d?.symbol ?? '', form: d?.form ?? '' });
      reload();
    },
  });
  // Keep the "last filing … ago" reading current.
  useEffect(() => {
    if (!lastLive) return;
    const id = setInterval(() => setTick((t) => t + 1), 15000);
    return () => clearInterval(id);
  }, [lastLive]);

  const rows = data?.rows ?? [];
  // "No news yet today" is only claimed from an unfiltered response that could contain news.
  const [newsSeen, setNewsSeen] = useState<number | null>(null);
  useEffect(() => {
    if (loaded && data && effKind === '' && !symbol) setNewsSeen(data.rows.filter((r) => r.kind === 'news').length);
  }, [data, loaded, effKind, symbol]);
  const noNews = newsSeen === 0;

  const showOpts = useMemo(() => SHOW_OPTS(noNews, !!form), [noNews, form]);

  const livePill = !connected
    ? <StatusPill label="Live feed off — refreshing every 30s" tone="neutral" />
    : lastLive
      ? <StatusPill label={`Live feed · last filing ${lastLive.form} ${lastLive.symbol} ${fmtAgo(lastLive.at)}`} tone="buy" />
      : <StatusPill label="Live feed" tone="buy" />;

  return (
    <>
      <SectionHeader level={1} title="News & filings"
        question="What news or SEC filings just came out for stocks in play?"
        caption="Times are the source's own — news publication time, SEC acceptance time (ET)"
        right={livePill} />

      <div className={c.bar}>
        <div className={c.row}>
          <label className={c.group}>
            <span className={c.label}>Find a symbol</span>
            <input className="input" value={symbol} placeholder="e.g. NVDA" aria-label="Find a symbol"
              style={{ width: 130 }} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
          </label>
          <label className={c.group}>
            <span className={c.label}>Form</span>
            <select className="input sans" value={form} aria-label="SEC form" onChange={(e) => setForm(e.target.value)}>
              <option value="">All SEC forms</option>
              {(data?.forms ?? []).map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
        </div>
        <div className={c.row}>
          <Seg<Kind> label="Show" value={effKind} options={showOpts} onChange={setKind} />
          <Seg<Sort> label="Sort" value={sort} options={SORT_OPTS} onChange={setSort} />
          <span className={c.counter} aria-live="polite">
            {loaded ? `${rows.length} most recent (cap ${FEED_CAP})` : 'Loading…'}
          </span>
        </div>
      </div>

      {!loaded ? (
        <div className="timeline" aria-busy="true">
          {[0, 1, 2, 3].map((i) => <div key={i} className="tl-item skel" style={{ height: 44 }} />)}
        </div>
      ) : rows.length === 0 ? (
        <EmptyState headline="Nothing matches these filters"
          reason={symbol || form ? `No ${effKind === 'filing' ? 'filings' : effKind === 'news' ? 'news' : 'news or filings'} recorded for ${[symbol, form].filter(Boolean).join(' · ')}.`
            : 'No news or filings have been recorded yet.'}
          next="New filings appear here the moment EDGAR publishes them." />
      ) : (
        <div className="timeline">
          {rows.map((r, i) => {
            const filing = r.kind === 'filing';
            const title = filing ? cleanTitle(r) : decodeEntities((r.title ?? '').trim());
            const codes = filing ? itemCodes(r.items) : [];
            return (
              <div className="tl-item" key={`${r.kind}-${r.symbol}-${r.ts}-${i}`}>
                <span className="tl-time">{fmtEtDate(r.ts)}</span>
                <div className={c.rowText}>
                  <button type="button" className={`sym ${c.symBtn}`} onClick={() => setSel(r.symbol)}
                    aria-label={`Open ${r.symbol} details`}>{r.symbol}</button>
                  {filing && r.form ? <StatusPill size="sm" label={r.form} tone="neutral" /> : null}
                  <a href={r.url} target="_blank" rel="noreferrer">
                    {title || (filing ? `${r.form ?? 'SEC'} filing` : 'News item')}
                  </a>
                  <span className="faint">— {r.source}</span>
                  <StatusPill size="sm" label={filing ? 'filed' : 'published'} tone={filing ? 'neutral' : 'accent'} />
                  {codes.length ? (
                    <span className="chips">
                      {codes.map((label, j) => <span className="chip" key={`${label}-${j}`}>{label}</span>)}
                    </span>
                  ) : null}
                  <Advanced>
                    <span className={c.rowMeta}>
                      {filing && r.items ? <code className={c.code}>items {r.items}</code> : null}
                      {filing && r.accession ? (
                        <a className={c.code} href={r.url} target="_blank" rel="noreferrer">accession {r.accession}</a>
                      ) : null}
                    </span>
                  </Advanced>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {sel && <DetailDrawer symbol={sel} onClose={() => setSel(null)} />}
    </>
  );
}
