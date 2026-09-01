'use client';
import { useState } from 'react';
import { usePolling } from '../lib/api';

interface Ops {
  now_et: string; phase: string; regime_text?: string; quiet_reason?: string | null;
  lanes: { lane: string; state: string; detail: string }[];
  upcoming: { event: string; at_et: string }[];
  not_running: { what: string; why: string }[];
}

const GOOD = ['RUNNING', 'OPEN', 'RUNNING 24/7', 'DONE TODAY', 'TREND', 'RANGE'];
const WARN = ['SCHEDULED', 'DAILY MODELS ONLY', 'UNCERTAIN', 'IDLE (no session)'];

function chipClass(state: string) {
  if (GOOD.includes(state)) return 'buy';
  if (WARN.includes(state)) return 'warn';
  if (state === 'CLOSED' || state === 'HIGH_RISK') return 'risk';
  return 'neutral';
}

export default function OpsPanel() {
  const [ops] = usePolling<Ops>('/api/ops', 30000);
  const [open, setOpen] = useState(true);
  if (!ops) return null;
  return (
    <div className="tbl-wrap" style={{ padding: '12px 16px', marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <b style={{ fontSize: 11, letterSpacing: 1.4, color: 'var(--text-faint)' }}>
          OPERATIONS — WHAT'S HAPPENING RIGHT NOW
        </b>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="ptab" style={{ padding: '2px 10px', fontSize: 10 }}
          onClick={() => setOpen((o) => !o)}>{open ? 'collapse' : 'expand'}</button>
      </div>
      {open && (
        <>
          {(ops.regime_text || ops.quiet_reason) && (
            <div style={{ marginTop: 8, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.5 }}>
              {ops.regime_text && <span>🧭 {ops.regime_text} </span>}
              {ops.quiet_reason && <span className="faint">· {ops.quiet_reason}</span>}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 8, marginTop: 10 }}>
            {ops.lanes.map((l) => (
              <div key={l.lane} className="gate" style={{ alignItems: 'flex-start' }} title={l.detail}>
                <span style={{ fontSize: 12 }}>{l.lane}
                  <div className="faint" style={{ fontSize: 10, marginTop: 2, whiteSpace: 'normal' }}>{l.detail}</div>
                </span>
                <span className={`badge ${chipClass(l.state)}`} style={{ flexShrink: 0 }}>{l.state}</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12, alignItems: 'baseline' }}>
            <b style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--text-faint)' }}>NEXT UP</b>
            {ops.upcoming.map((u, i) => (
              <span key={i} className="tb-item" style={{ fontSize: 11.5 }}>
                <b>{new Date(u.at_et).toLocaleString('en-US', { timeZone: 'America/New_York',
                  weekday: 'short', hour: 'numeric', minute: '2-digit' })}</b> {u.event}
              </span>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 8 }}>
            <b style={{ fontSize: 10, letterSpacing: 1.2, color: 'var(--text-faint)' }}>INTENTIONALLY OFF</b>
            {ops.not_running.map((n, i) => (
              <span key={i} className="faint" style={{ fontSize: 10.5 }} title={n.why}>◦ {n.what}</span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
