'use client';
import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';
import { fmtCompact, fmtEt, fmtEtDate, fmtNum, fmtPct, fmtPrice } from '../lib/format';
import { TERMS } from '../lib/terms';
import Chart, { type Bar } from './Chart';
import Score from './Score';
import ScoringLegend from './ScoringLegend';

interface Detail {
  symbol: string;
  live: any;
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
  signals: any[];
  bars: Bar[];
}

const COMPONENT_MAX: Record<string, number> = {
  momentum_volume: 30, catalyst_quality: 25, sec_filing: 15,
  liquidity_execution: 10, price_confirmation: 10, company_quality: 10,
};
const GATE_LABELS: Record<string, string> = {
  score_gate: 'Score ≥ threshold', catalyst_gate: 'Verified catalyst',
  rvol_gate: 'RVOL threshold', volume_gate: 'Volume / $-volume',
  freshness_gate: 'Fresh quote', spread_gate: 'Spread OK',
  no_hard_block: 'No hard blocks', price_confirmation_gate: 'Price confirmation',
};

export default function DetailDrawer({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setD(null); setErr(null);
    apiGet<Detail>(`/api/candidates/${symbol}`)
      .then((x) => alive && setD(x))
      .catch((e) => alive && setErr(String(e)));
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => { alive = false; window.removeEventListener('keydown', onKey); };
  }, [symbol, onClose]);

  const live = d?.live;
  const feats = live ?? d?.snapshot?.features ?? {};
  const scoreDetail = live ? { components: live.components, gates: live.gates, penalties: live.penalties, score: live.score }
    : d?.snapshot?.score_detail;
  const sig = d?.signals?.[0];

  return (
    <>
      <div className="drawer-veil" onClick={onClose} aria-hidden />
      <aside className="drawer" role="dialog" aria-label={`${symbol} details`} aria-modal="true">
        <div className="drawer-head">
          <div>
            <div className="sym-big">{symbol}
              {live?.buy && <span className="badge buy" style={{ marginLeft: 10, verticalAlign: 'middle' }}>BUY</span>}
            </div>
            <div className="dim">{d?.company?.name || live?.name || '…'}
              {d?.company?.exchange ? ` · ${d.company.exchange}` : ''}
              {d?.company?.sector ? ` · ${d.company.sector}` : ''}</div>
          </div>
          {scoreDetail && <div style={{ textAlign: 'right' }}>
            <Score v={scoreDetail.score} />
            <div className="faint" style={{ fontSize: 10, marginTop: 4 }}>SCORE</div>
          </div>}
          <button className="drawer-close" onClick={onClose} aria-label="Close details">✕ Close</button>
        </div>

        {err && <div className="err-box">Failed to load: {err}</div>}
        {!d && !err && <div className="skel" style={{ height: 300, marginTop: 20 }} />}

        {d && (<>
          {d.company.description && (
            <p className="co-desc">{d.company.description}
              {d.company.website && <> <a href={d.company.website} target="_blank" rel="noreferrer">{d.company.website.replace(/^https?:\/\//, '')} ↗</a></>}
            </p>
          )}
          <div className="kv-grid">
            <KV k={feats.price_indicative ? 'Price (indicative mid)' : 'Price'} v={(feats.price_indicative ? '~' : '') + fmtPrice(feats.price)} tip={TERMS.price} />
            <KV k="Gap %" v={fmtPct(feats.gap_pct)} cls={(feats.gap_pct ?? 0) >= 0 ? 'pos' : 'neg'} tip={TERMS.gap} />
            <KV k={`RVOL${feats.rvol_estimated ? ' (est)' : ''}`} v={feats.rvol != null ? fmtNum(feats.rvol, 1) + 'x' : '—'} tip={TERMS.rvol} />
            <KV k="PM Volume" v={fmtCompact(feats.pm_volume)} tip={TERMS.pm_vol} />
            <KV k="PM $ Volume" v={feats.pm_dollar_volume != null ? '$' + fmtCompact(feats.pm_dollar_volume) : '—'} tip={TERMS.pm_dvol} />
            <KV k="VWAP" v={fmtPrice(feats.vwap)} tip={TERMS.vwap} />
            <KV k="Above VWAP" v={feats.above_vwap === true ? 'Yes' : feats.above_vwap === false ? 'No' : 'n/a'}
               cls={feats.above_vwap === true ? 'pos' : feats.above_vwap === false ? 'neg' : ''} />
            <KV k="PM High / Low" v={`${fmtPrice(feats.pm_high)} / ${fmtPrice(feats.pm_low)}`} />
            <KV k="Spread" v={fmtPct(feats.spread_pct, false)} tip={TERMS.spread} />
            <KV k="Bid / Ask" v={`${fmtPrice(feats.bid)} / ${fmtPrice(feats.ask)}`} />
            <KV k="Market Cap" v={d.company.market_cap != null ? '$' + fmtCompact(d.company.market_cap) : (feats.market_cap != null ? '$' + fmtCompact(feats.market_cap) : '—')} tip={TERMS.mkt_cap} />
            <KV k="Float Rotation" v={feats.float_rotation != null ? fmtNum(feats.float_rotation * 100, 1) + '%' : '—'} tip={TERMS.float_rot} cls={feats.float_rotation >= 0.2 ? 'pos' : ''} />
            <KV k="Float" v={fmtCompact(d.company.float_shares ?? feats.float_shares)} tip={TERMS.float} />
            <KV k="Shares Out" v={fmtCompact(d.company.shares_outstanding ?? feats.shares_outstanding)} tip={TERMS.shares_out} />
            <KV k="Avg Volume" v={fmtCompact(d.company.avg_volume)} tip={TERMS.avg_vol} />
            <KV k="Industry" v={d.company.industry || '—'} mono={false} />
            <KV k="Country" v={d.company.country || '—'} mono={false} />
          </div>

          {live?.early && (
            <div className="early-banner">
              <b>EARLY WATCH</b> — every gate passes except the broker premarket window.
              BUY confirms automatically once your window opens (see Settings → Confirm BUY after).
            </div>
          )}

          {d.watch && (<>
            <div className="subhead">Watch history — since the scanner picked it up</div>
            <div className="kv-grid">
              <KV k="Watching since" v={fmtEt(d.watch.started_at)} mono={false} />
              <KV k="Price at first sight" v={fmtPrice(d.watch.start_price)} />
              <KV k="Change since watch" v={fmtPct(d.watch.change_since_watch_pct)}
                 cls={(d.watch.change_since_watch_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
              <KV k="Score then → now" v={`${d.watch.start_score?.toFixed(0) ?? '—'} → ${(live?.score ?? d.watch.series.at(-1)?.score)?.toFixed(0) ?? '—'}`} />
              <KV k="Scan passes" v={String(d.watch.checks)} />
            </div>
            {d.watch.series.length > 2 && <ScoreSpark series={d.watch.series} />}
          </>)}

          <div className="subhead">Chart — accumulated 1-min bars</div>
          <Chart bars={d.bars} buyPrice={sig?.buy_price} vwap={feats.vwap}
                 pmHigh={feats.pm_high} pmLow={feats.pm_low}
                 watchStart={d.watch ? Math.floor(Date.parse(d.watch.started_at) / 1000) : null} />

          {(live?.explain?.length || scoreDetail?.explain?.length) ? (<>
            <div className="subhead">Path to BUY — every row must pass</div>
            <div className="timeline">
              {(live?.explain ?? scoreDetail?.explain ?? []).map((e: any) => (
                <div className="gate buypath" key={e.key}>
                  <span className={e.pass ? 'g-ok' : 'g-no'} style={{ width: 40 }}>{e.pass ? 'PASS' : 'FAIL'}</span>
                  <span style={{ flex: 1 }}>{e.label}</span>
                  <span className="dim" style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>
                    {typeof e.actual === 'number' ? e.actual.toLocaleString('en-US', { maximumFractionDigits: 1 }) : String(e.actual ?? '—')}
                    <span className="faint">  (need {e.required})</span>
                  </span>
                </div>
              ))}
            </div>
          </>) : null}

          {scoreDetail && (<>
            <div className="subhead">Score breakdown <ScoringLegend /></div>
            {Object.entries(scoreDetail.components ?? {}).map(([k, v]) => (
              <div className="bar-row" key={k}>
                <span className="lbl">{k.replace(/_/g, ' ')}</span>
                <div className="bar-track"><div className="bar-fill"
                  style={{ width: `${Math.min(100, (Number(v) / (COMPONENT_MAX[k] || 30)) * 100)}%` }} /></div>
                <span className="val">{fmtNum(Number(v), 1)} / {COMPONENT_MAX[k] ?? '—'}</span>
              </div>
            ))}
            {!!scoreDetail.penalties?.length && (
              <div style={{ marginTop: 8 }}>
                {scoreDetail.penalties.map((p: any, i: number) => (
                  <span className="badge risk" key={i} style={{ marginRight: 6, marginBottom: 4 }}>
                    {p.type.replace(/_/g, ' ')} {p.points}
                  </span>
                ))}
              </div>
            )}
            <div className="subhead">BUY gates</div>
            <div className="gate-grid">
              {Object.entries(scoreDetail.gates ?? {}).map(([k, ok]) => (
                <div className="gate" key={k}>
                  <span>{GATE_LABELS[k] ?? k}</span>
                  <span className={ok ? 'g-ok' : 'g-no'}>{ok ? 'PASS' : 'FAIL'}</span>
                </div>
              ))}
            </div>
          </>)}

          {d.catalyst && (<>
            <div className="subhead">Catalyst {d.catalyst.ai ? '· AI-classified' : '· heuristic (low confidence)'}</div>
            <div className="tl-item" style={{ flexDirection: 'column', gap: 6 }}>
              <div>
                <span className={`badge ${d.catalyst.direction === 'positive' ? 'buy' : d.catalyst.direction === 'negative' ? 'risk' : 'neutral'}`}>{d.catalyst.direction}</span>{' '}
                <span className="badge neutral">{d.catalyst.type || 'unclassified'}</span>{' '}
                <span className="badge neutral">materiality {d.catalyst.materiality}</span>{' '}
                <span className="badge neutral">novelty: {d.catalyst.novelty}</span>{' '}
                <span className="badge neutral">conf {fmtNum(d.catalyst.confidence, 2)}</span>
                {d.catalyst.dilution && <span className="badge risk">dilution</span>}{' '}
                {d.catalyst.going_concern && <span className="badge risk">going concern</span>}
              </div>
              <div style={{ lineHeight: 1.5 }}>{d.catalyst.summary}</div>
              {d.catalyst.source_url && <a href={d.catalyst.source_url} target="_blank" rel="noreferrer">Original source ↗</a>}
              {!!d.catalyst.facts?.length && (
                <div className="faint" style={{ fontSize: 12 }}>
                  {d.catalyst.facts.map((f: any, i: number) => (
                    <div key={i}>· <b className="dim">{f.label}:</b> {f.value}</div>
                  ))}
                </div>
              )}
            </div>
          </>)}

          {sig && (<>
            <div className="subhead">Signal performance</div>
            <div className="kv-grid">
              <KV k="BUY price (immutable)" v={fmtPrice(sig.buy_price)} cls="pos" />
              <KV k="Current" v={fmtPrice(sig.current)} />
              <KV k="Change" v={fmtPct(sig.change_pct)} cls={(sig.change_pct ?? 0) >= 0 ? 'pos' : 'neg'} />
              <KV k="Max gain" v={fmtPct(sig.max_gain_pct)} cls="pos" />
              <KV k="Max drawdown" v={fmtPct(sig.max_drawdown_pct)} cls="neg" />
              <KV k="Initiated" v={fmtEtDate(sig.initiated_at)} mono={false} />
            </div>
          </>)}

          <div className="subhead">News timeline ({d.news.length})</div>
          <div className="timeline">
            {d.news.length === 0 && <div className="empty">No stored news for this symbol yet.</div>}
            {d.news.map((n, i) => (
              <div className="tl-item" key={i}>
                <span className="tl-time">{fmtEtDate(n.published_at)}</span>
                <span>
                  <a href={n.url} target="_blank" rel="noreferrer">{n.headline}</a>
                  <span className="faint"> — {n.source} · {n.kind === 'press_release' ? 'PR' : 'news'}</span>
                </span>
              </div>
            ))}
          </div>

          <div className="subhead">SEC filings — last 7 days ({d.filings.length})</div>
          <div className="timeline">
            {d.filings.length === 0 && <div className="empty">No relevant EDGAR filings in the window.</div>}
            {d.filings.map((f, i) => (
              <div className="tl-item" key={i}>
                <span className="tl-time">{fmtEtDate(f.accepted_at)}</span>
                <span>
                  <span className="badge neutral">{f.form}</span>{' '}
                  {f.items && <span className="faint">items {f.items} · </span>}
                  <a href={f.url} target="_blank" rel="noreferrer">{f.title || 'View on EDGAR'} ↗</a>
                </span>
              </div>
            ))}
          </div>

          <div className="disclaimer">
            BUY is a deterministic, rules-based research label generated from the documented scoring
            spec — not a guaranteed outcome and not personalized investment advice. No orders are placed.
          </div>
        </>)}
      </aside>
    </>
  );
}

function KV({ k, v, cls = '', mono = true, tip }: { k: string; v: string; cls?: string; mono?: boolean; tip?: string }) {
  return (
    <div className="kv" title={tip}>
      <div className="k">{k}{tip ? <span className="tip-mark" aria-hidden> ?</span> : null}</div>
      <div className={`v ${cls}`} style={mono ? undefined : { fontFamily: 'var(--sans)', fontSize: 12.5 }}>{v}</div>
    </div>
  );
}


function ScoreSpark({ series }: { series: { t: string; score: number }[] }) {
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
        <line x1={pad} x2={w - pad} y1={yFor(75)} y2={yFor(75)} stroke="var(--buy)"
              strokeDasharray="4 4" strokeOpacity="0.5" />
        <polyline points={pts.join(' ')} fill="none" stroke="var(--accent)" strokeWidth="2" />
      </svg>
      <div className="faint" style={{ fontSize: 10 }}>score over time — dashed line = BUY threshold (75)</div>
    </div>
  );
}
