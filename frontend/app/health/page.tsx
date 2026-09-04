'use client';
import { useMemo } from 'react';
import { Advanced, DataTable, Details, EmptyState, SectionHeader, StatusPill, type Column } from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { fmtAgo, fmtEtDate, fmtNum, fmtPrice } from '../../lib/format';
import { useScannerState, useStatus } from '../../lib/status';
import type { Canonical, HealthDetail, StrategyHealth, StrategyHealthRow } from '../../lib/types';
import { useMode } from '../../lib/mode';
import { LIFECYCLE, humanKey, modelStatus, phaseLabel, type Label } from '../../lib/vocab';
import c from '../controls.module.css';

/* Spec §3.5 — System health. One question: is anything broken? */

interface Check { ok: boolean; text: string }
type Entitlement = { ok: boolean; status: number };

/** Probe keys arrive in more than one spelling (e.g. EARNINGS-CAL / earnings_cal); keep the first. */
function dedupeEntitlements(ent: Record<string, Entitlement> | undefined): [string, Entitlement][] {
  const seen = new Map<string, [string, Entitlement]>();
  for (const [k, v] of Object.entries(ent ?? {})) {
    const norm = k.toUpperCase().replace(/[_\s]+/g, '-');
    if (!seen.has(norm)) seen.set(norm, [k, v]);
  }
  return [...seen.values()];
}

function backupPill(b: HealthDetail['backup']): Label & { detail: string | null } {
  if (!b) return { label: 'Backup status unknown', tone: 'neutral', detail: null };
  const detail = [b.latest, b.size_mb != null ? `${fmtNum(b.size_mb, 1)} MB` : null,
    b.count != null ? `${b.count} kept` : null, b.note].filter(Boolean).join(' · ') || null;
  if (b.status === 'OK' && b.age_hours != null && b.age_hours < 24)
    return { label: `Backed up ${fmtNum(b.age_hours, 1)} h ago`, tone: 'buy', detail };
  if (b.status === 'OK' || b.status === 'STALE')
    return { label: `Backup is ${b.age_hours != null ? fmtNum(b.age_hours, 1) : '—'} h old`, tone: 'warn', detail };
  if (b.status === 'NONE') return { label: 'No backups yet', tone: 'warn', detail };
  return { label: 'Backup status unknown', tone: 'neutral', detail };
}

/** Event messages are free text; money and long decimals inside them get the shared formatters.
 *  In Simple the lifecycle enums read as their plain labels, "@ 3.86" reads "at $3.86" and
 *  ALL_CAPS policy ids in parentheses are dropped (they stay visible in Advanced). */
