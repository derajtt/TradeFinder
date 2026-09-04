'use client';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMemo, useState } from 'react';
import DetailDrawer from '../../../components/DetailDrawer';
import {
  Advanced, DataTable, EmptyState, SectionHeader, SignalTable, StatTile, StatusPill, Term, pillFor, type Column,
} from '../../../components/ui';
import { usePollingState } from '../../../lib/api';
import { fmtDrawdown, fmtEtShort, fmtPct, fmtPrice, fmtR } from '../../../lib/format';
import { useMode } from '../../../lib/mode';
import { useStatus } from '../../../lib/status';
import type { ModelsPayload, Position, SignalRow } from '../../../lib/types';
import { LIFECYCLE, POSITION_STATUS, humanKey } from '../../../lib/vocab';
import s from './page.module.css';

type Tab = 'picks' | 'trades' | 'about';
type Sort = 'time' | 'score' | 'change' | 'symbol';
const SORT_LABEL: Record<Sort, string> = { score: 'Score', change: 'Move since pick', time: 'Newest', symbol: 'Stock' };
const TABS: { key: Tab; label: string }[] = [
  { key: 'picks', label: 'Picks' }, { key: 'trades', label: 'Paper trades' }, { key: 'about', label: 'About' },
];
const QUESTION = 'What does this strategy look for, and how is its paper account doing?';

/* /api/reversion/signals row fields this page reads (Extreme Reversion keeps its own table). */
interface ReversionSignal {
  signal_uid: string; symbol: string; confirmed_at: string; entry: number; score: number | null;
  status: string; variant: string; stop?: number | null; targets?: { price?: number }[];
}
/** Map the richer reversion rows onto the shared signal shape so one table serves every finder. */
function fromReversion(rows: ReversionSignal[]): SignalRow[] {
  return rows.map((r) => ({
    signal_uid: r.signal_uid, symbol: r.symbol, initiated_at: r.confirmed_at, buy_price: r.entry,
    current: r.entry, score: r.score, lifecycle: r.status, profile: r.variant, status: r.status,
    stop: r.stop, target1: r.targets?.[0]?.price, target2: r.targets?.[1]?.price,
  })) as unknown as SignalRow[];
}

function tradesWord(n: number): string {
  return `${n} ${n === 1 ? 'trade' : 'trades'}`;
}

