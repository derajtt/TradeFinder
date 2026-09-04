'use client';
import { useMemo } from 'react';
import { Advanced, DataTable, EmptyState, SectionHeader, StatTile, Term, type Column } from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { SAMPLE, wilsonLower } from '../../lib/evidence';
import { fmtPct } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { useAppSettings } from '../../lib/status';
import type { Canonical, Noon, PerfGroup, PerfPayload } from '../../lib/types';
import { OUTCOME, catalystLabel, humanKey } from '../../lib/vocab';
import s from './page.module.css';

/* ── breakdown tables ─────────────────────────────────────────────────────── */

interface GroupCfg { key: string; title: string; bucketHeader: string; label: (k: string) => string }
const GROUPS: GroupCfg[] = [
  { key: 'score_band', title: 'By score band', bucketHeader: 'Score band (out of 100)',
    label: (k) => (k === 'unknown' ? 'No score recorded' : k) },
  { key: 'catalyst', title: 'By news type', bucketHeader: 'News type', label: (k) => catalystLabel(k) },
  { key: 'market_cap', title: 'By company size', bucketHeader: 'Company size (market cap)',
    label: (k) => (k === 'unknown' ? 'Size unknown' : k) },
  { key: 'strategy_version', title: 'By engine version', bucketHeader: 'Engine version', label: (k) => `engine v${String(k).replace(/^v/i, '')}` },
];
function groupCfg(key: string): GroupCfg {
  return GROUPS.find((g) => g.key === key)
    ?? { key, title: `By ${humanKey(key).toLowerCase()}`, bucketHeader: humanKey(key), label: (k) => k };
}

interface Bucket extends PerfGroup { key: string; label: string }

function pctOf(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? '—' : `${Math.round(v * 100)}%`;
}
function signCls(v: number | null | undefined): string | undefined {
  if (v == null || v === 0) return undefined;
  return v > 0 ? 'pos' : 'neg';
}
/** True when every bucket carries win/neutral/loss, so the rate can be recomputed on the decided basis. */
function decidedBasis(b: Bucket[]): boolean {
  return b.length > 0 && b.every((x) => x.win != null && x.loss != null && x.neutral != null);
}
function decidedOf(b: Bucket): number {
  return (b.win ?? 0) + (b.loss ?? 0);
}

function Rate({ v, n }: { v: number | null | undefined; n: number }) {
  if (v == null) return <>—</>;
  return <>{pctOf(v)}{n < SAMPLE.judge ? <span className={`stat-warn ${s.mark}`}>too few</span> : null}</>;
}