function fmtMessage(m: string, advanced: boolean): string {
  let s = String(m ?? '')
    .replace(/\$(\d[\d,]*(?:\.\d+)?)/g, (_, n: string) => fmtPrice(Number(n.replace(/,/g, ''))))
    .replace(/(^|[^\w.$])(\d+\.\d{3,})(?![\w.])/g, (_, pre: string, n: string) => pre + fmtNum(Number(n)));
  if (!advanced) {
    s = s.replace(/\b(DISCOVERED|EARLY_WATCH|QUALIFIED_WATCH|ACTIONABLE_BUY|REJECTED|INVALIDATED|EXPIRED|CLOSED|DATA_ERROR)\b/g,
      (k) => LIFECYCLE[k]?.label ?? k)
      .replace(/ @ (\d+(?:\.\d+)?)/g, (_, n: string) => ` at ${fmtPrice(Number(n))}`)
      .replace(/\s*\([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\)/g, '');
  }
  return s;
}

const RUN_STATUS: Record<string, Label> = {
  ok: { label: 'OK', tone: 'buy' }, error: { label: 'Error', tone: 'risk' },
  running: { label: 'Running', tone: 'early' }, skipped: { label: 'Skipped', tone: 'neutral' },
};

/** Plain words for the reject counters a strategy reports. */
const GATE_WORDS: Record<string, string> = {
  window_rejects: 'outside its window',
  cap_rejects: 'at the position cap',
  item_rejects: 'filing type skipped',
  quote_rejects: 'no usable quote or under the price floor',
  spread_rejects: 'spread too wide',
  rejected_this_pass: 'rejected this pass',
};

export default function HealthPage() {
  const { data: h, loaded: hLoaded } = usePollingState<HealthDetail>('/api/health/detail', 20000);
  const { data: s, loaded: sLoaded } = usePollingState<StrategyHealth>('/api/health/strategies', 15000);
  // Reconciliation problem line (spec §7.10) — canonical report for all models.
  const { data: canon } = usePollingState<Canonical>('/api/report/canonical', 60000);
  const { status } = useStatus();
  const scanner = useScannerState();
  const { advanced } = useMode();
  const paperMode = status?.paper_mode;
  const loaded = hLoaded && sLoaded;

  const strategies = s?.strategies ?? [];
  const entitlements = useMemo(() => dedupeEntitlements(h?.entitlements), [h?.entitlements]);
  const entOk = entitlements.filter(([, e]) => e.ok).length;

  const checks = useMemo<Check[]>(() => {
    if (!loaded) return [];
    const out: Check[] = [];
    const scannerBad = scanner.key === 'problem' || scanner.key === 'unreachable';
    out.push({ ok: !scannerBad, text: scannerBad
      ? `${scanner.label}${status?.scanner?.last_error ? ` — ${status.scanner.last_error}` : ''}`
      : scanner.label });
    const b = h?.backup;
    const backupOk = !!b && b.status === 'OK' && b.age_hours != null && b.age_hours < 24;
    out.push({ ok: backupOk, text: backupOk
      ? `Database backed up ${fmtNum(b!.age_hours!, 1)} h ago`
      : b?.status === 'NONE' ? 'No database backup found yet'
        : b?.age_hours != null ? `Database backup is ${fmtNum(b.age_hours, 1)} h old (should be under 24 h)`
          : `Database backup status unknown${b?.note ? ` — ${b.note}` : ''}` });
    for (const r of strategies) {
      if (r.errors > 0) out.push({ ok: false, text: `${r.name}: ${r.errors} error${r.errors === 1 ? '' : 's'} in its last pass` });
    }
    for (const [name, e] of entitlements) {
      if (!e.ok) out.push({ ok: false, text: `${name} is not in the FMP plan (HTTP ${e.status})` });
    }
    if (canon?.reconciliation && canon.reconciliation.equals_total === false) {
      out.push({ ok: false, text: 'Pick counts do not reconcile — the lifecycle buckets do not add up to the total' });
    }
    return out;
  }, [loaded, scanner.key, scanner.label, status?.scanner?.last_error, h?.backup, strategies, entitlements, canon]);
  const problems = checks.filter((k) => !k.ok);

  const allIdle = strategies.length > 0 && strategies.every((r) => !r.symbols_scanned);
  const stratCols = useMemo<Column<StrategyHealthRow>[]>(() => [
    { key: 'name', header: 'Strategy', align: 'l', simple: true, sortValue: (r) => r.name,
      cell: (r) => (
        <span className={`stock-cell ${c.nowrap}`}>
          <span className="dot" style={{ background: r.color || 'var(--text-faint)' }} aria-hidden />
          <span>{r.name}</span>
          {r.own_worker ? <span className="faint">own worker</span> : null}
        </span>
      ) },
    { key: 'status', header: 'Status', align: 'l', simple: true, sortValue: (r) => r.status,
      cell: (r) => <StatusPill size="sm" {...modelStatus(r.status, { paperMode, trades: r.trades_closed })} raw={r.status} /> },
    { key: 'last_scan', header: 'Last scan', align: 'l', simple: true,
      sortValue: (r) => r.last_scan_at || r.last_seen_at || null,
      cell: (r) => (r.last_scan_at || r.last_seen_at ? fmtAgo(r.last_scan_at || r.last_seen_at) : 'never') },
    { key: 'symbols_scanned', header: 'Scanned', simple: true, sortValue: (r) => r.symbols_scanned,
      cell: (r) => fmtNum(r.symbols_scanned, 0) },
    { key: 'symbols_with_data', header: 'With data', sortValue: (r) => r.symbols_with_data,
      cell: (r) => fmtNum(r.symbols_with_data, 0) },
    { key: 'signals_today', header: 'Signals today', term: 'signals_today', simple: true,
      sortValue: (r) => r.signals_today, isEmpty: () => allIdle,
      cell: (r) => fmtNum(r.signals_today, 0) },
    { key: 'errors', header: 'Errors', simple: true, sortValue: (r) => r.errors,
      cell: (r) => <span className={r.errors ? 'neg' : undefined}>{fmtNum(r.errors, 0)}</span> },
    { key: 'risk_model', header: 'Risk model', align: 'l', cell: (r) => humanKey(r.risk_model) },
    { key: 'skip_reason', header: 'Why idle', align: 'l', simple: true,
      cell: (r) => {
        // Gate counters say what a strategy refused and why, so "scanned 45,
        // no signals" can be read without opening the database.
        const g = (r as { gates?: Record<string, number> }).gates;
        const parts = g ? Object.entries(g).filter(([, v]) => v > 0).map(
          ([k, v]) => `${v} ${GATE_WORDS[k] ?? k.replace(/_/g, ' ')}`) : [];
        return (
          <span className="dim">
            {r.skip_reason || (parts.length ? '' : '—')}
            {parts.length ? (
              <span className="faint">{r.skip_reason ? ' · ' : ''}{parts.join(' · ')}</span>
            ) : null}
          </span>
        );
      } },
  ], [paperMode, allIdle]);

  // LIVE and PAPER LIVE both read "Paper trading" in plain English, so merge counts by label.
  const countsLine = useMemo(() => {
    if (!s) return '';
    const byLabel = new Map<string, number>();
    for (const [k, v] of Object.entries(s.counts)) {
      const label = modelStatus(k, { paperMode }).label.toLowerCase();
      byLabel.set(label, (byLabel.get(label) ?? 0) + v);
    }
    return [...byLabel.entries()].sort((a, b) => b[1] - a[1]).map(([label, v]) => `${v} ${label}`).join(' · ');
  }, [s, paperMode]);
  const staleMin = s ? Math.round(s.stale_after_seconds / 60) : null;
  const cycles = status?.scanner?.cycles;
  const phase = h?.scheduler?.phase ?? status?.phase;
  const backup = backupPill(h?.backup);

  const endpointCols: Column<HealthDetail['endpoints'][number]>[] = [
    { key: 'provider', header: 'Provider', align: 'l', simple: true, sortValue: (r) => r.provider, cell: (r) => r.provider },
    { key: 'endpoint', header: 'Endpoint', align: 'l', simple: true, sortValue: (r) => r.endpoint,
      cell: (r) => <code className={c.code}>{r.endpoint}</code> },
    { key: 'calls', header: 'Calls', simple: true, sortValue: (r) => r.calls, cell: (r) => fmtNum(r.calls, 0) },
    { key: 'ok', header: 'Succeeded', simple: true, sortValue: (r) => r.ok,
      cell: (r) => <span className={r.ok === r.calls ? 'pos' : 'neg'}>{fmtNum(r.ok, 0)} / {fmtNum(r.calls, 0)}</span> },
    { key: 'last_status', header: 'Last HTTP', simple: true, sortValue: (r) => r.last_status,
      cell: (r) => <span className={r.last_status >= 400 || r.last_status === 0 ? 'neg' : 'pos'}>{r.last_status}</span> },
    { key: 'last_count', header: 'Records', simple: true, sortValue: (r) => r.last_count, cell: (r) => fmtNum(r.last_count, 0) },
    { key: 'avg_latency_ms', header: 'Avg ms', simple: true, sortValue: (r) => r.avg_latency_ms, cell: (r) => fmtNum(r.avg_latency_ms, 0) },
    { key: 'last_ts', header: 'Last call', align: 'l', simple: true, sortValue: (r) => r.last_ts, cell: (r) => fmtEtDate(r.last_ts) },
  ];
  const runCols: Column<HealthDetail['runs'][number]>[] = [
    { key: 'id', header: 'Run', simple: true, sortValue: (r) => r.id, cell: (r) => `#${r.id}` },
    { key: 'phase', header: 'Phase', align: 'l', simple: true, sortValue: (r) => r.phase, cell: (r) => phaseLabel(r.phase).label },
    { key: 'status', header: 'Status', align: 'l', simple: true, sortValue: (r) => r.status,
      cell: (r) => <StatusPill size="sm" {...(RUN_STATUS[r.status] ?? { label: 'Unknown', tone: 'neutral' })} raw={r.status} /> },
    { key: 'universe', header: 'Universe', simple: true, sortValue: (r) => r.universe, cell: (r) => fmtNum(r.universe, 0) },
    { key: 'shortlisted', header: 'Shortlist', simple: true, sortValue: (r) => r.shortlisted, cell: (r) => fmtNum(r.shortlisted, 0) },
    { key: 'enriched', header: 'Enriched', simple: true, sortValue: (r) => r.enriched, cell: (r) => fmtNum(r.enriched, 0) },
    { key: 'api_calls', header: 'API calls', simple: true, sortValue: (r) => r.api_calls, cell: (r) => fmtNum(r.api_calls, 0) },
    { key: 'started', header: 'Started', align: 'l', simple: true, sortValue: (r) => r.started, cell: (r) => fmtEtDate(r.started) },
    { key: 'error', header: 'Error', align: 'l', simple: true, cell: (r) => (r.error ? <span className="neg">{r.error}</span> : '—') },
  ];

  return (
    <>
      <SectionHeader level={1} title="System health" question="Is anything broken?"
        caption={loaded
          ? `Scan #${cycles ?? '—'} since start · ${phase ? phaseLabel(phase).label : 'phase unknown'} · checked every 15–20 s`
          : 'Checking…'} />

      {/* Verdict */}
      {!loaded ? (
        <div className={`${c.verdict} skel-tile`} aria-busy="true">
          <div className="skel" style={{ height: 28, width: 200 }}>&nbsp;</div>
          <div className="skel" style={{ height: 14, width: 320 }}>&nbsp;</div>
        </div>
      ) : (
        <div className={c.verdict} role="status">
          <div>
            {problems.length === 0
              ? <StatusPill label="All systems normal" tone="buy" />
              : <StatusPill label={`${problems.length} problem${problems.length === 1 ? '' : 's'}`} tone="risk" />}
          </div>
          {problems.length === 0 ? (
            <div className={c.line}>Scanner, backups, every strategy and every FMP endpoint checked out.</div>
          ) : problems.map((p, i) => <div key={i} className={`${c.line} ${c.problem}`}>{p.text}</div>)}
          <div className={c.row}>
            <StatusPill size="sm" label={scanner.label} tone={scanner.tone} />
            <StatusPill size="sm" label={backup.label} tone={backup.tone} />
            {backup.detail ? <span className={c.hint}>{backup.detail}</span> : null}
          </div>
        </div>
      )}

      {/* Strategies */}
      <SectionHeader title="Every strategy" question="Which strategies are running, and which are idle or failing?"
        caption={s ? `${countsLine} · a strategy not heard from for ${staleMin} minutes reads Offline` : undefined} />
      <DataTable<StrategyHealthRow> rows={strategies} columns={stratCols} rowKey={(r) => r.id} loaded={sLoaded}
        defaultSort={{ key: 'errors', dir: 'desc' }} minWidth={760}
        suppressedNote={() => 'Signals today hidden — no strategy has scanned anything yet this session'}
        empty={<EmptyState compact headline="No strategies reported" reason="The scheduler has not published any strategy heartbeat yet." />} />
      {s ? (
        <Details summary="What the statuses mean">
          <div className={c.stack}>
            {Object.entries(s.legend).map(([k, v]) => (
              <div key={k} className={c.row}>
                <StatusPill size="sm" {...modelStatus(k, { paperMode })} raw={k} />
                <span className={c.line}>{v}</span>
              </div>
            ))}
          </div>
        </Details>
      ) : null}

      {/* Entitlements */}
      <SectionHeader title="Data plan" question="Can the scanner reach every data endpoint it needs?"
        caption="FMP endpoints probed live against the paid account" />
      {!hLoaded ? <div className="skel" style={{ height: 40 }} /> : entitlements.length === 0 ? (
        <EmptyState compact headline="No probes yet this session" reason="Endpoints are probed the first time the scanner needs them." />
      ) : (
        <>
          <div className={c.row}>
            <StatusPill label={`${entOk} of ${entitlements.length} FMP endpoints available`}
              tone={entOk === entitlements.length ? 'buy' : 'risk'} />
          </div>
          <Details summary="Show every endpoint">
            <div className={c.stack}>
              {entitlements.map(([name, e]) => (
                <div key={name} className={c.row}>
                  <StatusPill size="sm" label={e.ok ? 'Available' : `Not in plan (HTTP ${e.status})`} tone={e.ok ? 'buy' : 'risk'} />
                  <span className={c.line}>{name}</span>
                </div>
              ))}
            </div>
          </Details>
        </>
      )}

      {/* Advanced: endpoint freshness + runs */}
      <Advanced>
        <SectionHeader title="Endpoint freshness — last 6 hours" question="Which provider calls are failing or slow?"
          caption="A run = one full pass over the stock universe" />
        <DataTable rows={h?.endpoints ?? []} columns={endpointCols} rowKey={(r) => `${r.provider}:${r.endpoint}`}
          loaded={hLoaded} minWidth={760} dense
          empty={<EmptyState compact headline="No provider calls in the window" reason="Nothing has been requested from a data provider in the last 6 hours." />} />

        <SectionHeader title="Scanner runs" question="Did each pass over the universe finish, and how much did it cost?"
          caption="A run = one full pass over the stock universe" />
        <DataTable rows={h?.runs ?? []} columns={runCols} rowKey={(r) => String(r.id)} loaded={hLoaded} minWidth={760} dense
          defaultSort={{ key: 'id', dir: 'desc' }}
          empty={<EmptyState compact headline="No runs recorded" reason="The scanner has not completed a pass yet." />} />
      </Advanced>

      {/* Events */}
      <SectionHeader title="Recent events" question="What has the system logged lately?" />
      {!hLoaded ? <div className="skel" style={{ height: 120 }} /> : (h?.events?.length ?? 0) === 0 ? (
        <EmptyState compact headline="No events" reason="Nothing has been logged yet." />
      ) : (
        <div className="timeline">
          {h!.events.map((e, i) => (
            <div className="tl-item" key={`${e.ts}-${i}`}>
              <span className="tl-time">{fmtEtDate(e.ts)}</span>
              <StatusPill size="sm" label={e.component}
                tone={e.level === 'error' ? 'risk' : e.level === 'warn' || e.level === 'warning' ? 'warn' : 'neutral'} />
              <span className="dim">{fmtMessage(e.message, advanced)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
