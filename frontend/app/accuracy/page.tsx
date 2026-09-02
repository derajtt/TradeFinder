'use client';
import { useState } from 'react';
import { usePolling } from '../../lib/api';
import { Tip } from '../../components/TradeRoadmap';

const COLS: [string, string, string][] = [
  ['name', 'Strategy', ''],
  ['version', 'Version', 'Materially different logic gets its own version and its own performance pool.'],
  ['paper_trades', 'Sample', 'Number of resolved paper trades. Small samples prove nothing.'],
  ['backtest_win_rate', 'Backtest win %', 'Measured on the training slice. Optimistic by construction.'],
  ['oos_win_rate', 'Out-of-sample win %', 'Measured once on data never used for fitting. This is the honest column.'],
  ['paper_win_rate', 'Paper win %', 'Observed forward paper result. Never blended with backtest figures.'],
  ['expectancy_r', 'Expectancy (R)', 'Average result per trade in units of risk. Ranks above win rate.'],
  ['profit_factor', 'Profit factor', 'Gross wins divided by gross losses.'],
  ['max_drawdown_pct', 'Max drawdown', 'Largest peak-to-trough fall in the paper ledger.'],
  ['sample', 'Confidence', ''],
];

export default function AccuracyPage() {
  const [data] = usePolling<any>('/api/accuracy', 60000);
  const [sort, setSort] = useState('expectancy_r');
  if (!data) return <div className="skel" style={{ height: 320, marginTop: 20 }} />;
  const rows = [...(data.rows || [])].sort((a, b) => {
    const av = a[sort], bv = b[sort];
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return typeof av === 'number' ? bv - av : String(av).localeCompare(String(bv));
  });
  return (
    <main className="wrap">
      <header className="page-head">
        <h1>Accuracy dashboard</h1>
        <p>{data.note}</p>
      </header>
      <div className="row" style={{ gap: 7, marginBottom: 12 }}>
        <span className="meta" style={{ marginRight: 4 }}>Sort by</span>
        {data.sortable.map((k: string) => (
          <button key={k} className={`tab ${sort === k ? 'on' : ''}`} onClick={() => setSort(k)}>
            {k.replace(/_/g, ' ')}
          </button>
        ))}
      </div>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead><tr>{COLS.map(([k, label, tip]) => (
            <th key={k}>{tip ? <Tip term={tip}><span>{label} ⓘ</span></Tip> : label}</th>
          ))}</tr></thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.id}>
                <td><span className="dot" style={{ background: r.color, marginRight: 7 }} />{r.name}
                  <br /><small className="muted">{r.risk_model === 'standard'
                    ? 'platform risk layer' : `${r.risk_model} risk model`}</small></td>
                <td className="mono">{r.version ?? '—'}</td>
                <td className="mono">{r.paper_trades ?? 0}</td>
                <td className="mono">{r.backtest_win_rate != null ? r.backtest_win_rate + '%' : '—'}</td>
                <td className="mono" style={{ fontWeight: 700 }}>
                  {r.oos_win_rate != null ? r.oos_win_rate + '%' : '—'}</td>
                <td className="mono">{r.paper_win_rate != null ? r.paper_win_rate + '%' : '—'}</td>
                <td className="mono" style={{ color: (r.expectancy_r ?? 0) > 0 ? 'var(--buy)' : (r.expectancy_r ?? 0) < 0 ? 'var(--risk)' : undefined }}>
                  {r.expectancy_r ?? '—'}</td>
                <td className="mono">{r.profit_factor ?? '—'}</td>
                <td className="mono">{r.max_drawdown_pct != null ? r.max_drawdown_pct + '%' : '—'}</td>
                <td><span className="badge neutral">{r.sample}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel" style={{ marginTop: 16 }}>
        <h3>How to read this</h3>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.7 }}>
          A backtest win rate is the number a strategy scores on the data it was fitted
          to; it is almost always the most flattering column and the least
          informative. The out-of-sample column is measured once, on data the fitting
          process never saw. Where the two disagree sharply, believe the second one.
          A strategy showing no numbers has not yet produced enough resolved trades to
          say anything, which is reported rather than hidden.
        </p>
      </div>
    </main>
  );
}
