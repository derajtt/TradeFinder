'use client';
import { useState } from 'react';
import TradeRoadmap from '../../components/TradeRoadmap';
import {
  DataTable, EmptyState, EvidenceTag, SectionHeader, StatTile, StatusPill, type Column,
} from '../../components/ui';
import { usePollingState } from '../../lib/api';
import type { BacktestSplit, Evidence } from '../../lib/evidence';
import { fmtR } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { humanKey, sampleLabel } from '../../lib/vocab';

/* ── payload shapes (backend/app/routes/risk_api.py, backend/app/bt/reversion_bt.py) ── */

interface Sig {
  signal_uid: string; symbol: string; timeframe: string; direction: string;
  variant: string; score: number; score_band: string; status: string;
  win_loss: string; entry: number; stop: number; targets: any[];
  confirmed_at: string; expires_at: string; regime: string; session: string;
  actionable: boolean; no_trade_reason?: string; roadmap: any; trade_plan: any;
  why: string[]; indicators: any; parameters: any; events: any[];
  asset_class: string; exit?: any;
}

/** `_agg()` — paper aggregates. `win_rate` is already a percent. */
interface Agg {
  signals: number; resolved: number; wins?: number; losses?: number;
  ambiguous?: number; breakeven?: number;
  win_rate?: number; expectancy_pct?: number; expectancy_r?: number;
  profit_factor?: number | null; sample: string; note?: string;
}
/** `metrics()` — backtest split metrics. `win_rate` is already a percent. */
interface BtMetrics {
  trades: number; wins?: number; losses?: number; win_rate?: number;
  win_rate_wilson_lb?: number; expectancy_r?: number; profit_factor?: number | null;
  sample?: string; note?: string;
}
type GroupMetric = Partial<Agg> & Partial<BtMetrics>;

interface VerdictObj {
  headline?: string; summary?: string; configs_evaluated?: number;
  positive_expectancy_in_sample?: number; holdout_expectancy_r?: number;
  holdout_profit_factor?: number; recommendation?: string; caveats?: string[];
}
interface Backtest {
  verdict?: VerdictObj | string; reason?: string;
  chosen?: { entry: Record<string, unknown>; exit: Record<string, unknown>;
             train: BtMetrics; validation: BtMetrics; test: BtMetrics };
  protocol?: Record<string, unknown>;
  test_breakdowns?: Record<string, Record<string, BtMetrics>>;
  coverage?: { symbol: string; timeframe: string; bars: number }[];
}
interface Perf {
  paper: { overall: Agg } & Record<string, Record<string, Agg> | Agg>;
  backtest: Backtest | null; separation_note: string;
}
interface Cfg {
  current: Record<string, unknown>; defaults: Record<string, unknown>;
  variants: Record<string, { label: string; note: string }>;
  versions: Record<string, string>;
  test_ranges: Record<string, (number | string)[]>;
  score_weights: Record<string, number>;
}

const TABS = ['Backtest', 'Paper signals', 'Performance', 'Strategy lab'] as const;
type Tab = typeof TABS[number];
const LIVE_STATUSES = ['CONFIRMED', 'ENTRY_ZONE', 'ACTIVE', 'TP1_HIT', 'TP2_HIT'];
const FILTERS: { key: string; label: string }[] = [
  { key: 'live', label: 'Open' }, { key: 'CLOSED', label: 'Closed' }, { key: 'NO_TRADE', label: 'No trade' },
  { key: 'EXPIRED', label: 'Expired' }, { key: '', label: 'All' },
];
const SRC_PAPER = 'Paper account · Extreme Reversion · resolved paper trades';
const SRC_BT = 'Backtest · Extreme Reversion · chosen configuration';

const nOf = (m: GroupMetric) => m.resolved ?? m.trades ?? 0;
const pctText = (v: number | null | undefined) => (v === null || v === undefined ? null : `${v}%`);
const pfText = (v: number | null | undefined, n: number) =>
  v === null || v === undefined || (v === 0 && n === 0) ? '—' : v.toFixed(2);
const toneOf = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? 'buy' : 'risk');
/** 'high_risk' → 'High risk' · 'HIGH VOLATILITY' → 'High volatility' · '5min' → '5 min' · 'etf' → 'ETF' */
const groupLabel = (k: string) => {
  const s = k.replace(/^(\d+)(min|hour)$/, '$1 $2');
  if (/^etf$/i.test(s)) return 'ETF';
  return humanKey(/^[A-Z0-9_ ]{4,}$/.test(s) ? s.toLowerCase() : s);
};

