'use client';
import { useEffect, useMemo, useState } from 'react';
import { apiGet, apiPostBody } from '../lib/api';
import { fmtCompact, fmtEtShort, fmtNum, fmtPct, fmtPrice } from '../lib/format';
import { useMode } from '../lib/mode';
import { useAppSettings, useMarketPhase } from '../lib/status';
import type { CandidateRow, SignalRow } from '../lib/types';
import { OUTCOME, catalystLabel, fieldLabel, gateLabel, humanKey, itemCodes } from '../lib/vocab';
import Chart, { type Bar } from './Chart';
import Freshness from './Freshness';
import TradeRoadmap from './TradeRoadmap';
import { Advanced } from './ui/Advanced';
import { Drawer } from './ui/Drawer';
import { EmptyState } from './ui/EmptyState';
import { Term } from './ui/Popover';
import { ScorePill } from './ui/ScorePill';
import { StatusPill, pillFor } from './ui/StatusPill';
import { WhatsMissing } from './ui/WhatsMissing';

/* /api/candidates/{symbol} (backend/app/routes/api.py candidate_detail) */
interface Detail {
  symbol: string;
  live: (Partial<CandidateRow> & Record<string, any>) | null;
  watch: { started_at: string; start_price: number | null; start_score: number | null;
           checks: number; change_since_watch_pct: number | null;
           series: { t: string; score: number; price: number | null }[] } | null;
  company: { name: string; exchange: string; cik: string; sector: string; industry: string;
             country: string; market_cap: number | null; float_shares: number | null;
             shares_outstanding: number | null; avg_volume: number | null;
             description?: string; website?: string; free_float_pct?: number | null };
  snapshot: { features: Record<string, any>; score_detail: any; at: string } | null;
  catalyst: any;
  news: { headline: string; source: string; url: string; kind: string; published_at: string | null }[];
  filings: { form: string; items: string; title: string; url: string; accession: string; accepted_at: string | null }[];
  signals: Partial<SignalRow>[];
  bars: Bar[];
}

const COMPONENT_MAX: Record<string, number> = {
  momentum_volume: 30, catalyst_quality: 25, sec_filing: 15,
  liquidity_execution: 10, price_confirmation: 10, company_quality: 10,
};

/** Everything about one stock, in the order a beginner asks: the plan, why it
 *  was picked, the company, news & filings, price history. Advanced adds the
 *  raw features, score breakdown, gates and the signal story. Never a `title=`. */
