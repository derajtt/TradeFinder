'use client';
import { usePolling } from '../../lib/api';
import { fmtNum } from '../../lib/format';

function M({ m }: { m: any }) {
  if (!m || m.n == null) return <span className="faint">—</span>;
  return (
    <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
      n={m.n} · WR {m.win_rate != null ? (m.win_rate * 100).toFixed(0) + '%' : '—'}
      {m.win_rate_lb != null && <span className="faint"> (LB {(m.win_rate_lb * 100).toFixed(0)}%)</span>}
      {' '}· exp {m.expectancy_pct != null ? m.expectancy_pct.toFixed(2) + '%' : '—'}
      {' '}· PF {m.profit_factor ?? '—'} · DD {m.max_drawdown_pct ?? '—'}%
    </span>
  );
}

export default function BacktestPage() {
  const [r] = usePolling<any>('/api/backtest/report', 60000);
  const [all] = usePolling<any>('/api/backtest/reports', 60000);
  const fleet = all?.reports?.fleet?.result;
  const nightly = all?.reports?.nightly?.result;
  if (!r) return <div className="skel" style={{ height: 300, marginTop: 20 }} />;
  if (!r.available) {
    return (<><div className="sect"><h2>Backtesting</h2></div>
      <div className="tbl-wrap"><div className="empty"><b>No backtest imported yet</b>{r.note}</div></div></>);
  }
  const res = r.result || {};
  const cov = res.coverage || {};
  const search = res.search || {};
  const hold = res.holdout || {};
  const wf = res.walk_forward || {};
  const primary = res.primary || {};
  return (
    <>
      <div className="sect"><h2>Backtesting</h2>
        <span className="meta">config {r.config_hash || '—'} · imported {r.created_at?.slice(0, 16)} · all labels honest: dev / val / walk-forward / holdout are separate cohorts, never combined</span></div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Primary policy (installed)</h2></div>
      <div className="kv-grid">
        <div className="kv"><div className="k">Strategy version</div><div className="v">{primary.strategy_version ?? '—'}</div></div>
        <div className="kv"><div className="k">Mode</div><div className="v">{primary.mode ?? '—'}</div></div>
        <div className="kv"><div className="k">Exit policy</div><div className="v">{primary.exit ?? '—'}</div></div>
        <div className="kv"><div className="k">Holdout passed</div>
          <div className="v" style={{ color: primary.holdout_pass ? 'var(--buy)' : 'var(--risk)' }}>{String(primary.holdout_pass ?? '—')}</div></div>
      </div>
      {primary.entry && (
        <div className="tl-item" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
          entry gates: {JSON.stringify(primary.entry)}
        </div>
      )}

      <div className="sect"><h2 style={{ fontSize: 13 }}>Data coverage (honest)</h2></div>
      <div className="timeline">
        {(res.coverage_notes || [
          '1-minute history: UNAVAILABLE on current FMP plan — replay runs on 5-minute bars (premarket included, verified from 4:00 AM).',
          'Historical float: CURRENT-VALUE ONLY — rotation zones labeled ESTIMATED_CURRENT_FLOAT.',
          'Global historical news: PARTIAL (large-cap biased) — discovery is SEC-filing-timestamped + per-symbol news enrichment.',
          'Historical bid/ask: UNAVAILABLE — spreads estimated by price tier & dollar volume; fills at next-bar open + slippage models.',
        ]).map((n: string, i: number) => <div className="tl-item" key={i}><span style={{ fontSize: 12 }}>{n}</span></div>)}
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Splits & results</h2></div>
      <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 700 }}>
        <thead><tr><th className="l">Cohort</th><th className="l">Range</th><th className="l">Result (baseline execution)</th></tr></thead>
        <tbody>
          <tr style={{ cursor: 'default' }}><td className="l">Development</td><td className="l dim">{res.splits?.dev?.join(' → ')}</td><td className="l"><M m={search.dev_metrics} /></td></tr>
          <tr style={{ cursor: 'default' }}><td className="l">Validation</td><td className="l dim">{res.splits?.val?.join(' → ')}</td><td className="l"><M m={search.val_metrics} /></td></tr>
          <tr style={{ cursor: 'default' }}><td className="l">Walk-forward (unseen blocks)</td><td className="l dim">5 folds</td><td className="l"><M m={wf.combined} /></td></tr>
          <tr style={{ cursor: 'default' }}><td className="l"><b>Untouched holdout (one look)</b></td><td className="l dim">{res.splits?.holdout?.join(' → ')}</td><td className="l"><M m={hold.baseline} /></td></tr>
          <tr style={{ cursor: 'default' }}><td className="l">Holdout · pessimistic execution</td><td className="l dim"></td><td className="l"><M m={hold.pessimistic} /></td></tr>
        </tbody>
      </table></div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Search discipline</h2></div>
      <div className="kv-grid">
        <div className="kv"><div className="k">Configs tested</div><div className="v">{res.configs_tested ?? '—'}</div></div>
        <div className="kv"><div className="k">Rounds</div><div className="v">{res.rounds ?? '—'}</div></div>
        <div className="kv"><div className="k">Stopped because</div><div className="v" style={{ fontFamily: 'var(--sans)', fontSize: 11.5 }}>{res.converged_because ?? '—'}</div></div>
        <div className="kv"><div className="k">PBO estimate</div><div className="v">{res.pbo ?? 'n/a'}</div></div>
        <div className="kv"><div className="k">Jitter robust</div><div className="v">{String(res.search?.jitter_ok ?? '—')}</div></div>
        <div className="kv"><div className="k">API calls / cache hits</div><div className="v">{res.api_calls} / {res.cache_hits}</div></div>
      </div>
      {fleet && (<>
        <div className="sect"><h2 style={{ fontSize: 13 }}>Fleet backtests — every daily-testable model</h2>
          <span className="meta">{fleet.cohort} · {fleet.sessions} sessions ({fleet.date_range?.join(' → ')}) · {fleet.trades_total} trades · frozen first-pass settings, no tuning</span></div>
        <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 820 }}>
          <thead><tr><th className="l">Model</th><th>n</th><th>WR (LB)</th><th>Expectancy</th>
            <th>PF</th><th>Max DD</th><th>Ambig.</th><th title="expectancy in first vs second half — stability check">H1 / H2 exp</th></tr></thead>
          <tbody>{Object.entries(fleet.by_model ?? {}).map(([mid, m]: any) => (
            <tr key={mid} style={{ cursor: 'default' }}>
              <td className="l">{mid.replace(/_/g, ' ')}</td>
              <td>{m.n}</td>
              <td>{m.win_rate != null ? (m.win_rate * 100).toFixed(0) + '%' : '—'}
                <span className="faint"> ({m.win_rate_lb != null ? (m.win_rate_lb * 100).toFixed(0) : '—'})</span></td>
              <td className={(m.expectancy_pct ?? 0) >= 0 ? 'pos' : 'neg'}>{m.expectancy_pct}%</td>
              <td>{m.profit_factor}</td><td className="neg">{m.max_drawdown_pct}%</td>
              <td className="dim">{m.ambiguous}</td>
              <td className="dim">{m.first_half_exp}% / {m.second_half_exp}%</td>
            </tr>))}</tbody>
        </table></div>
        <p className="disclaimer">Forward-only (cannot be honestly tested on daily bars): {(fleet.forward_only_models ?? []).map((x: string) => x.replace(/_/g, ' ')).join(', ')}.
          Negative baselines are shown as-is — engines are NOT retuned to fit history; live paper decides.</p>
      </>)}
      {nightly && (<>
        <div className="sect"><h2 style={{ fontSize: 13 }}>Nightly research — latest run</h2></div>
        <div className="tl-item" style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
          {JSON.stringify(nightly.replay ?? nightly)} · promotion: {nightly.promotion?.decision} ({nightly.promotion?.reason})
        </div>
      </>)}
      <p className="disclaimer">Backtests use estimated spreads and next-bar fills on 5-minute data; they are evidence, not proof.
        Nothing here is promoted to the live paper strategy without walk-forward + holdout + forward paper confirmation.</p>
    </>
  );
}
