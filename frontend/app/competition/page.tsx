'use client';
import Link from 'next/link';
import { useMemo } from 'react';
import {
  Advanced, DataTable, Details, EmptyState, SectionHeader, StatTile, StatusPill, pillFor, type Column,
} from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { SAMPLE } from '../../lib/evidence';
import { fmtAgo, fmtDrawdown, fmtPct, fmtPrice, money } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { useStatus } from '../../lib/status';
import type { Competition, CompetitionCard, ModelsPayload } from '../../lib/types';
import { BOARD, REGIME, humanKey, modelStatus } from '../../lib/vocab';
import s from './page.module.css';

type ResearchOnly = ModelsPayload['research_only'][number];

function tradesWord(n: number): string {
  return `${n} ${n === 1 ? 'trade' : 'trades'}`;
}
function money0(v: number): string {
  return '$' + Math.round(v).toLocaleString('en-US');
}

/** Equity sparkline with its low/high labelled — the shape alone says nothing. */
function Spark({ pts, color }: { pts: number[]; color: string }) {
  if (!pts || pts.length < 3) return null;
  const min = Math.min(...pts), max = Math.max(...pts);
  const rng = Math.max(1e-9, max - min);
  const path = pts.map((v, i) =>
    `${(i / (pts.length - 1)) * 100},${28 - ((v - min) / rng) * 24}`).join(' ');
  return (
    <div className={s.spark}>
      <svg viewBox="0 0 100 30" className={s.sparkSvg} preserveAspectRatio="none" aria-hidden>
        <polyline points={path} fill="none" stroke={color} strokeWidth="1.5"
          vectorEffect="non-scaling-stroke" opacity="0.85" />
      </svg>
      <div className={s.sparkLbl}>
        <span>low {money0(min)}</span>
        <span>last {pts.length} readings</span>
        <span>high {money0(max)}</span>
      </div>
    </div>
  );
}

/** One paper account. Return is the headline; n = closed trades drives the sample rule.
 *  Drawdown basis: `/api/competition cards[].max_drawdown_pct` → 'account' (spec §7.5). */
function AccountCard({ c, paperMode }: { c: CompetitionCard; paperMode: boolean | undefined }) {
  const st = modelStatus(c.status, { paperMode, trades: c.trades });
  const won = c.win_rate != null ? `${Math.round(c.win_rate * 100)}% won` : 'win rate —';
  return (
    <StatTile
      label={c.experimental ? `${c.name} (experimental)` : c.name}
      value={<span className={c.return_pct >= 0 ? 'pos' : 'neg'}>{fmtPct(c.return_pct)}</span>}
      n={c.trades}
      nLabel={c.trades > 0 ? `${tradesWord(c.trades)} · ${won}` : undefined}
      source={`Paper account · ${c.name} · return since the $10,000 start`}
      evidence="PAPER"
      paperMode={paperMode}
      href={`/models/${c.model_id}`}
      sub={
        <div className={s.acct}>
          <div>Account <b>{fmtPrice(c.equity)}</b> of $10,000</div>
          <div>Worst dip {fmtDrawdown(c.max_drawdown_pct, 'account')}</div>
          <div className={s.acctRow}>
            <span className="dot" style={{ background: c.color }} />
            <StatusPill size="sm" label={st.label} tone={st.tone} raw={c.status ?? 'UNKNOWN'} />
            {c.symbols_scanned ? <span>scanning {c.symbols_scanned} stocks this cycle</span> : null}
          </div>
          <Advanced>
            <div>Cash {fmtPrice(c.cash)} · realized {money(c.realized_pnl, 2)} · season {c.season}</div>
          </Advanced>
          <Spark pts={c.spark ?? []} color={c.color} />
        </div>
      }
    />
  );
}

/* ── leaderboards ─────────────────────────────────────────────────────────── */

function boardLabel(board: string): string {
  return (BOARD[board] ?? BOARD[board.replace(/^net_/, '')])?.label ?? humanKey(board);
}
function boardValue(board: string, r: CompetitionCard): string {
  if (board === 'win_rate') return r.win_rate != null ? `${Math.round(r.win_rate * 100)}% won` : '—';
  if (board === 'drawdown') return fmtDrawdown(r.max_drawdown_pct, 'account');
  return fmtPct(r.return_pct);
}

