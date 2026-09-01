'use client';
import { useEffect, useState } from 'react';
import { usePolling, useEventStream } from '../lib/api';
import { fmtEt } from '../lib/format';
import type { StatusPayload } from '../lib/types';

export default function TopBar() {
  const [status, err, reload] = usePolling<StatusPayload>('/api/status', 15000);
  const [modelsResp] = usePolling<{ models: { account: { equity: number } }[]; regime: any }>('/api/models', 60000);
  const [clock, setClock] = useState('');
  useEventStream({ buy_signal: () => reload(), scanner: () => {} });

  useEffect(() => {
    const id = setInterval(() => {
      setClock(new Date().toLocaleTimeString('en-US', {
        timeZone: 'America/New_York', hour12: false,
      }));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const s = status?.scanner;
  const phase = status?.phase ?? '…';
  const scannerOk = s?.last_cycle_ok;
  return (
    <header className="topbar" role="banner">
      <span className="tb-item"><b>{clock || '—'}</b> ET</span>
      <span className={`phase-chip phase-${phase}`}>{phase}</span>
      <span className="tb-item">
        <span className={`dot ${err ? 'bad' : s?.paused ? 'warn' : scannerOk ? 'ok' : scannerOk === false ? 'bad' : 'idle'}`} />
        Scanner {err ? 'unreachable' : s?.paused ? 'paused' : scannerOk ? 'live' : scannerOk === false ? 'error' : 'starting'}
      </span>
      <span className="tb-item">Last cycle <b>{fmtEt(s?.last_cycle_at)}</b></span>
      {phase === 'closed' && (
        <span className="tb-item">Next scan <b>{fmtEt(status?.next_scan_start)}</b></span>
      )}
      <span className="tb-item">Candidates <b>{s?.candidates ?? '—'}</b></span>
      <span className="tb-item">Active BUY <b style={{ color: 'var(--buy)' }}>{status?.active_signals ?? '—'}</b></span>
      <span className="tb-item" title="Provider calls per minute (5-min avg) — FMP allows 300/min. Throttles = HTTP 429s in the last hour.">
        API <b>{status?.api_calls_per_min ?? '—'}/min</b>
        {(status?.api_throttles_1h ?? 0) > 0
          ? <span className="badge risk">{status?.api_throttles_1h} throttled</span>
          : <span className="fresh ok">● no throttles</span>}
      </span>
      <span className="tb-item">AI mo. <b>${status?.ai_usage_month?.est_cost_usd?.toFixed(2) ?? '—'}</b></span>
      {modelsResp?.regime && (
        <span className={`regime-chip regime-${modelsResp.regime.state}`}
          title={`Regime Controller: ${modelsResp.regime.why}`}>
          {modelsResp.regime.state}
        </span>
      )}
      {modelsResp?.models && (
        <span className="tb-item" title="Total paper equity across all model accounts (each started at $10,000)">
          Σ paper <b>${(modelsResp.models.reduce((s, m) => s + (m.account?.equity ?? 0), 0) / 1000).toFixed(1)}k</b>
        </span>
      )}
      <span className="tb-item faint">{status?.strategy_version}</span>
    </header>
  );
}