/* ── one breakdown table: Group · Trades · Win rate · Expectancy · Profit factor · Sample ── */

interface Row { k: string; m: GroupMetric; n: number }

function MetricTable({ heading, groups, note, evidence, split, loaded = true }: {
  heading: string; groups: Record<string, GroupMetric> | null | undefined; note?: string;
  evidence: Evidence; split?: BacktestSplit; loaded?: boolean;
}) {
  const { advanced } = useMode();
  const all = Object.entries(groups ?? {}).map(([k, m]) => ({ k, m, n: nOf(m) }));
  const rows = all.filter((r) => r.n > 0);
  const backendNote = all.find((r) => r.m.note)?.m.note ?? null;
  const cols: Column<Row>[] = [
    { key: 'group', header: 'Group', align: 'l', simple: true, sortValue: (r) => r.k,
      cell: (r) => <>{groupLabel(r.k)}{advanced && groupLabel(r.k) !== r.k ? <> <code className="pill-raw">{r.k}</code></> : null}</> },
    { key: 'n', header: 'Trades', simple: true, sortValue: (r) => r.n, cell: (r) => r.n },
    { key: 'wr', header: 'Win rate', simple: true, sortValue: (r) => r.m.win_rate ?? null,
      cell: (r) => pctText(r.m.win_rate) ?? '—' },
    { key: 'exp', header: 'Expectancy', term: 'expectancy_r', simple: true,
      sortValue: (r) => r.m.expectancy_r ?? null,
      cell: (r) => <span className={(r.m.expectancy_r ?? 0) > 0 ? 'pos' : (r.m.expectancy_r ?? 0) < 0 ? 'neg' : undefined}>{fmtR(r.m.expectancy_r, 2)}</span> },
    { key: 'pf', header: 'Profit factor', term: 'profit_factor', simple: true,
      sortValue: (r) => r.m.profit_factor ?? null, cell: (r) => pfText(r.m.profit_factor, r.n) },
    { key: 'sample', header: 'Sample', simple: true, cell: (r) => sampleLabel(r.m.sample, r.n) },
  ];
  return (
    <section className="panel">
      <div className="panel-hd">
        <h3>{heading}</h3>
        <EvidenceTag evidence={evidence} split={split} />
      </div>
      {note ? <p className="note" style={{ marginBottom: 8 }}>{note}</p> : null}
      {!loaded || rows.length ? (
        <DataTable<Row> rows={rows} columns={cols} rowKey={(r) => r.k} loaded={loaded}
          defaultSort={{ key: 'exp', dir: 'desc' }} dense
          note={rows.length ? `${rows.length} groups with at least one resolved trade` : undefined} />
      ) : (
        <EmptyState compact headline="No resolved trades in this breakdown yet"
          reason={backendNote ?? 'No trade in this grouping has closed yet.'} />
      )}
    </section>
  );
}

/* ── KV grid for configuration values (settings, not stats) ── */

function KvGrid({ obj }: { obj: Record<string, unknown> | null | undefined }) {
  const entries = Object.entries(obj ?? {});
  if (!entries.length) return <p className="note">Nothing recorded.</p>;
  return (
    <div className="kv-grid">
      {entries.map(([k, v]) => (
        <div className="kv" key={k}>
          <div className="k">{humanKey(k)}</div>
          <div className="v sans">{Array.isArray(v) ? v.join(', ') : String(v)}</div>
        </div>
      ))}
    </div>
  );
}

/* ── page ── */