/** Order the traded cards for one board. Drawdowns are stored as negative percents,
 *  so "smallest worst dip" is the largest (least negative) value first. */
function rankFor(board: string, traded: CompetitionCard[]): CompetitionCard[] {
  const key = board.replace(/^net_/, '');
  const by = (f: (c: CompetitionCard) => number) => [...traded].sort((a, b) => f(b) - f(a));
  if (key === 'win_rate') return by((c) => (c.win_rate ?? -1) * 1000 + Math.min(c.trades, 999) / 1000);
  if (key === 'drawdown') return by((c) => -Math.abs(c.max_drawdown_pct ?? 0));
  return by((c) => c.return_pct);
}

const BOARD_TOP = 5;

/** Ranks only accounts with ≥ SAMPLE.rank closed trades; the rest are listed, not ranked;
 *  never-traded accounts do not appear at all (spec §7.3). Rows are computed from the
 *  full card list, not the backend's pre-cut top 5 (which included never-traded accounts). */
function Leaderboard({ board, cards }: { board: string; cards: CompetitionCard[] }) {
  const traded = rankFor(board, cards.filter((r) => r.trades > 0));
  const ranked = traded.filter((r) => r.trades >= SAMPLE.rank).slice(0, BOARD_TOP);
  const unranked = traded.filter((r) => r.trades < SAMPLE.rank);
  return (
    <div className="card">
      <h3>{boardLabel(board)}</h3>
      {ranked.length === 0 ? (
        <EmptyState compact headline="Nobody qualifies yet"
          reason={`Ranking needs at least ${SAMPLE.rank} closed paper trades.`} />
      ) : (
        <ol className={s.board}>
          {ranked.map((r) => (
            <li key={r.model_id} className={s.boardRow}>
              <Link href={`/models/${r.model_id}`} style={{ color: r.color }}>{r.name}</Link>
              <span className={`mono ${s.boardVal}`}>{boardValue(board, r)} · {tradesWord(r.trades)}</span>
              {r.trades < SAMPLE.judge ? <span className="stat-warn">too few to judge</span> : null}
            </li>
          ))}
        </ol>
      )}
      {unranked.length ? (
        <div className={s.unranked}>
          {unranked.map((r) => (
            <div key={r.model_id}>Not ranked — {r.name} · {tradesWord(r.trades)} (min {SAMPLE.rank})</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const RESEARCH_COLS: Column<ResearchOnly>[] = [
  { key: 'name', header: 'Method', align: 'l', simple: true, cell: (r) => <b>{r.name}</b> },
  { key: 'why_not', header: 'Why it does not compete', align: 'l', simple: true,
    cell: (r) => <span className={`dim ${s.wrap}`}>{r.why_not}</span> },
];

/* ── page ─────────────────────────────────────────────────────────────────── */

export default function CompetitionPage() {
  // Equity is re-marked every ~60s by the tracking cycle; poll faster than that.
  const comp = usePollingState<Competition>('/api/competition', 15000);
  const models = usePollingState<ModelsPayload>('/api/models', 60000);
  const { status } = useStatus();
  const { advanced } = useMode();
  const paperMode = status?.paper_mode;
  const loaded = comp.loaded;
  const cards = comp.data?.cards;

  const trading = useMemo(
    () => (cards ?? []).filter((c) => c.trades > 0).sort((a, b) => b.return_pct - a.return_pct),
    [cards]);
  const idle = useMemo(() => (cards ?? []).filter((c) => c.trades <= 0), [cards]);
  const regime = models.data?.regime ?? null;

  // Advanced-only header caption: when the ledgers were last marked, and the season(s).
  const advNote = useMemo(() => {
    if (!advanced || !cards?.length) return undefined;
    const marks = cards.map((c) => c.last_marked_at).filter((x): x is string => !!x).sort();
    const latest = marks.length ? marks[marks.length - 1] : null;
    const seasons = Array.from(new Set(cards.map((c) => c.season))).sort((a, b) => a - b);
    const parts = [`all accounts marked ${fmtAgo(latest)} · season ${seasons.join(', ')}`];
    if (regime?.why) parts.push(`regime: ${regime.why}`);
    return parts.join(' · ');
  }, [advanced, cards, regime]);

  return (
    <>
      <SectionHeader level={1} title="Strategies — paper accounts"
        question="Which strategies are actually making paper money, with enough trades to mean anything?"
        caption="Paper accounts · every strategy starts at $10,000 · identical costs, conservative fills, separate ledgers"
        evidence="PAPER"
        note={advNote}
        right={regime ? <StatusPill {...pillFor(REGIME, regime.state)} /> : null} />

      {comp.err && !comp.data ? (
        <EmptyState tone="risk" headline="Could not load the paper accounts" reason={comp.err.message}
          next="The page retries every 15 seconds." />
      ) : null}

      <SectionHeader title="Trading" question="Which paper accounts have closed at least one trade?"
        count={loaded ? trading.length : null}
        caption="Paper account · sorted by return · the sample warning says how much to trust each number" />
      {!loaded ? (
        <div className="stat-grid">
          {[0, 1, 2].map((i) => (
            <StatTile key={i} label="Strategy" value={null} n={null} source="Paper account" evidence="PAPER" loaded={false} />
          ))}
        </div>
      ) : trading.length === 0 ? (
        <EmptyState headline="No strategy has closed a paper trade yet"
          reason="Every account starts at exactly $10,000 and appears here only after its first closed trade — a fresh $10,000 balance is a starting point, not a result."
          next={idle.length ? `${idle.length} strategies are waiting for their first qualifying setup (listed below).` : undefined} />
      ) : (
        <div className="stat-grid">
          {trading.map((c) => <AccountCard key={c.model_id} c={c} paperMode={paperMode} />)}
        </div>
      )}
      {loaded && idle.length ? (
        <Details summary={`${idle.length} ${idle.length === 1 ? 'strategy has' : 'strategies have'} not traded yet (show)`}>
          <ul className={s.idle}>
            {idle.map((c) => {
              const st = modelStatus(c.status, { paperMode, trades: c.trades });
              return (
                <li key={c.model_id}>
                  <Link href={`/models/${c.model_id}`} style={{ color: c.color }}>{c.name}</Link>
                  {c.experimental ? <span className="faint"> (experimental)</span> : null}
                  <span className="dim">
                    {' · '}{c.has_traded ? 'first paper trade still open' : 'waiting for first trade'}
                    {' · '}{c.idle_reason ?? 'no reason reported by the scanner'}
                  </span>
                  {' '}<StatusPill size="sm" label={st.label} tone={st.tone} raw={c.status ?? 'UNKNOWN'} />
                </li>
              );
            })}
          </ul>
        </Details>
      ) : null}

      <SectionHeader title="Leaderboards"
        question="Which strategy leads on each objective — return, accuracy, smallest dip?"
        caption={`Ranked among strategies with at least ${SAMPLE.rank} paper trades`} evidence="PAPER"
        note="There is no single winner until you pick an objective — and none of these mean much until the trade counts grow." />
      {loaded && comp.data ? (
        <div className="cards">
          {Object.keys(comp.data.leaderboards ?? {}).map((board) => (
            <Leaderboard key={board} board={board} cards={cards ?? []} />
          ))}
        </div>
      ) : <div className={`skel ${s.skelBlock}`} aria-busy="true" />}

      {models.data?.research_only?.length ? (
        <>
          <SectionHeader title="Research-only methods"
            question="Which methods are visible here but do not compete, and why?"
            caption="These need data a retail feed does not have, so they never get a paper account" />
          <DataTable<ResearchOnly> rows={models.data.research_only} columns={RESEARCH_COLS}
            rowKey={(r) => r.id} minWidth={560} />
        </>
      ) : null}

      {comp.data?.note ? <p className="disclaimer">{comp.data.note}</p> : null}
    </>
  );
}