export default function DetailDrawer({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [story, setStory] = useState<any>(null);
  const { advanced } = useMode();
  const { settings } = useAppSettings();
  const phase = useMarketPhase();

  useEffect(() => {
    let alive = true;
    setD(null); setErr(null); setStory(null);
    apiGet<Detail>(`/api/candidates/${encodeURIComponent(symbol)}`)
      .then((x) => {
        if (!alive) return;
        setD(x);
        const uid = x.signals?.[0]?.signal_uid;
        if (uid) apiGet<any>(`/api/signals/${uid}`).then((s) => alive && setStory(s)).catch(() => {});
      })
      .catch((e) => alive && setErr(String(e)));
    return () => { alive = false; };
  }, [symbol]);

  const live = d?.live ?? null;
  const feats: Record<string, any> = live ?? d?.snapshot?.features ?? {};
  const scoreDetail = live
    ? { components: live.components, gates: live.gates, penalties: live.penalties, score: live.score }
    : d?.snapshot?.score_detail;
  const explain = live?.explain ?? scoreDetail?.explain ?? null;
  // `/api/candidates/{symbol}` lists signals with only the initiation fields; the full row
  // (`/api/signals/{uid}`: outcome, signal_type, price source, since-high/low) and the plan
  // numbers (roadmap: stop and targets) arrive with the story and are merged in, so "The plan"
  // never disagrees with the roadmap card under it.
  const sig = useMemo<Partial<SignalRow> | undefined>(() => {
    const base = d?.signals?.[0];
    if (!base) return undefined;
    const st = story && story.signal_uid === base.signal_uid ? story : null;
    const nums = st?.roadmap?.numbers;
    const tp = (t: any): number | null | undefined => (typeof t === 'number' ? t : t?.price);
    return {
      ...base,
      ...(st ? {
        current: st.current ?? base.current, status: st.status ?? base.status,
        signal_type: st.signal_type ?? base.signal_type, price_source: st.price_source ?? base.price_source,
        outcome: st.outcome ?? base.outcome, change_pct: st.change_pct ?? base.change_pct,
        max_gain_pct: st.max_gain_pct ?? base.max_gain_pct, max_drawdown_pct: st.max_drawdown_pct ?? base.max_drawdown_pct,
        post7_high: st.post7_high ?? base.post7_high, post7_low: st.post7_low ?? base.post7_low,
      } : {}),
      stop: base.stop ?? nums?.stop ?? null,
      target1: base.target1 ?? tp(nums?.targets?.[0]) ?? null,
      target2: base.target2 ?? tp(nums?.targets?.[1]) ?? null,
    };
  }, [d, story]);
  const marketOpen = phase.isOpen || phase.isPremarket;
  const confirmAt = settings?.buy_confirm_after_et ?? null;
  const minBuy = settings?.min_score_for_buy ?? 75;

  const state = live?.buy ? { label: 'Buy pick', tone: 'buy' as const }
    : live?.early ? { label: 'Early watch', tone: 'early' as const }
    : live ? { label: 'Watching', tone: 'early' as const }
    : null;

  const title = (
    <>
      {symbol}
      {state ? <StatusPill size="sm" label={state.label} tone={state.tone} /> : null}
      {scoreDetail?.score != null ? <ScorePill value={scoreDetail.score} minBuy={minBuy} /> : null}
    </>
  );
  const subtitle = d
    ? [d.company?.name || live?.name, d.company?.exchange, d.company?.sector].filter(Boolean).join(' · ') || null
    : 'Loading…';

  return (
    <Drawer open onClose={onClose} title={title} subtitle={subtitle}
      footer={(
        <div className="disclaimer" style={{ marginTop: 0, borderTop: 'none', paddingTop: 0 }}>
          A Buy pick is a rules-based research label from the documented scoring spec — not a guaranteed
          outcome and not personalised investment advice. Everything here is paper; no orders are placed.
        </div>
      )}>
      {err && <div className="err-box">Could not load {symbol}: {err}</div>}
      {!d && !err && (
        <div aria-busy="true">
          <div className="skel" style={{ height: 120, margin: '16px 0' }} />
          <div className="skel" style={{ height: 220 }} />
        </div>
      )}

      {d && (
        <>
          {/* ── The plan ─────────────────────────────────────────────── */}
          <div className="subhead">The plan</div>
          {sig ? (
            <>
              <div className="subhead-cap">Paper plan only — no order is placed. Prices from the pick record.</div>
              <div className="kv-grid">
                <KV k={fieldLabel('buy_price')} v={fmtPrice(sig.buy_price)} cls="pos" />
                <KV k={fieldLabel('current')} v={fmtPrice(sig.current)}
                  sub={sig.current_ts ? <Freshness ts={sig.current_ts} marketOpen={marketOpen} thresholdSec={settings?.quote_freshness_sec} /> : undefined} />
                <KV k={fieldLabel('change_pct')} v={fmtPct(sig.change_pct)} cls={sign(sig.change_pct)} />
                <KV k={fieldLabel('stop')} v={fmtPrice(sig.stop)} cls="neg" />
                <KV k={fieldLabel('target1')} v={fmtPrice(sig.target1)} cls="pos" />
                <KV k={fieldLabel('target2')} v={fmtPrice(sig.target2)} cls="pos" />
                <KV k={fieldLabel('initiated_at')} v={fmtEtShort(sig.initiated_at)} sans />
                <KV k={<Term k="early_pop">{fieldLabel('outcome')}</Term>}
                  v={<StatusPill size="sm" {...pillFor(OUTCOME, sig.outcome || 'pending')} />} />
                {advanced ? (
                  <>
                    <KV k={fieldLabel('max_gain_pct')} v={fmtPct(sig.max_gain_pct)} cls={sign(sig.max_gain_pct, true)} />
                    <KV k={fieldLabel('max_drawdown_pct')} v={fmtPct(sig.max_drawdown_pct)} cls={sign(sig.max_drawdown_pct, true)} />
                    <KV k={`${fieldLabel('post7_high')} / low`} v={`${fmtPrice(sig.post7_high)} / ${fmtPrice(sig.post7_low)}`} />
                    <KV k="Price source" v={sig.price_source ?? '—'} sans />
                  </>
                ) : null}
              </div>
            </>
          ) : (
            <EmptyState compact headline="No pick recorded for this stock"
              reason="The scanner has looked at it, but it has never been flagged as a Watch or a Buy." />
          )}
          {story?.roadmap?.numbers || story?.roadmap?.no_trade_reason ? (
            <TradeRoadmap rm={story.roadmap} symbol={symbol} status={story.lifecycle || story.status} plan={story.roadmap} />
          ) : null}

          {/* ── Why it was picked ────────────────────────────────────── */}
          <div className="subhead">Why it was picked</div>
          {live?.early ? (
            <div className="early-banner">
              Early watch — every check passes except the broker premarket window.
              {confirmAt ? ` It becomes a Buy pick from ${confirmAt} ET.` : ' It becomes a Buy pick once the window opens.'}
            </div>
          ) : null}
          {explain?.length || live?.hard_blocks?.length ? (
            <WhatsMissing full explain={explain ?? undefined} hardBlocks={live?.hard_blocks ?? undefined} />
          ) : (
            <p className="note">Not in today's scan — the checklist is only available while the scanner is tracking the stock.</p>
          )}
          {d.catalyst ? (
            <div className="tl-item" style={{ flexDirection: 'column', gap: 6, marginTop: 12 }}>
              <div className="chips">
                <StatusPill size="sm" label={`News: ${catalystLabel(d.catalyst.type)}`}
                  tone={d.catalyst.direction === 'positive' ? 'buy' : d.catalyst.direction === 'negative' ? 'risk' : 'neutral'} />
                {d.catalyst.dilution ? <StatusPill size="sm" label="Dilution" tone="risk" /> : null}
                {d.catalyst.going_concern ? <StatusPill size="sm" label="Going concern" tone="risk" /> : null}
                <span className="chip">{d.catalyst.ai ? 'AI-classified' : 'Keyword match · lower confidence'}</span>
              </div>
              {d.catalyst.summary ? <div style={{ lineHeight: 1.5 }}>{d.catalyst.summary}</div> : null}
              {d.catalyst.source_url ? <a href={d.catalyst.source_url} target="_blank" rel="noreferrer">Original source ↗</a> : null}
              <Advanced>
                <div className="note">
                  direction {String(d.catalyst.direction)} · materiality {String(d.catalyst.materiality)} ·
                  novelty {String(d.catalyst.novelty)} · confidence {fmtNum(d.catalyst.confidence, 2)}
                </div>
                {d.catalyst.facts?.length ? (
                  <div className="note">
                    {d.catalyst.facts.map((f: any, i: number) => <div key={i}>· <b className="dim">{f.label}:</b> {f.value}</div>)}
                  </div>
                ) : null}
              </Advanced>
            </div>
          ) : null}

          {/* ── Company ──────────────────────────────────────────────── */}
          <div className="subhead">Company</div>
          {d.company.description ? (
            <p className="co-desc">{d.company.description}
              {d.company.website ? <> <a href={d.company.website} target="_blank" rel="noreferrer">{d.company.website.replace(/^https?:\/\//, '')} ↗</a></> : null}
            </p>
          ) : null}
          <div className="kv-grid">
            <KV k={fieldLabel(feats.price_indicative ? 'price_indicative' : 'price')} v={fmtPrice(feats.price)}
              sub={feats.price_indicative ? 'midpoint of bid and ask — no fresh trade yet' : undefined} />
            <KV k={<Term k="gap">{fieldLabel('gap_pct')}</Term>} v={fmtPct(feats.gap_pct)} cls={sign(feats.gap_pct)} />
            <KV k={fieldLabel('rvol')} v={feats.rvol != null ? `${fmtNum(feats.rvol, 1)}× normal` : '—'}
              sub={feats.rvol_estimated ? 'estimate' : undefined} />
            <KV k={fieldLabel('pm_volume')} v={fmtCompact(feats.pm_volume)} />
            <KV k={fieldLabel('pm_dollar_volume')} v={feats.pm_dollar_volume != null ? `$${fmtCompact(feats.pm_dollar_volume)}` : '—'} />
            <KV k={fieldLabel('market_cap')} v={money0(d.company.market_cap ?? feats.market_cap)} />
            <KV k={fieldLabel('float_shares')} v={fmtCompact(d.company.float_shares ?? feats.float_shares)} />
            <KV k={fieldLabel('shares_outstanding')} v={fmtCompact(d.company.shares_outstanding ?? feats.shares_outstanding)} />
            <KV k={fieldLabel('avg_volume')} v={fmtCompact(d.company.avg_volume)} />
            <KV k={fieldLabel('industry')} v={d.company.industry || '—'} sans />
            <KV k={fieldLabel('country')} v={d.company.country || '—'} sans />
            {advanced ? (
              <>
                <KV k={fieldLabel('vwap')} v={fmtPrice(feats.vwap)} />
                <KV k={fieldLabel('above_vwap')} v={feats.above_vwap === true ? 'Yes' : feats.above_vwap === false ? 'No' : '—'}
                  cls={feats.above_vwap === true ? 'pos' : feats.above_vwap === false ? 'neg' : ''} sans />
                <KV k={`${fieldLabel('pm_high')} / low`} v={`${fmtPrice(feats.pm_high)} / ${fmtPrice(feats.pm_low)}`} />
                <KV k={fieldLabel('spread_pct')} v={fmtPct(feats.spread_pct, false)} />
                <KV k="Bid / ask" v={`${fmtPrice(feats.bid)} / ${fmtPrice(feats.ask)}`} />
                <KV k={fieldLabel('float_rotation')} v={feats.float_rotation != null ? `${fmtNum(feats.float_rotation * 100, 1)}%` : '—'} />
              </>
            ) : null}
          </div>

          {/* ── News & filings ───────────────────────────────────────── */}
          <div className="subhead">News &amp; filings</div>
          <div className="timeline">
            {d.news.length === 0 && d.filings.length === 0 ? (
              <EmptyState compact headline="Nothing stored yet" reason="No news or SEC filings for this stock are in the last 7 days." />
            ) : null}
            {d.news.map((n, i) => (
              <div className="tl-item" key={`n-${i}`}>
                <span className="tl-time">{fmtEtShort(n.published_at)}</span>
                <span>
                  <a href={n.url} target="_blank" rel="noreferrer">{n.headline}</a>
                  <span className="faint"> — {n.source} · {n.kind === 'press_release' ? 'press release' : 'news'}</span>
                </span>
              </div>
            ))}
            {d.filings.map((f, i) => (
              <div className="tl-item" key={`f-${i}`}>
                <span className="tl-time">{fmtEtShort(f.accepted_at)}</span>
                <span className="chips" style={{ alignItems: 'center' }}>
                  <StatusPill size="sm" label={f.form} tone="neutral" />
                  {itemCodes(f.items).map((c) => <span className="chip" key={c}>{c}</span>)}
                  <a href={f.url} target="_blank" rel="noreferrer">{f.title || 'View on EDGAR'} ↗</a>
                  <Advanced>
                    {f.items ? <code className="pill-raw">items {f.items}</code> : null}
                    {f.accession ? <code className="pill-raw">{f.accession}</code> : null}
                  </Advanced>
                </span>
              </div>
            ))}
          </div>

          {/* ── Price history ────────────────────────────────────────── */}
          <div className="subhead">Price history</div>
          {d.bars?.length ? (
            <>
              <div className="subhead-cap">1-minute bars accumulated by the scanner · lines mark the pick price, stop and targets</div>
              <Chart bars={d.bars} buyPrice={sig?.buy_price} vwap={feats.vwap}
                pmHigh={feats.pm_high} pmLow={feats.pm_low}
                stop={sig?.stop} target1={sig?.target1} target2={sig?.target2}
                watchStart={d.watch ? Math.floor(Date.parse(d.watch.started_at) / 1000) : null} />
            </>
          ) : (
            <EmptyState compact headline="No price history stored yet" reason="Bars accumulate only while the scanner is tracking the stock." />
          )}

          {/* ── Advanced: watch history, score breakdown, gates, story, raw features ── */}
          <Advanced>
            {d.watch ? (
              <>
                <div className="subhead">Watch history</div>
                <div className="kv-grid">
                  <KV k="Watching since" v={fmtEtShort(d.watch.started_at)} sans />
                  <KV k="Price at first sight" v={fmtPrice(d.watch.start_price)} />
                  <KV k="Since first sight" v={fmtPct(d.watch.change_since_watch_pct)} cls={sign(d.watch.change_since_watch_pct)} />
                  <KV k="Score then → now" v={`${d.watch.start_score?.toFixed(0) ?? '—'} → ${(live?.score ?? d.watch.series.at(-1)?.score)?.toFixed(0) ?? '—'}`} />
                  <KV k="Scan passes" v={String(d.watch.checks)} />
                </div>
                {d.watch.series.length > 2 ? <ScoreSpark series={d.watch.series} minBuy={minBuy} /> : null}
              </>
            ) : null}

            {scoreDetail ? (
              <>
                <div className="subhead">Score breakdown</div>
                <div className="subhead-cap">Points out of each part's maximum · a Buy needs ≥ {minBuy} and every gate passing</div>
                {Object.entries(scoreDetail.components ?? {}).map(([k, v]) => (
                  <div className="bar-row" key={k}>
                    <span className="lbl">{humanKey(k)}</span>
                    <div className="bar-track"><div className="bar-fill"
                      style={{ width: `${Math.min(100, (Number(v) / (COMPONENT_MAX[k] || 30)) * 100)}%` }} /></div>
                    <span className="val">{fmtNum(Number(v), 1)} / {COMPONENT_MAX[k] ?? '—'}</span>
                  </div>
                ))}
                {scoreDetail.penalties?.length ? (
                  <div className="chips" style={{ marginTop: 8 }}>
                    {scoreDetail.penalties.map((p: any, i: number) => (
                      <StatusPill size="sm" key={i} tone="risk" label={`${humanKey(p.type)} ${p.points}`} />
                    ))}
                  </div>
                ) : null}
                {scoreDetail.gates ? (
                  <>
                    <div className="subhead">Buy gates</div>
                    <div className="gate-grid">
                      {Object.entries(scoreDetail.gates).map(([k, ok]) => (
                        <div className="gate" key={k}>
                          <span>{gateLabel(k)}</span>
                          <span className={ok ? 'g-ok' : 'g-no'}>{ok ? 'Pass' : 'Fail'}</span>
                        </div>
                      ))}
                    </div>
                  </>
                ) : null}
              </>
            ) : null}

            {story && (story.events?.length || Object.keys(story.checkpoints ?? {}).length) ? (
              <>
                <div className="subhead">Signal story</div>
                <div className="timeline">
                  {[
                    ...(story.events ?? []).map((e: any) => ({
                      ts: e.ts, label: humanKey(e.type),
                      detail: e.type === 'created' ? `signal recorded at ${fmtPrice(e.detail?.price)}`
                        : e.type === 'paper_opened' ? `paper position filled at ${fmtPrice(e.detail?.fill)} · stop ${fmtPrice(e.detail?.stop)}`
                        : e.type === 'outcome_locked' ? `${e.detail?.policy}: ${e.detail?.class}`
                        : e.type === 'correction' ? `correction: ${e.detail?.reason ?? ''}`
                        : JSON.stringify(e.detail ?? {}).slice(0, 90),
                      tone: (e.type === 'correction' ? 'warn' : e.type === 'outcome_locked' ? 'buy' : 'neutral') as 'warn' | 'buy' | 'neutral' })),
                    ...Object.entries(story.checkpoints ?? {}).map(([label, c]: any) => ({
                      ts: null, label: `checkpoint ${label}`,
                      detail: `${fmtPrice(c.price)} (${c.pct >= 0 ? '+' : ''}${c.pct}%)`,
                      tone: (c.pct >= 0 ? 'buy' : 'risk') as 'buy' | 'risk' })),
                  ].map((row, i) => (
                    <div className="tl-item" key={i}>
                      <span className="tl-time">{row.ts ? fmtEtShort(row.ts) : '—'}</span>
                      <StatusPill size="sm" label={row.label} tone={row.tone} />
                      <span className="dim">{row.detail}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}

            {Object.keys(feats).length ? (
              <>
                <div className="subhead">Raw features</div>
                <div className="rm-grid" style={{ padding: 0 }}>
                  {Object.entries(feats)
                    .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
                    .map(([k, v]) => (
                      <div className="rm-kv sm" key={k}><span>{humanKey(k)}</span><b>{String(v)}</b></div>
                    ))}
                </div>
              </>
            ) : null}
          </Advanced>

          {/* ── Journal ──────────────────────────────────────────────── */}
          <div className="subhead">Journal</div>
          <JournalNote symbol={symbol} signalUid={sig?.signal_uid ?? ''} />
        </>
      )}
    </Drawer>
  );
}

function sign(v: number | null | undefined, onlyWhenNonzero = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '';
  if (onlyWhenNonzero && v === 0) return '';
  return v >= 0 ? 'pos' : 'neg';
}
function money0(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `$${fmtCompact(v)}`;
}

function KV({ k, v, cls = '', sans = false, sub }: {
  k: React.ReactNode; v: React.ReactNode; cls?: string; sans?: boolean; sub?: React.ReactNode;
}) {
  return (
    <div className="kv">
      <div className="k">{k}</div>
      <div className={`v ${cls}${sans ? ' sans' : ''}`}>{v}</div>
      {sub ? <div className="v-sub">{sub}</div> : null}
    </div>
  );
}

function JournalNote({ symbol, signalUid }: { symbol: string; signalUid: string }) {
  const [note, setNote] = useState('');
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (!note.trim()) return;
    try {
      await apiPostBody('/api/journal', { symbol, note, signal_uid: signalUid });
      setNote(''); setSaved(true); setError(null);
    } catch (e) { setError(String(e)); }
  }
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <label className="sr-only" htmlFor="drawer-journal">Note about {symbol}</label>
      <input id="drawer-journal" className="input sans" placeholder={`Note about ${symbol}… (Enter to save)`} value={note}
        style={{ flex: 1, minWidth: 220 }}
        onChange={(e) => { setNote(e.target.value); setSaved(false); }}
        onKeyDown={(e) => { if (e.key === 'Enter') void save(); }} />
      <button type="button" className="btn sm" onClick={() => void save()}>Save</button>
      {saved ? <span className="save-note">saved ✓</span> : null}
      {error ? <span className="note neg">{error}</span> : null}
    </div>
  );
}

function ScoreSpark({ series, minBuy }: { series: { t: string; score: number }[]; minBuy: number }) {
  const w = 640, h = 60, pad = 4;
  const xs = series.map((p) => Date.parse(p.t));
  const min = Math.min(...xs), max = Math.max(...xs);
  const pts = series.map((p) => {
    const x = pad + ((Date.parse(p.t) - min) / Math.max(1, max - min)) * (w - 2 * pad);
    const y = h - pad - (Math.min(100, Math.max(0, p.score)) / 100) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const yFor = (s: number) => h - pad - (s / 100) * (h - 2 * pad);
  return (
    <div style={{ margin: '4px 0 2px' }}>
      <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 60 }} role="img"
        aria-label="Score over time since first watch">
        <line x1={pad} x2={w - pad} y1={yFor(minBuy)} y2={yFor(minBuy)} stroke="var(--buy)"
          strokeDasharray="4 4" strokeOpacity="0.5" />
        <polyline points={pts.join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2" />
      </svg>
      <div className="note faint">score over time — dashed line = Buy threshold ({minBuy})</div>
    </div>
  );
}