function Breakdown({ g, items }: { g: GroupCfg; items: Record<string, PerfGroup> }) {
  const { advanced } = useMode();
  const buckets = useMemo<Bucket[]>(
    () => Object.entries(items).map(([k, v]) => ({ ...v, key: k, label: g.label(k) })).filter((b) => b.n > 0),
    [items, g]);
  const decided = decidedBasis(buckets);
  const columns = useMemo<Column<Bucket>[]>(() => {
    const cols: Column<Bucket>[] = [
      { key: 'label', header: g.bucketHeader, align: 'l', simple: true, sortValue: (b) => b.label, cell: (b) => b.label },
      { key: 'n', header: 'Buy picks', simple: true, sortValue: (b) => b.n, cell: (b) => b.n },
    ];
    if (decided) {
      cols.push(
        { key: 'decided', header: 'Decided n', simple: true, sortValue: (b) => decidedOf(b), cell: (b) => decidedOf(b) },
        { key: 'rate', header: <Term k="early_pop">Early pop rate (decided only)</Term>, simple: true,
          sortValue: (b) => (decidedOf(b) ? (b.win ?? 0) / decidedOf(b) : null),
          cell: (b) => <Rate v={decidedOf(b) ? (b.win ?? 0) / decidedOf(b) : null} n={decidedOf(b)} /> },
      );
    } else {
      cols.push({ key: 'rate', header: <Term k="pop_rate_incl_flat">Early pop rate (incl. flat)</Term>, simple: true,
        sortValue: (b) => b.win_rate, cell: (b) => <Rate v={b.win_rate} n={b.n} /> });
    }
    cols.push(
      { key: 'avg_change_pct', header: 'Avg move since pick', simple: true, sortValue: (b) => b.avg_change_pct,
        cell: (b) => <span className={signCls(b.avg_change_pct)}>{fmtPct(b.avg_change_pct)}</span> },
      // ── Advanced-only from here ──
      { key: 'avg_max_gain_pct', header: 'Avg max gain', sortValue: (b) => b.avg_max_gain_pct,
        cell: (b) => <span className="pos">{fmtPct(b.avg_max_gain_pct)}</span> },
      { key: 'avg_max_drawdown_pct', header: 'Avg max DD', sortValue: (b) => b.avg_max_drawdown_pct,
        cell: (b) => <span className="neg">{fmtPct(b.avg_max_drawdown_pct)}</span> },
    );
    return cols;
  }, [g, decided]);

  // Fewer than two informative buckets is not a breakdown.
  if (buckets.length < 2) return <p className={`dim ${s.nobreak}`}>{g.title}: no breakdown yet</p>;

  const what = g.title.replace(/^By /, '').toLowerCase();
  return (
    <>
      <SectionHeader title={g.title} question={`Do picks work out differently by ${what}?`}
        caption="All models · Buy picks only" />
      <DataTable<Bucket> rows={buckets} columns={columns} rowKey={(b) => b.key}
        defaultSort={{ key: 'n', dir: 'desc' }} evidence="TRACKED" minWidth={620}
        note={
          <>
            Rate denominator is the &quot;Buy picks&quot;{decided ? ' and "Decided n"' : ''} column
            {advanced ? ' · Avg max DD: negative = drop from pick price' : ''}
          </>
        } />
    </>
  );
}

/* ── by pick type (Advanced) ──────────────────────────────────────────────── */

interface TypeRow { type: string; win: number; neutral: number; loss: number; pending: number }
const TYPE_COLS: Column<TypeRow>[] = [
  { key: 'type', header: 'Pick type', align: 'l', simple: true,
    cell: (r) => (r.type === 'buy' ? 'Buy picks' : r.type === 'watch' ? 'Watching' : humanKey(r.type)) },
  { key: 'win', header: OUTCOME.win.label, simple: true, sortValue: (r) => r.win, cell: (r) => <span className="pos">{r.win}</span> },
  { key: 'neutral', header: OUTCOME.neutral.label, simple: true, sortValue: (r) => r.neutral, cell: (r) => r.neutral },
  { key: 'loss', header: OUTCOME.loss.label, simple: true, sortValue: (r) => r.loss, cell: (r) => <span className="neg">{r.loss}</span> },
  { key: 'pending', header: OUTCOME.pending.label, simple: true, sortValue: (r) => r.pending, cell: (r) => <span className="dim">{r.pending}</span> },
  { key: 'rate', header: <Term k="early_pop">Early pop rate (decided only)</Term>, simple: true,
    sortValue: (r) => (r.win + r.loss ? r.win / (r.win + r.loss) : null),
    cell: (r) => <Rate v={r.win + r.loss ? r.win / (r.win + r.loss) : null} n={r.win + r.loss} /> },
];

/* ── page ─────────────────────────────────────────────────────────────────── */

function floorPct(wins: number, n: number): string {
  const w = wilsonLower(wins, n);
  return w == null ? '—' : `${Math.round(w * 100)}%`;
}