export default function ModelPage() {
  const { id } = useParams<{ id: string }>();
  const [sort, setSort] = useState<Sort>('score');
  const [dedupe, setDedupe] = useState(true);
  const [tab, setTab] = useState<Tab>('picks');
  const [sel, setSel] = useState<string | null>(null);
  const { status } = useStatus();
  const { advanced } = useMode();
  const paperMode = status?.paper_mode;

  const models = usePollingState<ModelsPayload>('/api/models', 30000);
  // Extreme Reversion records into its own table, not buy_signals — querying
  // by profile there would always come back empty.
  const ownTable = id === 'extreme_reversion';
  const sig = usePollingState<{ rows?: SignalRow[]; signals?: ReversionSignal[] }>(
    ownTable ? '/api/reversion/signals?limit=200'
      : `/api/signals?profile=${encodeURIComponent(id)}&limit=200&dedupe=${dedupe ? 1 : 0}&sort=${sort}`,
    30000);
  const pos = usePollingState<{ rows: Position[] }>(`/api/positions?profile=${encodeURIComponent(id)}`, 30000);

  const m = useMemo(() => models.data?.models.find((x) => x.id === id), [models.data, id]);
  const sigRows = useMemo<SignalRow[]>(
    () => (ownTable ? fromReversion(sig.data?.signals ?? []) : (sig.data?.rows ?? [])),
    [ownTable, sig.data]);
  // "Now" for a paper trade comes from the tracked signal rows of the same stock.
  const liveBySymbol = useMemo(() => {
    const map = new Map<string, number | null>();
    for (const r of sigRows) if (!map.has(r.symbol)) map.set(r.symbol, r.current);
    return map;
  }, [sigRows]);

  // §2.7 Simple columns; Advanced adds Size, Remaining, Exit reason, Opened, Engine version.
  const posCols = useMemo<Column<Position>[]>(() => [
    { key: 'symbol', header: 'Stock', align: 'l', simple: true, sortValue: (p) => p.symbol,
      cell: (p) => <span className="sym">{p.symbol}</span> },
    { key: 'entry_fill', header: 'Bought at', simple: true, sortValue: (p) => p.entry_fill,
      cell: (p) => fmtPrice(p.entry_fill) },
    { key: 'now', header: 'Now', simple: true,
      cell: (p) => fmtPrice(liveBySymbol.get(p.symbol) ?? null),
      isEmpty: (p) => liveBySymbol.get(p.symbol) == null },
    { key: 'stop', header: 'Stop', simple: true, cell: (p) => fmtPrice(p.stop) },
    { key: 'targets', header: 'Targets', simple: true,
      isEmpty: (p) => p.target1 == null && p.target2 == null,
      cell: (p) => `${fmtPrice(p.target1)} / ${fmtPrice(p.target2)}` },
    { key: 'realized_r', header: <Term k="r_multiple">Result so far</Term>, simple: true, sortValue: (p) => p.realized_r,
      cell: (p) => (
        <span className={p.realized_r > 0 ? 'pos' : p.realized_r < 0 ? 'neg' : undefined}>{fmtR(p.realized_r, 2)}</span>
      ) },
    { key: 'status', header: 'Status', align: 'l', simple: true, sortValue: (p) => p.status,
      cell: (p) => <StatusPill size="sm" {...pillFor(POSITION_STATUS, p.status)} /> },
    // ── Advanced-only from here ──
    { key: 'size_usd', header: 'Size', sortValue: (p) => p.size_usd ?? null, cell: (p) => fmtPrice(p.size_usd) },
    { key: 'remaining_frac', header: 'Remaining',
      cell: (p) => (p.remaining_frac == null ? '—' : `${Math.round(p.remaining_frac * 100)}%`) },
    { key: 'exit_reason', header: 'Exit reason', align: 'l', cell: (p) => (p.exit_reason ? humanKey(p.exit_reason) : '—') },
    { key: 'opened_at', header: 'Opened', align: 'l', sortValue: (p) => p.opened_at, cell: (p) => fmtEtShort(p.opened_at) },
    { key: 'strategy_version', header: 'Engine version', align: 'l',
      cell: (p) => (p.strategy_version ? <code>engine v{p.strategy_version}</code> : '—') },
  ], [liveBySymbol]);

  if (!models.loaded) {
    return (
      <>
        <SectionHeader level={1} title="Strategy" question={QUESTION} />
        <div className="stat-grid">
          {[0, 1, 2, 3].map((i) => (
            <StatTile key={i} label="Loading" value={null} n={null} source="Paper account" evidence="PAPER" loaded={false} />
          ))}
        </div>
      </>
    );
  }
  if (!m) {
    return (
      <>
        <SectionHeader level={1} title="Strategy not found" question={QUESTION} />
        <EmptyState tone="warn" headline="No strategy with this id"
          reason={models.err ? models.err.message : `"${id}" is not in the strategy registry.`}
          action={{ label: 'All strategies', href: '/competition' }} />
      </>
    );
  }

  const a = m.account;
  const n = a.trades_closed;
  const sigTotal = Object.values(m.signals).reduce((x, y) => x + y, 0);
  const buys = m.signals.ACTIONABLE_BUY ?? 0;
  const watching = (m.signals.QUALIFIED_WATCH ?? 0) + (m.signals.EARLY_WATCH ?? 0);
  const legacy = m.signals.legacy ?? 0;
  const scope = `${m.name} model`;
  const breakdown = Object.entries(m.signals).map(([k, v]) => (
    <span key={k}>
      {k === 'legacy' ? <Term k="legacy_bucket">other</Term> : (LIFECYCLE[k]?.label ?? humanKey(k))} {v}
    </span>
  ));
  const posRows = pos.data?.rows ?? [];

  return (
    <>
      <SectionHeader level={1}
        title={
          <>
            <span style={{ color: m.color }}>Strategy: {m.name}</span>
            {m.experimental ? <span className={`dim ${s.word}`}>experimental</span> : null}
            {!m.enabled ? <StatusPill label="Off" tone="neutral" raw="DISABLED" /> : null}
          </>
        }
        question={QUESTION}
        caption={`${m.universe} · ${m.horizon} · ${m.cadence} · ${(m.asset_classes ?? []).join(' + ')}`}
        right={<Link className="btn sm" href="/competition">All strategies</Link>} />

      {/* Drawdown basis: `/api/models account.max_drawdown_pct` → 'account' (spec §7.5). */}
      <div className="stat-grid">
        <StatTile label="Paper account" term="paper" value={fmtPrice(a.equity)} n={n}
          nLabel={n > 0 ? `of $10,000 start · ${tradesWord(n)}` : undefined}
          source={`Paper account · ${scope}`} evidence="PAPER" paperMode={paperMode}
          sub={<>Worst dip {fmtDrawdown(a.max_drawdown_pct, 'account')}</>} />
        <StatTile label="Return" n={n}
          value={<span className={a.return_pct >= 0 ? 'pos' : 'neg'}>{fmtPct(a.return_pct)}</span>}
          nLabel={n > 0 ? `${tradesWord(n)} · paper` : undefined}
          source={`Paper account · ${scope} · since the $10,000 start`} evidence="PAPER" paperMode={paperMode}
          sub={advanced ? `Cash ${fmtPrice(a.cash)} · realized ${fmtPrice(a.realized_pnl)}` : undefined} />
        <StatTile label="Closed trades" value={n} n={n}
          nLabel={n > 0 ? `${a.wins} won · ${Math.max(0, n - a.wins)} lost` : undefined}
          source={`Paper account · ${scope}`} evidence="PAPER" paperMode={paperMode} />
        <StatTile label="Picks" value={sigTotal} n={sigTotal} unit="picks"
          nLabel={sigTotal > 0 ? (
            <>all time · {buys} buys · {watching} watching
              {legacy ? <> · {legacy} <Term k="legacy_bucket">other</Term></> : null}</>
          ) : undefined}
          source={`Tracked picks · ${scope} · every state, all sessions`} evidence="TRACKED"
          sub={advanced && sigTotal > 0 ? <span className={s.breakdown}>{breakdown}</span> : undefined} />
      </div>

      <div className="ptabs" role="tablist" aria-label="Strategy sections">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`ptab ${tab === t.key ? 'active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === 'trades' && pos.loaded ? <span className={`dim ${s.tabCount}`}>{posRows.length}</span> : null}
          </button>
        ))}
      </div>

      {tab === 'picks' ? (
        <section aria-label="Picks">
          <div className={s.controls}>
            <span className="dim">Sort</span>
            {(Object.keys(SORT_LABEL) as Sort[]).map((k) => (
              <button key={k} type="button" className={`tab ${sort === k ? 'on' : ''}`}
                aria-pressed={sort === k} disabled={ownTable} onClick={() => setSort(k)}>
                {SORT_LABEL[k]}
              </button>
            ))}
            <button type="button" className={`tab ${dedupe ? 'on' : ''}`} aria-pressed={dedupe}
              disabled={ownTable} onClick={() => setDedupe((d) => !d)}>
              {dedupe ? 'One row per stock: on' : 'One row per stock: off'}
            </button>
            {sig.loaded ? (
              <span className={`dim ${s.counter}`}>
                {ownTable ? `${sigRows.length} records`
                  : dedupe ? `${sigRows.length} stocks (one row per stock)`
                  : `${sigRows.length} records (every state change)`}
              </span>
            ) : null}
          </div>
          {!dedupe && !ownTable ? (
            <p className={`faint ${s.note}`}>
              The tracking table stores one row per state per day, so the same stock repeats while this is off.
            </p>
          ) : null}
          <SignalTable rows={sigRows} onSelect={(r) => setSel(r.symbol)} variant="mixed" scope={scope} loaded={sig.loaded} />
        </section>
      ) : null}

      {tab === 'trades' ? (
        <section aria-label="Paper trades">
          <DataTable<Position> rows={posRows} columns={posCols}
            rowKey={(p) => `${p.symbol}|${p.opened_at}|${p.closed_at ?? 'open'}`}
            onRowClick={(p) => setSel(p.symbol)} defaultSort={{ key: 'opened_at', dir: 'desc' }}
            evidence="PAPER" note={`Paper account · ${scope}`} loaded={pos.loaded} minWidth={720}
            empty={<EmptyState compact headline={`No paper trades yet for ${scope}`}
              reason="The paper account opens a trade only when this strategy issues a Buy pick that passes every check." />} />
        </section>
      ) : null}

      {tab === 'about' ? (
        <section aria-label="About" className="panel">
          <h3>What it looks for</h3>
          <p className="lead">{m.edge}{m.edge && !/[.!?]$/.test(m.edge) ? '.' : ''}</p>
          {m.requires?.length ? (
            <p className={`dim ${s.req}`}>
              <span>Needs all of these on the same stock, the same day:</span>
              {m.requires.map((r) => <StatusPill key={r} size="sm" label={humanKey(r)} tone="neutral" raw={r} />)}
            </p>
          ) : null}
          {m.hypothesis ? <p className="dim">Hypothesis: {m.hypothesis}</p> : null}
          {m.data_notes ? <p className="dim">Data note: {m.data_notes}</p> : null}
          <Advanced>
            <p className="dim">
              Engine <code>{m.engine ?? '—'}</code> · ledger <code>{m.ledger_profile ?? m.id}</code> · id <code>{m.id}</code>
            </p>
          </Advanced>
          <p className="disclaimer">
            This strategy runs continuously with its own settings and ledger. It shares only market data and the
            conservative execution simulator with other strategies — never scores or balances. Adjust its parameters in
            Settings → Strategy models. Every statistic is labelled by cohort; forward paper evidence decides the competition.
          </p>
        </section>
      ) : null}

      {sel ? <DetailDrawer symbol={sel} onClose={() => setSel(null)} /> : null}
    </>
  );
}
