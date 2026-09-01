'use client';
import { useEffect, useState } from 'react';
import { usePolling, useEventStream } from '../lib/api';
import { fmtEt } from '../lib/format';
import type { StatusPayload } from '../lib/types';

export default function TopBar() {
  const [status, err, reload] = usePolling<StatusPayload>('/api/status', 15000);
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
      <span className="tb-item">API 24h <b>{status?.api_calls_24h ?? '—'}</b></span>
      <span className="tb-item">AI mo. <b>${status?.ai_usage_month?.est_cost_usd?.toFixed(2) ?? '—'}</b></span>
      <span className="tb-item faint">{status?.strategy_version}</span>
    </header>
  );
}
