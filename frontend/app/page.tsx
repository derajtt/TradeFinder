'use client';
import { useCallback, useRef, useState } from 'react';
import CandidateTable from '../components/CandidateTable';
import DetailDrawer from '../components/DetailDrawer';
import SignalTable from '../components/SignalTable';
import { useEventStream, usePolling } from '../lib/api';
import { fmtPct, fmtPrice } from '../lib/format';
import type { CandidateRow, SignalRow, StatusPayload } from '../lib/types';

export default function Dashboard() {
  const [candResp] = usePolling<{ rows: CandidateRow[] }>('/api/candidates', 30000);
  const [sigResp, , reloadSigs] = usePolling<{ rows: SignalRow[] }>('/api/signals?active_only=true', 30000);
  const [status] = usePolling<StatusPayload>('/api/status', 20000);
  const [liveRows, setLiveRows] = useState<CandidateRow[] | null>(null);
  const [liveSigs, setLiveSigs] = useState<Record<string, Partial<SignalRow>>>({});
  const [updated, setUpdated] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const clearRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEventStream({
    candidates: (d) => {
      setLiveRows(d.rows ?? []);
      const syms = new Set<string>((d.rows ?? []).map((r: CandidateRow) => r.symbol));
      setUpdated(syms);
      if (clearRef.current) clearTimeout(clearRef.current);
      clearRef.current = setTimeout(() => setUpdated(new Set()), 1600);
    },
    signals: (d) => {
      setLiveSigs((prev) => {
        const next = { ...prev };
        for (const u of d.rows ?? []) next[u.signal_uid] = u;
        return next;
      });
    },
    buy_signal: () => reloadSigs(),
  });

  const rows = liveRows ?? candResp?.rows ?? [];
  const sigRows = (sigResp?.rows ?? []).map((s) => {
    const u = liveSigs[s.signal_uid] as any;
    return u ? { ...s, current: u.current ?? s.current,
      day_high: u.day_high ?? s.day_high, day_low: u.day_low ?? s.day_low,
      since_high: u.since_high ?? s.since_high, since_low: u.since_low ?? s.since_low,
      change_pct: u.change_pct ?? s.change_pct,
      max_gain_pct: u.max_gain_pct ?? s.max_gain_pct,
      max_drawdown_pct: u.max_drawdown_pct ?? s.max_drawdown_pct } : s;
  });

  const best = sigRows.reduce<SignalRow | null>((a, b) =>
    (b.change_pct ?? -1e9) > (a?.change_pct ?? -1e9) ? b : a, null);
  const topCand = rows[0];
  const onSelect = useCallback((sym: string) => setSelected(sym), []);

  return (
    <>
      <div className="cards">
        <div className={`card ${sigRows.length ? 'glow-buy' : ''}`}>
          <h3>Active BUY Signals</h3>
          <div className="big">{sigRows.length}</div>
          <div className="sub">{sigRows.length ? 'tracking from immutable initiation price' : 'none yet — all gates must pass'}</div>
        </div>
        <div className="card">
          <h3>Best Current Performer</h3>
          <div className="big">{best ? `${best.symbol} ${fmtPct(best.change_pct)}` : '—'}</div>
          <div className="sub">{best ? `BUY ${fmtPrice(best.buy_price)} → now ${fmtPrice(best.current)}` : 'no active signals'}</div>
        </div>
        <div className="card">
          <h3>Highest-Score Candidate</h3>
          <div className="big">{topCand ? `${topCand.symbol} · ${topCand.score.toFixed(0)}` : '—'}</div>
          <div className="sub">{topCand ? (topCand.catalyst_type || 'no catalyst identified') : 'scanner warming up'}</div>
        </div>
        <div className="card">
          <h3>Scanner Health</h3>
          <div className="big" style={{ color: status?.scanner?.last_cycle_ok ? 'var(--buy)' : 'var(--warn)' }}>
            {status?.scanner?.paused ? 'PAUSED' : status?.scanner?.last_cycle_ok ? 'LIVE' : status?.scanner?.last_cycle_ok === false ? 'ERROR' : '…'}
          </div>
          <div className="sub">cycle #{status?.scanner?.cycles ?? '—'} · {status?.phase}</div>
        </div>
      </div>

      <div className="sect">
        <h2>Active BUY Signals</h2>
        <span className="meta">initiation price never changes; live fields update separately</span>
      </div>
      <SignalTable rows={sigRows} compact onSelect={(s) => setSelected(s.symbol)} />

      <div className="sect" style={{ marginTop: 30 }}>
        <h2>Candidate Scanner</h2>
        <span className="meta">click any row for the full breakdown</span>
        <span className="spacer" />
      </div>
      <CandidateTable rows={rows} updatedSyms={updated} onSelect={onSelect} />

      <p className="disclaimer">
        BUY is a rules-based research signal produced by the documented scoring engine
        (momentum, catalyst, filings, liquidity, price confirmation, company quality, minus risk
        penalties). It is not investment advice, not a recommendation, and not connected to order
        execution. Data: Financial Modeling Prep &amp; SEC EDGAR; delays and gaps are labeled, never hidden.
      </p>

      {selected && <DetailDrawer symbol={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