export default function ReversionPage() {
  const [tab, setTab] = useState<Tab>('Backtest');
  const [filter, setFilter] = useState('live');
  const sigs = usePollingState<{ signals: Sig[] }>(`/api/reversion/signals?status=${filter}&limit=60`, 20000);
  const perf = usePollingState<Perf>('/api/reversion/performance', 60000);
  const cfg = usePollingState<Cfg>('/api/reversion/config', 120000);
  const { advanced } = useMode();

  const rows = sigs.data?.signals ?? [];
  const live = rows.filter((r) => LIVE_STATUSES.includes(r.status));
  const overall = perf.data?.paper?.overall ?? null;
  const bt = perf.data?.backtest ?? null;
  const chosen = bt?.chosen;
  const verdictObj = bt && typeof bt.verdict === 'object' && bt.verdict ? bt.verdict : null;
  const verdictStr = bt && typeof bt.verdict === 'string' ? bt.verdict : null;
  const bbDev = (cfg.data?.current?.bb_dev ?? cfg.data?.defaults?.bb_dev) as number | undefined;
  const paperGroups = (key: string) => (perf.data?.paper?.[key] as Record<string, Agg> | undefined) ?? null;

  return (
    <>
      <SectionHeader level={1}
        title={<>Extreme Reversion <StatusPill label="Failed test — paper only" tone="risk" />
          {advanced ? <code className="pill-raw">EXTREME_BB_RSI</code> : null}</>}
        question="What did the failed Extreme Reversion experiment show, and is it still producing paper signals?"
        caption="Fades statistically extreme dislocations — only after price has closed back inside the band and momentum has turned. Paper research only; no orders are placed. The backtest verdict was negative and the strategy was never promoted." />

      <div className="safety">
        <span aria-hidden style={{ fontSize: 16 }}>⚠</span>
        <div>
          <b>These are research signals, not guaranteed outcomes.</b> Risk management
          limits planned exposure but cannot eliminate losses, slippage, gaps or
          market risk. A stop order can fill worse than the stop price.
        </div>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} type="button"
            className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === 'Backtest' && (
        <>
          <SectionHeader title="What the backtest showed" question="Did the rules make money on past data once they were locked and tested on unseen periods?"
            evidence="BACKTEST" caption="Optimised on the first 60% of each series, selected on the next 20%, scored once on the final 20%." />
          {!perf.loaded ? (
            <EmptyState loaded={false} headline="Loading backtest" reason={null} />
          ) : !bt ? (
            <EmptyState headline="No backtest on disk yet"
              reason={perf.err ? perf.err.message : 'The performance endpoint returned no backtest object (optimize_result.json is absent).'}
              next="Run backend/scripts/reversion_optimize.py to populate this tab." />
          ) : (
            <>
              {verdictObj?.headline ? (
                <section className="panel panel--risk">
                  <h3 style={{ color: 'var(--risk)' }}>Backtest verdict — {verdictObj.headline}</h3>
                  {verdictObj.summary ? <p className="lead" style={{ lineHeight: 1.7 }}>{verdictObj.summary}</p> : null}
                  <p className="note" style={{ marginTop: 8 }}>
                    {verdictObj.configs_evaluated !== undefined ? <>{verdictObj.configs_evaluated} configurations evaluated</> : null}
                    {verdictObj.positive_expectancy_in_sample !== undefined ? <> · {verdictObj.positive_expectancy_in_sample} had positive expectancy in-sample</> : null}
                    {!chosen && verdictObj.holdout_expectancy_r !== undefined ? <> · holdout expectancy {fmtR(verdictObj.holdout_expectancy_r, 2)} (trade count not reported)</> : null}
                    {!chosen && verdictObj.holdout_profit_factor !== undefined ? <> · holdout profit factor {verdictObj.holdout_profit_factor}</> : null}
                  </p>
                  {verdictObj.recommendation ? <p className="note" style={{ marginTop: 8, lineHeight: 1.65 }}>{verdictObj.recommendation}</p> : null}
                  {verdictObj.caveats?.length ? (
                    <ul className="rm-why" style={{ marginTop: 8, paddingLeft: 18 }}>
                      {verdictObj.caveats.map((c, i) => <li key={i}>{c}</li>)}
                    </ul>
                  ) : null}
                </section>
              ) : null}

              {verdictStr && !chosen ? (
                <EmptyState tone="warn" headline={`Backtest verdict: ${humanKey(verdictStr)}`}
                  reason={bt.reason ?? null}
                  next="Nothing is promoted on an insufficient or unvalidated sample. This is reported exactly as measured." />
              ) : null}

              {chosen ? (
                <>
                  <div className="stat-grid">
                    <StatTile label="Dev expectancy" term="expectancy_r" value={fmtR(chosen.train?.expectancy_r, 2)}
                      n={chosen.train?.trades} source={SRC_BT} evidence="BACKTEST" split="DEV" tone={toneOf(chosen.train?.expectancy_r)} />
                    <StatTile label="Validation expectancy" term="expectancy_r" value={fmtR(chosen.validation?.expectancy_r, 2)}
                      n={chosen.validation?.trades} source={SRC_BT} evidence="BACKTEST" split="VALIDATION" tone={toneOf(chosen.validation?.expectancy_r)} />
                    <StatTile label="Holdout win rate" value={pctText(chosen.test?.win_rate)}
                      n={chosen.test?.trades} source={SRC_BT} evidence="BACKTEST" split="HOLDOUT"
                      sub={chosen.test?.win_rate_wilson_lb !== undefined ? `Conservative floor ${chosen.test.win_rate_wilson_lb}%` : undefined} />
                    <StatTile label="Holdout expectancy" term="expectancy_r" value={fmtR(chosen.test?.expectancy_r, 2)}
                      n={chosen.test?.trades} source={SRC_BT} evidence="BACKTEST" split="HOLDOUT" tone={toneOf(chosen.test?.expectancy_r)} />
                    <StatTile label="Holdout profit factor" term="profit_factor"
                      value={pfText(chosen.test?.profit_factor, chosen.test?.trades ?? 0)}
                      n={chosen.test?.trades} source={SRC_BT} evidence="BACKTEST" split="HOLDOUT" />
                  </div>
                  <section className="panel">
                    <h3>Protocol</h3>
                    <KvGrid obj={bt.protocol} />
                  </section>
                  <section className="panel">
                    <h3>Selected configuration</h3>
                    <KvGrid obj={{ ...chosen.entry, ...chosen.exit }} />
                  </section>
                  {bt.test_breakdowns ? Object.entries(bt.test_breakdowns).map(([k, g]) => (
                    <MetricTable key={k} heading={`Holdout — ${humanKey(k.replace(/^by_/, ''))}`} groups={g}
                      evidence="BACKTEST" split="HOLDOUT" />
                  )) : null}
                </>
              ) : null}

              {bt.coverage?.length ? (
                <section className="panel">
                  <h3>Data coverage</h3>
                  <p className="note">
                    {bt.coverage.filter((c) => c.bars > 0).length} of {bt.coverage.length} series returned data —{' '}
                    {bt.coverage.reduce((a, c) => a + (c.bars || 0), 0).toLocaleString()} bars.
                    {(() => {
                      const empty = Array.from(new Set(bt.coverage!.filter((c) => !c.bars).map((c) => c.timeframe))).filter(Boolean);
                      return empty.length
                        ? ` Series with no data (${empty.map((t) => groupLabel(String(t))).join(', ')}) are reported untested rather than assumed.`
                        : '';
                    })()}
                  </p>
                </section>
              ) : null}
            </>
          )}
        </>
      )}

      {tab === 'Paper signals' && (
        <>
          <SectionHeader title="Paper signals" count={sigs.loaded ? live.length : null}
            question="Is the strategy still producing paper signals, and how have they done?"
            evidence="PAPER"
            caption={overall ? `Paper · Extreme Reversion · ${overall.signals} recorded · ${overall.resolved} resolved` : 'Paper · Extreme Reversion'} />
          <div className="stat-grid">
            <StatTile label="Paper win rate" value={overall?.resolved ? pctText(overall.win_rate) : null}
              n={overall?.resolved} source={SRC_PAPER} evidence="PAPER" loaded={perf.loaded}
              nLabel={overall ? `of ${overall.resolved} resolved · ${overall.signals} recorded` : undefined}
              sub="Observed frequency on resolved paper trades — not a probability that the next trade wins." />
            <StatTile label="Expectancy per trade" term="expectancy_r"
              value={overall?.resolved ? fmtR(overall.expectancy_r, 2) : null}
              n={overall?.resolved} source={SRC_PAPER} evidence="PAPER" loaded={perf.loaded}
              tone={toneOf(overall?.expectancy_r)}
              sub="Average result per trade in units of risk. This matters more than win rate." />
            <StatTile label="Profit factor" term="profit_factor"
              value={overall?.resolved ? pfText(overall.profit_factor, overall.resolved) : null}
              n={overall?.resolved} source={SRC_PAPER} evidence="PAPER" loaded={perf.loaded} />
          </div>

          <div className="row" style={{ gap: 8, marginBottom: 14 }} role="tablist" aria-label="Signal filter">
            {FILTERS.map((f) => (
              <button key={f.key || 'all'} type="button" role="tab" aria-selected={filter === f.key}
                className={`tab ${filter === f.key ? 'on' : ''}`} onClick={() => setFilter(f.key)}>{f.label}</button>
            ))}
          </div>

          {!sigs.loaded ? (
            <EmptyState loaded={false} headline="Loading signals" reason={null} />
          ) : !rows.length ? (
            <EmptyState headline="No signals in this view yet"
              reason={`The strategy requires price to pierce ${bbDev !== undefined ? `a ${bbDev}σ` : 'an extreme'} Bollinger band while RSI is at an extreme, and then close back inside the band with RSI turning. That combination is deliberately rare — long quiet stretches are expected and are not a fault.`}
              next="The worker records every scan; its heartbeat is on System health."
              action={{ label: 'System health', href: '/health' }} />
          ) : (
            <div className="roadmap-grid">
              {rows.map((s) => (
                <TradeRoadmap key={s.signal_uid} rm={s.roadmap} symbol={s.symbol}
                  timeframe={s.timeframe} score={s.score} scoreBand={s.score_band}
                  status={s.status} indicators={s.indicators} plan={s.trade_plan}
                  parameters={s.parameters} events={s.events} />
              ))}
            </div>
          )}
        </>
      )}

      {tab === 'Performance' && (
        <>
          <SectionHeader title="Paper performance by group" question="Where did the paper trades do better or worse?"
            evidence="PAPER" caption="Paper · Extreme Reversion · resolved paper trades only" />
          <section className="panel">
            <h3>Three separate records, never blended</h3>
            <p className="note">{perf.data?.separation_note ?? 'Backtest, paper and live-observed results are stored and reported separately.'}</p>
          </section>
          <MetricTable heading="By variant" groups={paperGroups('by_variant')} evidence="PAPER" loaded={perf.loaded} />
          <MetricTable heading="By asset class" groups={paperGroups('by_asset_class')} evidence="PAPER" loaded={perf.loaded}
            note="Stock and crypto statistics are kept apart — they are different instruments." />
          <MetricTable heading="By timeframe" groups={paperGroups('by_timeframe')} evidence="PAPER" loaded={perf.loaded} />
          <MetricTable heading="By market regime" groups={paperGroups('by_regime')} evidence="PAPER" loaded={perf.loaded} />
          <MetricTable heading="By setup-quality band" groups={paperGroups('by_score_band')} evidence="PAPER" loaded={perf.loaded}
            note="If a higher score does not produce better results, the score is not yet informative and gets reweighted." />
          <MetricTable heading="By session" groups={paperGroups('by_session')} evidence="PAPER" loaded={perf.loaded} />
          <MetricTable heading="By symbol" groups={paperGroups('by_symbol')} evidence="PAPER" loaded={perf.loaded} />
        </>
      )}

      {tab === 'Strategy lab' && (
        <>
          <SectionHeader title="Strategy lab" question="Which variants exist, how is setup quality scored, and what ranges were tested?" />
          {!cfg.loaded ? (
            <EmptyState loaded={false} headline="Loading configuration" reason={null} />
          ) : !cfg.data ? (
            <EmptyState headline="Configuration unavailable" reason={cfg.err?.message ?? null} />
          ) : (
            <>
              <section className="panel">
                <h3>Variants — kept permanently, never overwritten</h3>
                <DataTable<[string, { label: string; note: string }]>
                  rows={Object.entries(cfg.data.variants)} rowKey={([k]) => k} dense
                  columns={[
                    { key: 'variant', header: 'Variant', align: 'l', simple: true,
                      cell: ([k, v]) => <><b>{v.label}</b>{advanced ? <> <code className="pill-raw">{k}</code></> : null}</> },
                    { key: 'version', header: 'Version', cell: ([k]) => <span className="mono">{cfg.data!.versions[k] ?? '—'}</span> },
                    { key: 'note', header: 'What it adds', align: 'l', simple: true,
                      cell: ([, v]) => <span style={{ whiteSpace: 'normal', display: 'inline-block', maxWidth: 520 }}>{v.note}</span> },
                  ]} />
              </section>
              <section className="panel">
                <h3>Setup-quality weights</h3>
                <p className="note" style={{ marginBottom: 8 }}>
                  A starting hypothesis, not a validated model. Weights get reweighted from realised results once the sample supports it.
                </p>
                <KvGrid obj={cfg.data.score_weights} />
              </section>
              <section className="panel">
                <h3>Tested parameter ranges</h3>
                <KvGrid obj={cfg.data.test_ranges} />
              </section>
            </>
          )}
        </>
      )}

      <style href="tf-reversion" precedence="default">{`
        .panel-hd { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
        .panel-hd h3 { margin: 0; }
        .panel--risk { border-color: rgba(248, 113, 113, 0.4); background: rgba(248, 113, 113, 0.06); }
      `}</style>
    </>
  );
}
