'use client';
import { useEffect, useState } from 'react';
import { useSharedPoll } from '../lib/api';
import { fmtEtShort, fmtNum } from '../lib/format';
import { useMode } from '../lib/mode';
import { useMarketPhase, useScannerState, useStatus } from '../lib/status';
import type { Canonical, ModelsPayload, StatusPayload } from '../lib/types';
import { regime as regimeLabel } from '../lib/vocab';
import { Advanced } from './ui/Advanced';
import { GlossaryPanel } from './ui/GlossaryPanel';
import { ModeToggle } from './ui/ModeToggle';
import { Term } from './ui/Popover';
import { StatusPill } from './ui/StatusPill';

/** Local clock in America/New_York. Empty until mounted (SSR-safe). */
function useEtClock(seconds: boolean): string {
  const [clock, setClock] = useState('');
  useEffect(() => {
    const fmt = () => new Date().toLocaleTimeString('en-US', {
      timeZone: 'America/New_York', hourCycle: 'h23', hour: '2-digit', minute: '2-digit',
      ...(seconds ? { second: '2-digit' } : {}),
    });
    setClock(fmt());
    const id = setInterval(() => setClock(fmt()), 1000);
    return () => clearInterval(id);
  }, [seconds]);
  return clock;
}

const DATA_USE_LIMIT = 300;   // FMP plan: 300 calls/min
const DATA_USE_WARN = 240;

/** Advanced-only items 6–12 (spec §1.2). Mounted only in Advanced, so /api/models
 *  and /api/report/canonical are never fetched in Simple. */
function AdvancedItems({ status }: { status: StatusPayload | null }) {
  const { data: models } = useSharedPoll<ModelsPayload>('/api/models', 60000);
  const { data: canonical } = useSharedPoll<Canonical>('/api/report/canonical', 60000);
  const perMin = status?.api_calls_per_min;
  const throttles = status?.api_throttles_1h ?? 0;
  const cost = status?.ai_usage_month?.est_cost_usd;
  const accounts = models?.models ?? null;
  const equity = accounts ? accounts.reduce((s, m) => s + (m.account?.equity ?? 0), 0) : null;
  const stripV = (v: string | null | undefined) => (v ? String(v).replace(/^v/i, '') : v);
  const engine = stripV(canonical?.versions?.strategy_version);
  const settingsVersion = stripV(status?.strategy_version);
  return (
    <div className="tb-adv">
      {throttles > 0 ? <StatusPill size="sm" tone="risk" label={`Throttled ${throttles} in last hour`} /> : null}
      <span className="tb-item">Tracked signals (all models) <b>{status?.active_signals ?? '—'}</b></span>
      <span className={`tb-item${(perMin ?? 0) > DATA_USE_WARN ? ' is-warn' : ''}`}>
        Data use <b>{perMin == null ? '—' : Math.round(perMin)}/{DATA_USE_LIMIT}</b> per min
      </span>
      <span className="tb-item">AI cost this month <b>{cost == null ? '—' : `$${cost.toFixed(2)}`}</b></span>
      {accounts && equity != null ? (
        <span className="tb-item">Paper accounts total <b>${fmtNum(equity, 0)}</b> ({accounts.length} × $10k)</span>
      ) : null}
      {models?.regime?.state ? (
        <span className="tb-item"><Term k="regime">Market type</Term>: <b>{regimeLabel(models.regime.state).label}</b></span>
      ) : null}
      {engine ? (
        <span className="tb-item faint">
          engine v{engine}{settingsVersion && settingsVersion !== engine ? ` · settings v${settingsVersion}` : ''}
        </span>
      ) : null}
    </div>
  );
}

/** One 44px line, one data source (`useStatus`). Simple: phase · clock · scanner ·
 *  next scan · mode toggle · glossary. Advanced appends the machinery. */
export default function TopBar() {
  const { status, loaded } = useStatus();
  const { advanced } = useMode();
  const phase = useMarketPhase();
  const scanner = useScannerState();
  const clock = useEtClock(advanced);
  const [glossary, setGlossary] = useState(false);
  const scanning = phase.isPremarket || phase.isOpen;

  return (
    <header className="topbar" role="banner">
      {loaded
        ? <StatusPill label={phase.label} tone={phase.tone} raw={phase.key === 'unknown' ? phase.raw ?? undefined : undefined} />
        : <span className="pill pill--neutral skel" aria-busy="true" style={{ width: 96, color: 'transparent' }}>…</span>}
      <span className="tb-clock" aria-label="Eastern time">{clock || (advanced ? '--:--:--' : '--:--')} ET</span>
      <StatusPill label={scanner.label} tone={scanner.tone} href="/health" />
      {scanning ? (
        <span className="tb-item">Scanning now · <b>{status?.scanner?.candidates ?? '—'}</b> candidates</span>
      ) : null}
      <span className="tb-item">Next scan <b>{fmtEtShort(status?.next_scan_start)}</b></span>
      <Advanced fallback={<span className="tb-spacer" />}>
        <AdvancedItems status={status} />
      </Advanced>
      <ModeToggle />
      <button type="button" className="tb-help" aria-label="Glossary — what the words mean"
        aria-expanded={glossary} onClick={() => setGlossary(true)}>?</button>
      <GlossaryPanel open={glossary} onClose={() => setGlossary(false)} />
    </header>
  );
}
