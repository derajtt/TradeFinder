'use client';
import { MetricLine, NotInReport, floorOf, pctSigned, pfText, ratePct, type BtMetric } from '../../components/BacktestBits';
import {
  Advanced, DataTable, EmptyState, EvidenceTag, SectionHeader, StatusPill, Term, pillFor, type Column,
} from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { PROMOTION_MIN_TRADES_FALLBACK, type BacktestSplit } from '../../lib/evidence';
import { fmtEtDate } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { humanKey, type Label } from '../../lib/vocab';

/* ── /api/backtest/report (backend/app/routes/api.py backtest_report; result from
      backend/scripts/backtest_run.py or bt_analyze.py) ── */
interface Primary {
  strategy_version?: string; mode?: string; exit?: string;
  entry?: string | Record<string, unknown>; holdout_pass?: boolean | null;
}
interface Result {
  primary?: Primary;
  coverage_notes?: string[];
  splits?: { dev?: string[]; val?: string[]; holdout?: string[] };
  search?: { dev_metrics?: BtMetric; val_metrics?: BtMetric; jitter_ok?: boolean;
             configs_tested?: number; converged_because?: string; pbo?: number };
  walk_forward?: { combined?: BtMetric; folds?: unknown[] };
  holdout?: { baseline?: BtMetric; pessimistic?: BtMetric; sessions?: number };
  configs_tested?: number; rounds?: number; converged_because?: string; pbo?: number;
  api_calls?: number; cache_hits?: number;
  tournament?: Record<string, unknown>;
}
interface Report { available: boolean; note?: string; config_hash?: string; created_at?: string; kind?: string; result?: Result }

/* ── /api/backtest/reports (latest job per kind) ── */
interface FleetResult {
  cohort?: string; resolution?: string; sessions?: number; date_range?: (string | null)[];
  trades_total?: number; by_model?: Record<string, BtMetric>; forward_only_models?: string[];
}
interface NightlyResult {
  replay?: { date?: string; candidates?: number; signals?: number; early?: number; rejects?: number; paper_trades?: number };
  promotion?: { decision?: string; reason?: string; min_trades?: number;
                requirements?: { min_forward_trades?: number | null; better_reliable_wr?: unknown; positive_expectancy?: unknown };
                audit?: string };
}
interface Reports {
  kinds?: string[];
  reports?: { fleet?: { created_at: string; config_hash: string; result: FleetResult };
              nightly?: { created_at: string; config_hash: string; result: NightlyResult };
              primary?: { created_at: string; config_hash: string; result: Result } };
}

const DECISION: Record<string, Label> = {
  hold: { label: 'On hold', tone: 'neutral' },
  promote: { label: 'Promoted', tone: 'buy' },
  promoted: { label: 'Promoted', tone: 'buy' },
  reject: { label: 'Rejected', tone: 'risk' },
};

const isDate = (s: string | undefined) => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s);

function Kv({ k, children }: { k: React.ReactNode; children: React.ReactNode }) {
  return <div className="kv"><div className="k">{k}</div><div className="v sans">{children}</div></div>;
}

interface SplitRow { key: string; split: BacktestSplit; name: string; range: React.ReactNode; m: BtMetric | null | undefined }
interface FleetRow { id: string; m: BtMetric }

