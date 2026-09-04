'use client';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import {
  DataTable, Details, EmptyState, EvidenceTag, SectionHeader, Term, type Column,
} from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { SAMPLE } from '../../lib/evidence';
import { fmtDrawdown, fmtMult, fmtPrice, fmtR } from '../../lib/format';
import type { AccuracyPayload, AccuracyRow } from '../../lib/types';
import { humanKey, sampleLabel } from '../../lib/vocab';
import s from './page.module.css';

const SORT_LABEL: Record<string, string> = {
  paper_trades: 'Trades', expectancy_r: 'Expectancy', paper_win_rate: 'Paper win %',
  oos_win_rate: 'Holdout win %', max_drawdown_pct: 'Worst dip',
};

/** Backend win rates on this endpoint are already 0–100. */
function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(1)}%`;
}
/** The backend's `sample` is a constant "INSUFFICIENT DATA" for platform rows (risk_api.py),
 *  so the class is derived from the paper trade count with the backend's own 30/100/500 cut-offs;
 *  the backend label is used only when there is no count to go on. */
function paperSample(r: AccuracyRow): string {
  const n = r.paper_trades;
  if (n == null) return sampleLabel(r.sample, null);
  if (n < SAMPLE.judge) return `too few trades (${n} of ${SAMPLE.judge} needed)`;
  if (n < SAMPLE.early) return `early sample (${n} of ${SAMPLE.early})`;
  if (n < 500) return `moderate sample (${n} trades)`;
  return `strong sample (${n} trades)`;
}
/** `oos_sample` arrives as a class label ("EARLY") on this endpoint; a number means a count. */
function oosSample(v: unknown): string | null {
  if (v == null || v === '') return null;
  if (typeof v === 'number') return `${v} trades`;
  const k = String(v).toUpperCase();
  const words: Record<string, string> = {
    'INSUFFICIENT DATA': 'too few trades', EARLY: 'early sample',
    'MODERATE SAMPLE': 'moderate sample', 'STRONGER SAMPLE': 'strong sample',
  };
  return words[k] ?? humanKey(k.toLowerCase()).toLowerCase();
}
function hasResults(r: AccuracyRow): boolean {
  return r.paper_trades > 0
    || [r.backtest_win_rate, r.oos_win_rate, r.paper_win_rate, r.expectancy_r, r.profit_factor].some((v) => v != null);
}
function riskModel(r: AccuracyRow): string {
  return r.risk_model === 'standard' ? 'platform risk layer' : `${humanKey(r.risk_model).toLowerCase()} risk model`;
}
function signCls(v: number | null | undefined): string | undefined {
  if (v == null || v === 0) return undefined;
  return v > 0 ? 'pos' : 'neg';
}

/** Three evidence classes live in this table by design (the page's question is
 *  whether they agree), so each win-rate column carries its own EvidenceTag in
 *  the header and no cell ever mixes two. Paper-derived columns say "· paper".
 *  Drawdown basis: `/api/accuracy max_drawdown_pct` is the paper ledger → 'account'. */
const COLUMNS: Column<AccuracyRow>[] = [
  { key: 'name', header: 'Strategy', align: 'l', simple: true, sortValue: (r) => r.name,
    cell: (r) => (
      <div className={s.name}>
        <span className={s.nameRow}>
          <span className="dot" style={{ background: r.color }} />
          <Link href={`/models/${r.id}`}>{r.name}</Link>
        </span>
        <span className={s.cap}>{paperSample(r)} · {riskModel(r)}</span>
      </div>
    ) },
  { key: 'version', header: <Term k="engine_version">Version</Term>, align: 'l', sortValue: (r) => r.version,
    cell: (r) => (r.version ? <code>{r.version}</code> : '—') },
  { key: 'paper_trades', header: 'Trades · paper', simple: true, sortValue: (r) => r.paper_trades,
    cell: (r) => (
      <>
        {r.paper_trades}
        {r.paper_trades > 0 && r.paper_trades < SAMPLE.judge ? <span className={`stat-warn ${s.mark}`}>too few</span> : null}
      </>
    ) },
  { key: 'backtest_win_rate', simple: true, sortValue: (r) => r.backtest_win_rate,
    header: <span className={s.hdr}><Term k="backtest_dev">Win %</Term><EvidenceTag evidence="BACKTEST" split="DEV" /></span>,
    cell: (r) => pct(r.backtest_win_rate) },
  { key: 'oos_win_rate', simple: true, sortValue: (r) => r.oos_win_rate,
    header: <span className={s.hdr}><Term k="backtest_holdout">Win %</Term><EvidenceTag evidence="BACKTEST" split="HOLDOUT" /></span>,
    cell: (r) => (r.oos_win_rate == null ? '—' : (
      <div className={s.name}>
        <b>{pct(r.oos_win_rate)}</b>
        {oosSample(r.oos_sample) ? <span className={s.cap}>{oosSample(r.oos_sample)}</span> : null}
      </div>
    )) },
  { key: 'paper_win_rate', simple: true, sortValue: (r) => r.paper_win_rate,
    header: <span className={s.hdr}>Win %<EvidenceTag evidence="PAPER" /></span>,
    cell: (r) => pct(r.paper_win_rate) },
  { key: 'expectancy_r', header: <><Term k="expectancy_r">Expectancy</Term> · paper</>, simple: true,
    sortValue: (r) => r.expectancy_r,
    cell: (r) => <span className={signCls(r.expectancy_r)}>{fmtR(r.expectancy_r, 2)}</span> },
  { key: 'profit_factor', header: <><Term k="profit_factor">Profit factor</Term> · paper</>, simple: true,
    sortValue: (r) => r.profit_factor,
    cell: (r) => (r.profit_factor == null || (r.profit_factor === 0 && r.paper_trades === 0) ? '—' : fmtMult(r.profit_factor, 2)) },
  { key: 'max_drawdown_pct', header: <Term k="drawdown_account">Worst dip (of $10k paper account)</Term>, simple: true,
    sortValue: (r) => r.max_drawdown_pct,
    cell: (r) => (r.max_drawdown_pct == null || r.paper_trades === 0 ? '—' : fmtDrawdown(r.max_drawdown_pct, 'account')) },
  // ── Advanced-only from here ──
  { key: 'oos_expectancy_r', sortValue: (r) => r.oos_expectancy_r ?? null,
    header: <span className={s.hdr}>Expectancy<EvidenceTag evidence="BACKTEST" split="HOLDOUT" /></span>,
    cell: (r) => <span className={signCls(r.oos_expectancy_r)}>{fmtR(r.oos_expectancy_r, 2)}</span> },
  { key: 'avg_r', header: 'Avg R · paper', sortValue: (r) => r.avg_r, cell: (r) => fmtR(r.avg_r, 2) },
  { key: 'equity', header: 'Equity · paper', sortValue: (r) => r.equity ?? null,
    cell: (r) => (r.paper_trades > 0 ? fmtPrice(r.equity) : '—') },
];

export default function AccuracyPage() {
  const { data, err, loaded } = usePollingState<AccuracyPayload>('/api/accuracy', 60000);
  const [sort, setSort] = useState('paper_trades');
  const rows = data?.rows;
  const { withResults, noResults } = useMemo(() => {
    const all = rows ?? [];
    return { withResults: all.filter(hasResults), noResults: all.filter((r) => !hasResults(r)) };
  }, [rows]);
  const sortable = data?.sortable ?? Object.keys(SORT_LABEL);

  return (
    <>
      <SectionHeader level={1} title="Accuracy board"
        question="For each strategy, do backtest, out-of-sample and paper results agree?"
        caption={data?.note ?? 'Backtest, holdout and paper columns are separate measurements and are never combined.'}
        note="Each win-rate column carries its own evidence chip. A — means that measurement does not exist for the strategy yet."
        right={
          <div className={s.sortRow} role="group" aria-label="Sort by">
            <span className="dim">Sort by</span>
            {sortable.map((k) => (
              <button key={k} type="button" className={`tab ${sort === k ? 'on' : ''}`}
                aria-pressed={sort === k} onClick={() => setSort(k)}>
                {SORT_LABEL[k] ?? humanKey(k)}
              </button>
            ))}
          </div>
        } />

      {err && !data ? (
        <EmptyState tone="risk" headline="Could not load the accuracy board" reason={err.message} />
      ) : null}

      {/* key={sort}: the sort buttons re-seed DataTable's own click-sort state. */}
      <DataTable<AccuracyRow>
        key={sort}
        rows={withResults} columns={COLUMNS} rowKey={(r) => r.id}
        defaultSort={{ key: sort, dir: 'desc' }}
        // The evidence columns are the point of this page: never hide them for being mostly empty.
        suppressEmptyAbove={1}
        loaded={loaded} minWidth={900}
        note="Color dot = strategy, same as elsewhere · win rates are percentages of closed trades in that cohort"
        empty={<EmptyState compact headline="No strategy has results yet"
          reason="Results appear once a strategy closes paper trades or a backtest is imported." />} />

      {loaded && noResults.length ? (
        <Details summary={`${noResults.length} ${noResults.length === 1 ? 'strategy has' : 'strategies have'} no results yet`}>
          <ul className={s.noResults}>
            {noResults.map((r) => (
              <li key={r.id}>
                <span className="dot" style={{ background: r.color }} />
                <Link href={`/models/${r.id}`}>{r.name}</Link>
                <span className="dim"> · {paperSample(r)} · {riskModel(r)}</span>
              </li>
            ))}
          </ul>
        </Details>
      ) : null}

      <div className="panel">
        <h3>How to read this</h3>
        <p className={`dim ${s.prose}`}>
          <b>Backtest · Dev</b> is the score a strategy gets on the data its rules were tuned on — almost
          always the most flattering column and the least informative. <b>Backtest · Holdout</b> is measured
          once, on data the tuning never saw; where the two disagree sharply, believe the holdout.
          <b> Paper</b> is what the simulated account actually did going forward. The three are never blended.
          A strategy with no numbers has not produced enough closed trades to say anything yet — that is
          reported, not hidden. Expectancy ranks above win rate: a strategy can be right often and still lose money.
        </p>
      </div>
    </>
  );
}
