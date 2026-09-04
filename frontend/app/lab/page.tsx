'use client';
import { floorOf, pctSigned, pfText, ratePct, type BtMetric } from '../../components/BacktestBits';
import { DataTable, EmptyState, SectionHeader, type Column } from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { humanKey } from '../../lib/vocab';

/* ── tournament() / mfe_decay() in backend/app/bt/tournament.py ── */
interface Policy {
  family: string; unavailable?: string;
  optimistic?: BtMetric; baseline?: BtMetric; pessimistic?: BtMetric;
  ci_expectancy?: { lo: number; hi: number };
}
interface Decay { minute: number; avg_unrealized_pct: number; avg_mfe_pct: number; n: number }
interface Result { tournament?: Record<string, Policy>; mfe_decay?: Decay[] }
interface Job { available?: boolean; created_at?: string; config_hash?: string; result?: Result }
interface Reports { reports?: { primary?: Job; walkforward?: Job } }

interface Row { name: string; family: string; baseline: BtMetric; pessimistic?: BtMetric }

const isDate = (s: string | undefined) => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s);

/** Bars for average peak profit (MFE) and average marked profit by minutes after entry.
 *  Three gridlines with % labels, 12px text, token colors. */
function DecayChart({ decay }: { decay: Decay[] }) {
  const W = 720, H = 190, L = 54, R = 16, T = 34, B = 44;
  const vals = decay.flatMap((d) => [d.avg_mfe_pct, d.avg_unrealized_pct, 0]);
  const yMax = Math.max(...vals), yMin = Math.min(...vals);
  const span = Math.max(1e-6, yMax - yMin);
  const y = (v: number) => T + ((yMax - v) / span) * (H - T - B);
  const plotW = W - L - R;
  const slot = plotW / Math.max(1, decay.length);
  const bw = Math.max(3, Math.min(10, slot * 0.36));
  const ticks = [yMax, (yMax + yMin) / 2, yMin];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }} role="img"
      aria-label="Average peak profit and average marked profit by minutes after entry">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
          <text x={L - 6} y={y(t) + 4} fontSize="12" fill="var(--text-dim)" textAnchor="end">{t.toFixed(1)}%</text>
        </g>
      ))}
      <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke="var(--line)" />
      {decay.map((d, i) => {
        const cx = L + slot * i + slot / 2;
        const m = d.avg_mfe_pct, u = d.avg_unrealized_pct;
        return (
          <g key={d.minute}>
            <rect x={cx - bw - 1} y={Math.min(y(m), y(0))} width={bw} height={Math.abs(y(m) - y(0))} fill="var(--accent)" opacity="0.45" />
            <rect x={cx + 1} y={Math.min(y(u), y(0))} width={bw} height={Math.abs(y(u) - y(0))} fill={u >= 0 ? 'var(--buy)' : 'var(--risk)'} />
            {(decay.length <= 14 || i % 2 === 0) ? (
              <text x={cx} y={H - B + 16} fontSize="12" fill="var(--text-dim)" textAnchor="middle">{d.minute}</text>
            ) : null}
          </g>
        );
      })}
      <text x={L + plotW / 2} y={H - 8} fontSize="12" fill="var(--text-dim)" textAnchor="middle">Minutes after entry</text>
      <rect x={L} y={10} width={10} height={10} fill="var(--accent)" opacity="0.45" />
      <text x={L + 15} y={19} fontSize="12" fill="var(--text)">Average peak profit (MFE)</text>
      <rect x={L + 215} y={10} width={10} height={10} fill="var(--buy)" />
      <text x={L + 230} y={19} fontSize="12" fill="var(--text)">Average marked profit at that minute</text>
    </svg>
  );
}