export default function PerformancePage() {
  const perf = usePollingState<PerfPayload>('/api/performance', 60000);
  const canon = usePollingState<Canonical>('/api/report/canonical', 60000);
  const noon = usePollingState<Noon>('/api/outcomes/noon', 60000);
  const { settings } = useAppSettings();
  const { advanced } = useMode();

  const o = perf.data?.outcomes;
  const decided = o ? o.win + o.loss : 0;
  const abp = canon.data?.actionable_buy_performance;
  const nn = noon.data;
  const noonWins = nn ? (nn.counts.WIN_10_TOUCH ?? 0) + (nn.counts.WIN_NOON_GREEN ?? 0) : 0;
  const earlyMin = settings?.early_window_min;
  const groups = perf.data?.groups;

  const typeRows = useMemo<TypeRow[]>(() => Object.entries(o?.by_type ?? {}).map(([type, c]) => ({
    type, win: c.win ?? 0, neutral: c.neutral ?? 0, loss: c.loss ?? 0, pending: c.pending ?? 0,
  })), [o]);

  return (
    <>
      <SectionHeader level={1} title="Scorecard" question="Overall, how often do picks work out?"
        caption="All models · demo picks excluded · three separate measurements, never blended" />

      {/* Definitions identical to Today's TrustTiles (§2.6), scope "All models". */}
      <div className="stat-grid">
        <StatTile label="Early pops" term="early_pop" loaded={perf.loaded} tone="early"
          value={o?.win_rate != null ? `${Math.round(o.win_rate * 100)}% popped` : null}
          n={o ? decided : null} unit="picks"
          nLabel={o ? `of ${decided} decided picks · ${o.neutral} flat not counted · ${o.pending} pending` : undefined}
          source={`All models · watches and buys · ${earlyMin != null ? `judged in the first ${earlyMin} min` : 'judged in the early window'}`}
          evidence="TRACKED" />
        <StatTile label="Paper trades" loaded={canon.loaded}
          value={abp?.win_rate != null ? `${Math.round(abp.win_rate * 100)}% won` : null}
          n={abp ? abp.closed_trades : null}
          source="Paper account · All models · Buy picks only" evidence="PAPER"
          sub={abp ? (
            <>
              {abp.calibration !== 'calibrated' ? <div>{abp.note || 'Not enough trades yet to trust this rate'}</div> : null}
              <Advanced>
                <div>
                  <Term k="conservative_floor">Conservative floor</Term> {floorPct(abp.wins, abp.closed_trades)}
                  {' · '}{abp.open_positions} open
                </div>
              </Advanced>
            </>
          ) : undefined} />
        <StatTile label="Noon check" term="noon_check" loaded={noon.loaded} tone="early"
          value={nn?.call_win_rate != null ? `${Math.round(nn.call_win_rate * 100)}% green at noon` : null}
          n={nn ? nn.denominator : null} unit="picks"
          nLabel={nn && nn.denominator > 0 ? `of ${nn.denominator} picks` : undefined}
          source="All models · was the pick above its pick price at 12:00 ET" evidence="TRACKED"
          sub={nn && advanced ? (
            <>
              <Term k="conservative_floor">Conservative floor</Term> {floorPct(noonWins, nn.denominator)}
              {' · '}{nn.counts.INCOMPLETE ?? 0} incomplete not counted
            </>
          ) : undefined} />
      </div>

      {perf.loaded && perf.data ? (
        <div className={s.denom}>
          <div className="dim">
            {perf.data.total_signals} Buy picks recorded
            {o ? <> · {decided} decided · {o.neutral} flat · {o.pending} pending</> : null}
          </div>
          <div className="faint">
            Today&apos;s page counts only the selected strategy; this page counts all models.
            Decided, flat and pending include watches as well as buys.
          </div>
        </div>
      ) : null}

      {perf.err && !perf.data ? (
        <EmptyState tone="risk" headline="Could not load the scorecard" reason={perf.err.message} />
      ) : null}
      {perf.loaded && perf.data && perf.data.total_signals === 0 ? (
        <EmptyState headline="No Buy picks recorded yet"
          reason="Aggregates appear after the first genuine Buy pick is tracked (demo picks are excluded)." />
      ) : null}

      <Advanced>
        {typeRows.length > 1 ? (
          <>
            <SectionHeader title="By pick type" question="Do Buy picks pop more often than watches?"
              caption="Tracked · All models · watches and buys" />
            <DataTable<TypeRow> rows={typeRows} columns={TYPE_COLS} rowKey={(r) => r.type}
              defaultSort={{ key: 'win', dir: 'desc' }} evidence="TRACKED" minWidth={520} />
          </>
        ) : null}
      </Advanced>

      {perf.loaded && groups && (perf.data?.total_signals ?? 0) > 0
        ? Object.keys(groups).map((key) => <Breakdown key={key} g={groupCfg(key)} items={groups[key]} />)
        : null}

      <p className="disclaimer">
        Aggregates measure signal behaviour only; they include no fills, slippage or costs and are not a
        performance guarantee.
      </p>
    </>
  );
}
