'use client';
import { usePolling } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';

interface Health {
  env_status: Record<string, boolean>;
  backup?: { status: string; latest?: string; age_hours?: number; size_mb?: number; count?: number; note?: string };
  entitlements: Record<string, { ok: boolean; status: number }>;
  endpoints: { provider: string; endpoint: string; calls: number; ok: number;
    last_status: number; last_ts: string; avg_latency_ms: number; last_count: number }[];
  events: { ts: string; level: string; component: string; message: string }[];
  runs: { id: number; started: string; finished: string | null; phase: string; status: string;
    universe: number; shortlisted: number; enriched: number; api_calls: number; error: string }[];
  scheduler: { phase: string; cycles: number; last_error: string } | null;
}

export default function HealthPage() {
  const [h] = usePolling<Health>('/api/health/detail', 20000);
  if (!h) return <div className="skel" style={{ height: 400, marginTop: 20 }} />;
  return (
    <>
      <div className="sect"><h2>System Health</h2>
        <span className="meta">scheduler phase: {h.scheduler?.phase} · cycle #{h.scheduler?.cycles}</span></div>

      {h.backup && (
        <div className="kv" style={{ maxWidth: 380, marginBottom: 12 }}>
          <div className="k">Database backups</div>
          <div className="v" style={{ color: h.backup.status === 'OK' ? 'var(--buy)' : h.backup.status === 'NONE' ? 'var(--warn)' : 'var(--risk)' }}>
            {h.backup.status}{h.backup.latest && <span className="faint" style={{ fontSize: 10, marginLeft: 8 }}>
              {h.backup.latest} · {h.backup.age_hours}h old · {h.backup.size_mb}MB · {h.backup.count} kept</span>}
            {h.backup.note && <span className="faint" style={{ fontSize: 10, marginLeft: 8 }}>{h.backup.note}</span>}
          </div>
        </div>
      )}
      <div className="sect"><h2 style={{ fontSize: 13 }}>FMP plan entitlements</h2>
        <span className="meta">endpoints probed live against the paid account</span></div>
      <div className="kv-grid">
        {Object.entries(h.entitlements).length === 0 && <div className="dim">No probes yet this session.</div>}
        {Object.entries(h.entitlements).map(([name, e]) => (
          <div className="kv" key={name}>
            <div className="k">{name}</div>
            <div className="v" style={{ color: e.ok ? 'var(--buy)' : 'var(--risk)' }}>
              {e.ok ? '● available' : `○ not in plan (${e.status})`}
            </div>
          </div>
        ))}
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Endpoint freshness — last 6h</h2></div>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 700 }}>
          <thead><tr>
            <th className="l">Provider</th><th className="l">Endpoint</th><th>Calls</th>
            <th>OK</th><th>Last HTTP</th><th>Records</th><th>Avg ms</th><th className="l">Last call</th>
          </tr></thead>
          <tbody>
            {h.endpoints.map((e, i) => (
              <tr key={i} style={{ cursor: 'default' }}>
                <td className="l">{e.provider}</td>
                <td className="l" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{e.endpoint}</td>
                <td>{e.calls}</td>
                <td className={e.ok === e.calls ? 'pos' : 'neg'}>{e.ok}/{e.calls}</td>
                <td className={e.last_status >= 400 || e.last_status === 0 ? 'neg' : 'pos'}>{e.last_status}</td>
                <td>{e.last_count}</td>
                <td className="dim">{e.avg_latency_ms}</td>
                <td className="l dim" style={{ fontSize: 12 }}>{fmtEtDate(e.last_ts)}</td>
              </tr>
            ))}
            {h.endpoints.length === 0 && <tr><td colSpan={8} className="l dim">No provider calls in window.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Scanner runs</h2></div>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 700 }}>
          <thead><tr>
            <th>Run</th><th className="l">Phase</th><th className="l">Status</th><th>Universe</th>
            <th>Shortlist</th><th>Enriched</th><th>API calls</th><th className="l">Started</th><th className="l">Error</th>
          </tr></thead>
          <tbody>
            {h.runs.map((r) => (
              <tr key={r.id} style={{ cursor: 'default' }}>
                <td>#{r.id}</td>
                <td className="l">{r.phase}</td>
                <td className="l"><span className={`badge ${r.status === 'ok' ? 'buy' : r.status === 'error' ? 'risk' : 'neutral'}`}>{r.status}</span></td>
                <td>{r.universe}</td><td>{r.shortlisted}</td><td>{r.enriched}</td><td>{r.api_calls}</td>
                <td className="l dim" style={{ fontSize: 12 }}>{fmtEtDate(r.started)}</td>
                <td className="l neg" style={{ fontSize: 11, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.error || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Recent events</h2></div>
      <div className="timeline">
        {h.events.map((e, i) => (
          <div className="tl-item" key={i}>
            <span className="tl-time">{fmtEtDate(e.ts)}</span>
            <span className={`badge ${e.level === 'error' ? 'risk' : e.level === 'warn' ? 'warn' : 'neutral'}`}>{e.component}</span>
            <span className="dim" style={{ fontSize: 12 }}>{e.message}</span>
          </div>
        ))}
        {h.events.length === 0 && <div className="dim">No events.</div>}
      </div>
    </>
  );
}
