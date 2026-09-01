'use client';
import { usePolling } from '../../lib/api';
import { fmtNum, fmtPct } from '../../lib/format';

interface Group { n: number; win_rate: number | null; avg_change_pct: number | null;
  avg_max_gain_pct: number | null; avg_max_drawdown_pct: number | null; }
interface Perf { total_signals: number; groups: Record<string, Record<string, Group>>;
  outcomes?: { win: number; neutral: number; loss: number; pending: number;
               win_rate: number | null; by_type: Record<string, Record<string, number>> }; }

const TITLES: Record<string, string> = {
  score_band: 'By score band', catalyst: 'By catalyst type',
  market_cap: 'By market cap', strategy_version: 'By strategy version',
};

export default function PerformancePage() {
  const [perf] = usePolling<Perf>('/api/performance', 60000);
  if (!perf) return <div className="skel" style={{ height: 300, marginTop: 20 }} />;
  return (
    <>
      <div className="sect">
        <h2>Performance</h2>
        <span className="meta">honest aggregates over {perf.total_signals} recorded signals (demo excluded)</span>
      </div>
      {perf.outcomes && (
        <>
          <div className="sect"><h2 style={{ fontSize: 13 }}>Scanner scoreboard</h2>
            <span className="meta">judged on the first minutes after each pick becomes tradable — a pop = WIN forever, a drop = LOSS; never re-judged later</span></div>
          <div className="cards" style={{ marginTop: 4 }}>
            <div className="card"><h3>Win rate</h3>
              <div className="big pos">{perf.outcomes.win_rate != null ? (perf.outcomes.win_rate * 100).toFixed(0) + '%' : '—'}</div>
              <div className="sub">{perf.outcomes.win + perf.outcomes.loss} decided</div></div>
            <div className="card"><h3>Early-window pops</h3><div className="big pos">{perf.outcomes.win}</div></div>
            <div className="card"><h3>Neutral</h3><div className="big dim">{perf.outcomes.neutral}</div></div>
            <div className="card"><h3>Losses</h3><div className="big neg">{perf.outcomes.loss}</div>
              <div className="sub">{perf.outcomes.pending} still pending</div></div>
          </div>
          {Object.keys(perf.outcomes.by_type).length > 1 && (
            <div className="tbl-wrap" style={{ marginBottom: 8 }}>
              <table className="tbl" style={{ minWidth: 420 }}>
                <thead><tr><th className="l">Type</th><th>Win</th><th>Neutral</th><th>Loss</th><th>Pending</th></tr></thead>
                <tbody>
                  {Object.entries(perf.outcomes.by_type).map(([t, o]) => (
                    <tr key={t} style={{ cursor: 'default' }}>
                      <td className="l"><span className={`badge ${t === 'buy' ? 'buy' : 'early'}`}>{t.toUpperCase()}</span></td>
                      <td className="pos">{o.win}</td><td>{o.neutral}</td>
                      <td className="neg">{o.loss}</td><td className="dim">{o.pending}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {perf.total_signals === 0 && (
        <div className="tbl-wrap"><div className="empty">
          <b>No signals recorded yet</b>
          Aggregate performance appears after the first genuine BUY transitions are tracked.
        </div></div>
      )}
      {Object.entries(perf.groups).map(([g, items]) => (
        <div key={g}>
          <div className="sect"><h2 style={{ fontSize: 13 }}>{TITLES[g] ?? g}</h2></div>
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 620 }}>
              <thead><tr>
                <th className="l">Bucket</th><th>Signals</th><th>Win rate</th>
                <th>Avg change</th><th>Avg max gain</th><th>Avg max DD</th>
              </tr></thead>
              <tbody>
                {Object.entries(items).map(([k, v]) => (
                  <tr key={k} style={{ cursor: 'default' }}>
                    <td className="l">{k}</td>
                    <td>{v.n}</td>
                    <td>{v.win_rate != null ? fmtNum(v.win_rate * 100, 0) + '%' : '—'}</td>
                    <td className={(v.avg_change_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(v.avg_change_pct)}</td>
                    <td className="pos">{fmtPct(v.avg_max_gain_pct)}</td>
                    <td className="neg">{fmtPct(v.avg_max_drawdown_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <p className="disclaimer">Aggregates measure signal behavior only; they include no fills,
        slippage, or costs and are not a performance guarantee.</p>
    </>
  );
}