export default function BacktestPage() {
  const rep = usePollingState<Report>('/api/backtest/report', 60000);
  const all = usePollingState<Reports>('/api/backtest/reports', 60000);
  const { advanced } = useMode();
  const r = rep.data;
  const fleet = all.data?.reports?.fleet?.result;
  const nightly = all.data?.reports?.nightly?.result;

  const header = (caption?: React.ReactNode) => (
    <SectionHeader level={1} title="Backtests" evidence="BACKTEST"
      question="What did the historical simulation of this strategy show, honestly?" caption={caption} />
  );

  if (!rep.loaded) return <>{header()}<EmptyState loaded={false} headline="Loading report" reason={null} /></>;
  if (!r || !r.available) {
    return (
      <>
        {header()}
        <EmptyState headline="No backtest imported yet"
          reason={r?.note ?? rep.err?.message ?? null}
          next="Run backend/scripts/backtest_run.py, then bt_import.py, to fill this page." />
      </>
    );
  }

  const res = r.result ?? {};
  const hash = r.config_hash || '';
  const configLabel = hash ? (isDate(hash) ? `config date ${hash}` : `config ${hash}`) : 'config —';
  const primary = res.primary;
  const search = res.search;
  const wf = res.walk_forward;
  const hold = res.holdout;
  const hasSplits = !!(res.splits || search?.dev_metrics || search?.val_metrics || wf || hold);
  const hasSearch = res.configs_tested !== undefined || res.rounds !== undefined || !!search;

  const splitRows: SplitRow[] = [
    { key: 'dev', split: 'DEV', name: 'Development', range: res.splits?.dev?.join(' → ') ?? '—', m: search?.dev_metrics },
    { key: 'val', split: 'VALIDATION', name: 'Validation', range: res.splits?.val?.join(' → ') ?? '—', m: search?.val_metrics },
    { key: 'wf', split: 'WALK_FORWARD', name: 'Walk-forward (unseen blocks)',
      range: `${wf?.folds?.length ?? '—'} folds`, m: wf?.combined },
    { key: 'hold', split: 'HOLDOUT', name: 'Untouched holdout (one look)', range: res.splits?.holdout?.join(' → ') ?? '—', m: hold?.baseline },
    { key: 'hold-pess', split: 'HOLDOUT', name: 'Holdout · worst-case fills', range: '', m: hold?.pessimistic },
  ];
  const splitCols: Column<SplitRow>[] = [
    { key: 'cohort', header: 'Cohort', align: 'l', simple: true,
      cell: (x) => <span className="stock-cell"><EvidenceTag evidence="BACKTEST" split={x.split} /> {x.name}</span> },
    { key: 'range', header: 'Range', align: 'l', simple: true, cell: (x) => <span className="dim">{x.range}</span> },
    { key: 'result', header: 'Result (baseline fills)', align: 'l', simple: true, cell: (x) => <MetricLine m={x.m} /> },
  ];

  const fleetRows: FleetRow[] = Object.entries(fleet?.by_model ?? {}).map(([id, m]) => ({ id, m }));
  // The report stores the range in whatever order it was built; always read min → max.
  const fleetRange = ((): [string, string] => {
    const ds = (fleet?.date_range ?? []).filter((d): d is string => !!d).sort();
    return [ds[0] ?? '—', ds[ds.length - 1] ?? '—'];
  })();
  const fleetCols: Column<FleetRow>[] = [
    { key: 'model', header: 'Model', align: 'l', simple: true, sortValue: (x) => x.id,
      cell: (x) => <>{humanKey(x.id)}{advanced ? <> <code className="pill-raw">{x.id}</code></> : null}</> },
    { key: 'n', header: 'Trades', simple: true, sortValue: (x) => x.m.n, cell: (x) => x.m.n },
    { key: 'wr', header: 'Win rate', simple: true, sortValue: (x) => x.m.win_rate, cell: (x) => ratePct(x.m.win_rate) },
    { key: 'floor', header: 'Conservative floor', term: 'conservative_floor', simple: true,
      sortValue: (x) => floorOf(x.m), cell: (x) => ratePct(floorOf(x.m)) },
    { key: 'exp', header: 'Expectancy', simple: true, sortValue: (x) => x.m.expectancy_pct,
      cell: (x) => <span className={(x.m.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{pctSigned(x.m.expectancy_pct)}</span> },
    { key: 'pf', header: 'Profit factor', term: 'profit_factor', sortValue: (x) => x.m.profit_factor, cell: (x) => pfText(x.m.profit_factor, x.m.n) },
    { key: 'dd', header: 'Sum of trade %', term: 'drawdown_sum', simple: true, sortValue: (x) => x.m.max_drawdown_pct,
      cell: (x) => x.m.max_drawdown_pct == null ? '—' : <span className="neg">{Math.abs(x.m.max_drawdown_pct).toFixed(1)}%</span> },
    { key: 'amb', header: 'Unclear fills', term: 'ambiguous', sortValue: (x) => x.m.ambiguous ?? null, cell: (x) => x.m.ambiguous ?? '—' },
    { key: 'h1', header: 'First half expectancy', sortValue: (x) => x.m.first_half_exp ?? null, cell: (x) => pctSigned(x.m.first_half_exp) },
    { key: 'h2', header: 'Second half expectancy', sortValue: (x) => x.m.second_half_exp ?? null, cell: (x) => pctSigned(x.m.second_half_exp) },
  ];

  /* nightly promotion line — computed client-side, never trusted from the decision alone */
  const paperN = nightly?.replay?.paper_trades ?? null;
  const promoMin = nightly?.promotion?.min_trades ?? nightly?.promotion?.requirements?.min_forward_trades ?? PROMOTION_MIN_TRADES_FALLBACK;
  const decision = nightly?.promotion?.decision;
  const meets = paperN === null ? null : paperN >= promoMin;
  const inconsistent = meets === false && (decision === 'promote' || decision === 'promoted');

  return (
    <>
      {header(<>Report imported {fmtEtDate(r.created_at)} · {configLabel}{r.kind ? <> · kind {humanKey(r.kind).toLowerCase()}</> : null}</>)}

      <SectionHeader title="Primary policy (installed)" question="Which version of the rules is the paper account running, and did it pass its holdout?" />
      {primary ? (
        <>
          <div className="kv-grid">
            <Advanced>
              <Kv k="Strategy version"><span className="mono">{primary.strategy_version ?? '—'}</span></Kv>
            </Advanced>
            <Kv k="Mode">{primary.mode ? humanKey(primary.mode) : '—'}</Kv>
            <Kv k="Exit policy">{primary.exit ?? '—'}</Kv>
            <Kv k="Holdout passed">
              {primary.holdout_pass === true ? <StatusPill size="sm" label="Passed" tone="buy" />
                : primary.holdout_pass === false ? <StatusPill size="sm" label="Failed" tone="risk" />
                : <StatusPill size="sm" label="Not evaluated" tone="neutral" />}
            </Kv>
          </div>
          {typeof primary.entry === 'string' ? <p className="note">Entry gates: {primary.entry}</p> : null}
          {primary.entry && typeof primary.entry === 'object' ? (
            <Advanced>
              <div className="kv-grid">
                {Object.entries(primary.entry).map(([k, v]) => <Kv key={k} k={humanKey(k)}>{String(v)}</Kv>)}
              </div>
            </Advanced>
          ) : null}
        </>
      ) : <NotInReport what="Primary policy" />}

      <SectionHeader title="Data coverage" question="What data was and was not available to the simulation?" />
      {res.coverage_notes?.length ? (
        <div className="timeline">
          {res.coverage_notes.map((n, i) => <div className="tl-item" key={i}><span>{n}</span></div>)}
        </div>
      ) : <NotInReport what="Coverage notes" />}

      <SectionHeader title="Splits and results" question="How did the rules do on the data they were tuned on versus data they never saw?"
        caption="Development, validation, walk-forward and holdout are separate cohorts and are never combined." />
      {hasSplits ? (
        <DataTable<SplitRow> rows={splitRows} columns={splitCols} rowKey={(x) => x.key} minWidth={720} suppressEmptyAbove={1}
          note="Result reads: trades · share won · conservative floor · expectancy per trade · profit factor · worst run as a sum of trade %" />
      ) : <NotInReport what="Splits" />}

      <SectionHeader title="Search discipline" question="How hard did we search, and how likely is the best result to be luck?" />
      {hasSearch ? (
        <div className="kv-grid">
          <Kv k="Configs tested">{res.configs_tested ?? search?.configs_tested ?? '—'}</Kv>
          <Kv k="Rounds">{res.rounds ?? '—'}</Kv>
          <Kv k="Stopped because">{res.converged_because ?? search?.converged_because ?? '—'}</Kv>
          <Kv k={<Term k="PBO">Overfitting estimate</Term>}>{res.pbo ?? search?.pbo ?? '—'}</Kv>
          <Kv k="Robust to small changes">
            {search?.jitter_ok === true ? <StatusPill size="sm" label="Yes" tone="buy" />
              : search?.jitter_ok === false ? <StatusPill size="sm" label="No" tone="risk" />
              : <span className="dim">Not in this report</span>}
          </Kv>
          <Advanced>
            <Kv k="API calls / cache hits">{res.api_calls ?? '—'} / {res.cache_hits ?? '—'}</Kv>
          </Advanced>
        </div>
      ) : <NotInReport what="Search discipline" />}

      <SectionHeader title="Fleet backtests — every daily-testable model" evidence="BACKTEST"
        question="How did each strategy do on frozen first-pass settings, with no tuning?"
        caption={fleet ? <>{fleet.cohort ? humanKey(fleet.cohort) : 'Replay'} · {fleet.sessions ?? '—'} sessions
          ({fleetRange[0]} → {fleetRange[1]}) · {fleet.trades_total ?? '—'} trades · frozen first-pass settings</> : undefined} />
      {!all.loaded ? <EmptyState loaded={false} headline="Loading fleet report" reason={null} />
        : fleet?.by_model ? (
          <>
            <DataTable<FleetRow> rows={fleetRows} columns={fleetCols} rowKey={(x) => x.id} minWidth={820}
              defaultSort={{ key: 'floor', dir: 'desc' }}
              note="Win rate = wins ÷ resolved trades; conservative floor = Wilson lower bound on that rate." />
            {fleet.forward_only_models?.length ? (
              <p className="note" style={{ marginTop: 8 }}>
                Cannot be honestly tested on daily bars (forward paper only): {fleet.forward_only_models.map((x) => humanKey(x)).join(', ')}.
                Negative baselines are shown as-is — engines are not retuned to fit history; paper trading decides.
              </p>
            ) : null}
          </>
        ) : <NotInReport what="Fleet backtests" />}

      <SectionHeader title="Nightly research — latest run" question="What did last night's replay of the day find, and is the paper sample big enough to promote anything?" />
      {!all.loaded ? <EmptyState loaded={false} headline="Loading nightly report" reason={null} />
        : nightly ? (
          <>
            {nightly.replay ? (
              <div className="kv-grid">
                {Object.entries(nightly.replay).map(([k, v]) => <Kv key={k} k={humanKey(k)}>{v === null || v === undefined ? '—' : String(v)}</Kv>)}
              </div>
            ) : <NotInReport what="Replay" />}
            <div className="row" style={{ gap: 10, marginTop: 8 }}>
              {decision ? <StatusPill size="sm" {...pillFor(DECISION, decision)} /> : null}
              {paperN !== null ? (
                <span>Paper sample {paperN}/{promoMin} — {meets ? 'meets' : 'below'} the promotion minimum</span>
              ) : (
                <span className="dim">Paper sample not reported as a number in this report — promotion minimum {promoMin} trades.</span>
              )}
              {inconsistent ? <StatusPill size="sm" label="inconsistent — see System" tone="warn" href="/health" /> : null}
            </div>
            {paperN === null && nightly.promotion?.reason ? <p className="note">{nightly.promotion.reason}</p> : null}
            {paperN !== null && nightly.promotion?.reason ? (
              <Advanced><p className="note mono">{nightly.promotion.reason}</p></Advanced>
            ) : null}
          </>
        ) : <NotInReport what="Nightly research" />}

      <p className="disclaimer">Backtests use estimated spreads and next-bar fills on 5-minute data; they are evidence, not proof.
        Nothing here is promoted to the paper strategy without walk-forward + holdout + forward paper confirmation.</p>
    </>
  );
}
