'use client';
import { usePolling } from '../../lib/api';

export default function ExitLabPage() {
  const [r] = usePolling<any>('/api/backtest/report', 60000);
  if (!r) return <div className="skel" style={{ height: 300, marginTop: 20 }} />;
  const t = r?.result?.tournament;
  const decay = r?.result?.mfe_decay ?? [];
  if (!r.available || !t) {
    return (<><div className="sect"><h2>Exit Strategy Lab</h2></div>
      <div className="tbl-wrap"><div className="empty"><b>No tournament data yet</b>
        The exit tournament fills in after the first imported backtest run.</div></div></>);
  }
  const rows = Object.entries(t).filter(([, v]: any) => v.baseline)
    .map(([name, v]: any) => ({ name, ...v }))
    .sort((a: any, b: any) => (b.baseline.win_rate_lb ?? -1) - (a.baseline.win_rate_lb ?? -1));
  const unavailable = Object.entries(t).filter(([, v]: any) => v.unavailable);
  const maxAbs = Math.max(1, ...decay.map((d: any) => Math.abs(d.avg_mfe_pct)));
  return (
    <>
      <div className="sect"><h2>Exit Strategy Lab</h2>
        <span className="meta">identical frozen signals & entries for every policy — only the exit differs · baseline execution shown, ranked by reliable (lower-bound) win rate</span></div>

      {decay.length > 0 && (<>
        <div className="sect"><h2 style={{ fontSize: 13 }}>MFE decay — average peak profit by minutes after entry</h2>
          <span className="meta">reveals whether profits peak in the first minutes or build into the open</span></div>
        <div className="tbl-wrap" style={{ padding: '14px 16px' }}>
          <svg viewBox="0 0 700 120" style={{ width: '100%', height: 120 }} role="img" aria-label="MFE decay chart">
            <line x1="30" x2="690" y1="95" y2="95" stroke="var(--line)" />
            {decay.map((d: any, i: number) => {
              const x = 30 + (i / Math.max(1, decay.length - 1)) * 660;
              const hM = Math.max(2, (d.avg_mfe_pct / maxAbs) * 70);
              const hU = (d.avg_unrealized_pct / maxAbs) * 70;
              return (<g key={i}>
                <rect x={x - 7} y={95 - hM} width={6} height={hM} fill="var(--accent)" opacity="0.45" />
                <rect x={x + 1} y={hU >= 0 ? 95 - hU : 95} width={6} height={Math.abs(hU)} fill={hU >= 0 ? 'var(--buy)' : 'var(--risk)'} />
                <text x={x} y={110} fontSize="8" fill="var(--text-faint)" textAnchor="middle">{d.minute}m</text>
              </g>);
            })}
            <text x="34" y="16" fontSize="9" fill="var(--accent)">▮ avg MFE (peak)   </text>
            <text x="140" y="16" fontSize="9" fill="var(--buy)">▮ avg unrealized (mark)</text>
          </svg>
        </div>
      </>)}

      <div className="sect"><h2 style={{ fontSize: 13 }}>Tournament — {rows.length} policies</h2></div>
      <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 900 }}>
        <thead><tr>
          <th className="l">Policy</th><th className="l">Family</th><th>n</th>
          <th title="Win rate with Wilson lower bound — a 3/3 record cannot outrank a stable 60% on 200 trades">WR (LB)</th>
          <th>Expectancy</th><th>PF</th><th>Max DD</th><th>Ambig.</th>
          <th title="Return per minute of capital exposure">Ret/min</th>
          <th>Pessimistic exp.</th>
        </tr></thead>
        <tbody>{rows.map((p: any) => (
          <tr key={p.name} style={{ cursor: 'default' }}>
            <td className="l" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{p.name}</td>
            <td className="l"><span className="badge neutral">{p.family}</span></td>
            <td>{p.baseline.n}</td>
            <td>{p.baseline.win_rate != null ? (p.baseline.win_rate * 100).toFixed(0) + '%' : '—'}
              <span className="faint"> ({p.baseline.win_rate_lb != null ? (p.baseline.win_rate_lb * 100).toFixed(0) : '—'})</span></td>
            <td className={(p.baseline.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{p.baseline.expectancy_pct?.toFixed(2)}%</td>
            <td>{p.baseline.profit_factor ?? '—'}</td>
            <td className="neg">{p.baseline.max_drawdown_pct}%</td>
            <td className="dim">{p.baseline.ambiguous}</td>
            <td className="dim">{p.baseline.ret_per_min ?? '—'}</td>
            <td className={(p.pessimistic?.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{p.pessimistic?.expectancy_pct?.toFixed(2) ?? '—'}%</td>
          </tr>))}</tbody>
      </table></div>
      {unavailable.length > 0 && (
        <p className="disclaimer">Not historically testable at 5-minute resolution (forward/live shadow only): {unavailable.map(([n]) => n).join(', ')}.</p>
      )}
      <p className="disclaimer">AMBIGUOUS = stop and target inside one bar with unknown tick order — never counted as a win.
        Rankings use conservative execution; nothing is promoted from this table without walk-forward + forward paper evidence.</p>
    </>
  );
}