export default function ExitLabPage() {
  const rep = usePollingState<Job>('/api/backtest/report', 60000);
  const all = usePollingState<Reports>('/api/backtest/reports', 60000);
  const { advanced } = useMode();

  // Same source as /backtest: the imported report; the per-kind listing is the fallback
  // (bt_import.py posts kind "walkforward"; the spec also names an optional "primary").
  const src = [rep.data, all.data?.reports?.walkforward, all.data?.reports?.primary]
    .find((j) => j?.result?.tournament && Object.keys(j.result.tournament).length);
  const loaded = rep.loaded && all.loaded;
  const configDate = rep.data?.created_at ? fmtEtDate(rep.data.created_at)
    : isDate(rep.data?.config_hash) ? rep.data!.config_hash! : 'date unknown';

  const header = (
    <SectionHeader level={1} title="Exit lab" evidence="BACKTEST"
      question="Which exit rule kept the most of the gains in the backtest?"
      caption="From the imported backtest. For each way of exiting a trade, how much of the move it captured." />
  );

  if (!loaded) return <>{header}<EmptyState loaded={false} headline="Loading tournament" reason={null} /></>;
  if (!src) {
    return (
      <>
        {header}
        <EmptyState headline={`The imported backtest (${configDate}) has no exit tournament.`}
          reason={rep.data && rep.data.available === false ? (rep.data as { note?: string }).note ?? null
            : 'The latest imported report does not contain a tournament object.'}
          next="Run the tournament to fill this page." />
      </>
    );
  }

  const t = src.result!.tournament!;
  const decay = src.result!.mfe_decay ?? [];
  const rows: Row[] = Object.entries(t)
    .filter(([, v]) => v.baseline)
    .map(([name, v]) => ({ name, family: v.family, baseline: v.baseline!, pessimistic: v.pessimistic }));
  const unavailable = Object.entries(t).filter(([, v]) => v.unavailable).map(([n]) => n);

  const cols: Column<Row>[] = [
    { key: 'policy', header: 'Exit rule', align: 'l', simple: true, sortValue: (r) => r.name,
      cell: (r) => <>{humanKey(r.name)}{advanced ? <> <code className="pill-raw">{r.name}</code></> : null}</> },
    { key: 'family', header: 'Family', align: 'l', simple: true, sortValue: (r) => r.family, cell: (r) => humanKey(r.family) },
    { key: 'n', header: 'Trades', simple: true, sortValue: (r) => r.baseline.n, cell: (r) => r.baseline.n },
    { key: 'wr', header: 'Win rate', simple: true, sortValue: (r) => r.baseline.win_rate, cell: (r) => ratePct(r.baseline.win_rate) },
    { key: 'floor', header: 'Conservative floor', term: 'conservative_floor', simple: true,
      sortValue: (r) => floorOf(r.baseline), cell: (r) => ratePct(floorOf(r.baseline)) },
    { key: 'exp', header: 'Expectancy', simple: true, sortValue: (r) => r.baseline.expectancy_pct,
      cell: (r) => <span className={(r.baseline.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{pctSigned(r.baseline.expectancy_pct)}</span> },
    { key: 'pf', header: 'Profit factor', term: 'profit_factor', sortValue: (r) => r.baseline.profit_factor,
      cell: (r) => pfText(r.baseline.profit_factor, r.baseline.n) },
    { key: 'dd', header: 'Sum of trade %', term: 'drawdown_sum', simple: true, sortValue: (r) => r.baseline.max_drawdown_pct,
      cell: (r) => r.baseline.max_drawdown_pct == null ? '—' : <span className="neg">{Math.abs(r.baseline.max_drawdown_pct).toFixed(1)}%</span> },
    { key: 'amb', header: 'Unclear fills', term: 'ambiguous', sortValue: (r) => r.baseline.ambiguous ?? null, cell: (r) => r.baseline.ambiguous ?? '—' },
    { key: 'rpm', header: 'Return per minute held', sortValue: (r) => r.baseline.ret_per_min ?? null,
      cell: (r) => r.baseline.ret_per_min == null ? '—' : r.baseline.ret_per_min.toFixed(4) },
    { key: 'pess', header: 'Expectancy, worst-case fills', simple: true, sortValue: (r) => r.pessimistic?.expectancy_pct ?? null,
      cell: (r) => <span className={(r.pessimistic?.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{pctSigned(r.pessimistic?.expectancy_pct)}</span> },
  ];

  return (
    <>
      {header}

      {decay.length > 0 ? (
        <>
          <SectionHeader title="Where the profit peaks" evidence="BACKTEST"
            question="Do gains peak in the first minutes after entry or build into the open?"
            caption={`Average across ${decay[0]?.n ?? '—'} backtest trades, by minutes after entry`} />
          <div className="tbl-wrap" style={{ padding: '14px 16px' }}>
            <DecayChart decay={decay} />
          </div>
        </>
      ) : null}

      <SectionHeader title="Exit tournament" count={rows.length} evidence="BACKTEST"
        question="Same signals and entries for every rule — only the exit differs. Which one kept the most?"
        caption="Baseline fills shown; ranked by conservative floor so a 3-for-3 record cannot outrank a steady 60% on 200 trades." />
      <DataTable<Row> rows={rows} columns={cols} rowKey={(r) => r.name} minWidth={900}
        defaultSort={{ key: 'floor', dir: 'desc' }}
        note="Win rate = wins ÷ resolved trades; conservative floor = Wilson lower bound on that rate."
        empty={<EmptyState compact headline="No exit rule has a baseline result" reason="Every rule in this tournament is marked unavailable." />} />
      {unavailable.length > 0 ? (
        <p className="note" style={{ marginTop: 8 }}>
          Not testable on 5-minute bars (forward paper only): {unavailable.map((n) => humanKey(n)).join(', ')}.
        </p>
      ) : null}
      <p className="disclaimer">Unclear fills = the stop and the target were both hit inside one bar with unknown order — never counted as a win.
        Rankings use conservative fills; nothing is promoted from this table without walk-forward + forward paper evidence.</p>
    </>
  );
}
