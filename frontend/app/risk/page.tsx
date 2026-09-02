'use client';
import { useEffect, useState } from 'react';
import { apiPostBody, apiPut, usePolling } from '../../lib/api';
import { money, Tip } from '../../components/TradeRoadmap';

const RISK_OPTIONS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0];

const FIELD_HELP: Record<string, string> = {
  account_equity: 'The balance position sizes are calculated from.',
  default_risk_pct: 'How much of the account a single trade is allowed to lose if the stop is hit.',
  max_risk_pct: 'A hard ceiling. Automatic adjustments can lower risk but never raise it past this.',
  max_total_open_risk_pct: 'Combined risk across every open position at once.',
  max_sector_risk_pct: 'Combined risk inside one sector.',
  max_correlated_risk_pct: 'Combined risk across positions that tend to move together.',
  daily_loss_limit_pct: 'Losing this much in a day pauses new trade recommendations.',
  weekly_loss_limit_pct: 'Losing this much in a week pauses new trade recommendations.',
  max_position_pct: 'The largest share of the account any single position may occupy.',
  min_rr: 'Minimum reward-to-risk before a trade is shown as actionable.',
  preferred_rr: 'The ratio the system prefers when structure allows it.',
  slippage_pct: 'Assumed slippage per fill. Never zero by default.',
  commission_pct: 'Assumed commission per side.',
  consecutive_loss_trigger: 'Losses in a row before risk is automatically halved.',
  consecutive_loss_pause: 'Losses in a row before the strategy is flagged for review.',
  drawdown_reduce_25_pct: 'Account drawdown that trims risk by 25%.',
  drawdown_reduce_50_pct: 'Account drawdown that halves risk.',
  drawdown_pause_pct: 'Account drawdown that pauses live recommendations. Paper testing continues.',
  leverage: 'Leverage changes capital required, never the planned dollar risk.',
};

const GROUPS: [string, string[]][] = [
  ['Account', ['account_equity', 'leverage', 'max_position_pct']],
  ['Risk per trade', ['default_risk_pct', 'max_risk_pct', 'min_rr', 'preferred_rr']],
  ['Portfolio ceilings', ['max_total_open_risk_pct', 'max_correlated_risk_pct', 'max_sector_risk_pct']],
  ['Circuit breakers', ['daily_loss_limit_pct', 'weekly_loss_limit_pct',
    'consecutive_loss_trigger', 'consecutive_loss_pause',
    'drawdown_reduce_25_pct', 'drawdown_reduce_50_pct', 'drawdown_pause_pct']],
  ['Costs', ['slippage_pct', 'commission_pct']],
];

export default function RiskPage() {
  const [cfg, , reload] = usePolling<any>('/api/risk/settings', 60000);
  const [pf] = usePolling<any>('/api/risk/portfolio', 20000);
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [calc, setCalc] = useState({ entry: '', stop: '', risk_pct: '', account_equity: '', direction: 'long' });
  const [calcOut, setCalcOut] = useState<any>(null);

  useEffect(() => { if (cfg?.settings && !Object.keys(draft).length) setDraft(cfg.settings); },
    [cfg?.settings]);

  const s = { ...(cfg?.settings || {}), ...draft };
  const overRisk = Number(s.default_risk_pct) > 2;

  async function save() {
    try {
      const payload: Record<string, any> = {};
      for (const [k, v] of Object.entries(draft)) {
        if (typeof (cfg?.defaults || {})[k] === 'number') payload[k] = Number(v);
        else payload[k] = v;
      }
      const r: any = await apiPut('/api/risk/settings', payload);
      setMsg(r.warning || 'Saved.');
      reload();
    } catch (e: any) { setMsg(e.message); }
  }

  async function runCalc() {
    try {
      const body: any = { entry: Number(calc.entry), stop: Number(calc.stop), direction: calc.direction };
      if (calc.risk_pct) body.risk_pct = Number(calc.risk_pct);
      if (calc.account_equity) body.account_equity = Number(calc.account_equity);
      setCalcOut(await apiPostBody('/api/risk/calculator', body));
    } catch (e: any) { setCalcOut({ error: e.message }); }
  }

  const openPct = pf?.portfolio?.total_open_risk_pct ?? 0;
  const ceiling = pf?.limits?.max_total_open_risk_pct ?? 3;

  return (
    <main className="wrap">
      <header className="page-head">
        <h1>Risk settings</h1>
        <p>
          Position size is always calculated from the distance between your entry and
          your stop, never from a flat percentage of the account. A wider stop
          therefore gives a smaller position for the same planned loss.
        </p>
      </header>

      <div className="sect"><h2>Portfolio risk right now</h2>
        <span className="meta">what is exposed if every open stop fills at its stop</span></div>
      <div className="cards">
        <div className="card">
          <h3>Open risk</h3>
          <div className="big" style={{ color: openPct > ceiling ? 'var(--risk)' : undefined }}>
            {openPct.toFixed(2)}%
          </div>
          <div className="sub">of {ceiling}% ceiling</div>
          <div className={`meter ${openPct > ceiling ? 'over' : ''}`} style={{ marginTop: 8 }}>
            <i style={{ width: `${Math.min(100, (openPct / ceiling) * 100)}%` }} />
          </div>
        </div>
        <div className="card"><h3>Open positions</h3>
          <div className="big">{pf?.portfolio?.open_positions ?? 0}</div>
          <div className="sub">{money(pf?.portfolio?.total_open_risk)} at risk</div></div>
        <div className="card"><h3>Headroom</h3>
          <div className="big">{(pf?.headroom_pct ?? 0).toFixed(2)}%</div>
          <div className="sub">available for new trades</div></div>
        <div className="card"><h3>Circuit breaker</h3>
          <div className={`big ${pf?.circuit_breaker?.paused ? 'neg' : 'pos'}`}>
            {pf?.circuit_breaker?.paused ? 'PAUSED' : 'CLEAR'}</div>
          <div className="sub">paper recording always continues</div></div>
      </div>

      {(pf?.circuit_breaker?.blocks?.length || pf?.circuit_breaker?.warnings?.length) ? (
        <div className="panel">
          <h3>Active protections</h3>
          {pf.circuit_breaker.blocks?.map((b: string, i: number) => (
            <div className="rm-check warn" key={`b${i}`}><span aria-hidden>!</span> {b}</div>))}
          {pf.circuit_breaker.warnings?.map((w: string, i: number) => (
            <div className="rm-check warn" key={`w${i}`}><span aria-hidden>!</span> {w}</div>))}
        </div>
      ) : null}

      {pf?.positions?.length ? (
        <div className="panel">
          <h3>Where the risk sits</h3>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>Symbol</th><th>Strategy</th><th>Correlation group</th><th>Open risk</th></tr></thead>
              <tbody>{pf.positions.map((p: any, i: number) => (
                <tr key={i}><td className="sym">{p.symbol}</td><td>{p.profile}</td>
                  <td>{p.correlation_group?.replace(/_/g, ' ') || '—'}</td>
                  <td className="mono">{money(p.open_risk_dollars)}</td></tr>))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="sect"><h2>Position size calculator</h2>
        <span className="meta">changes nothing — a what-if tool</span></div>
      <div className="panel">
        <div className="form-grid">
          <label className="field"><span>Entry price</span>
            <input value={calc.entry} onChange={(e) => setCalc({ ...calc, entry: e.target.value })}
              inputMode="decimal" placeholder="50.00" /></label>
          <label className="field"><span>Stop price</span>
            <input value={calc.stop} onChange={(e) => setCalc({ ...calc, stop: e.target.value })}
              inputMode="decimal" placeholder="49.00" /></label>
          <label className="field"><span>Risk % (blank = your default)</span>
            <input value={calc.risk_pct} onChange={(e) => setCalc({ ...calc, risk_pct: e.target.value })}
              inputMode="decimal" placeholder={String(s.default_risk_pct ?? 1)} /></label>
          <label className="field"><span>Account (blank = your setting)</span>
            <input value={calc.account_equity} onChange={(e) => setCalc({ ...calc, account_equity: e.target.value })}
              inputMode="decimal" placeholder={String(s.account_equity ?? 10000)} /></label>
          <label className="field"><span>Direction</span>
            <select value={calc.direction} onChange={(e) => setCalc({ ...calc, direction: e.target.value })}>
              <option value="long">Long</option><option value="short">Short</option></select></label>
        </div>
        <div className="btn-row"><button className="btn" onClick={runCalc}>Calculate</button></div>
        {calcOut && (
          <div style={{ marginTop: 12 }}>
            {calcOut.error || calcOut.sizing?.valid === false ? (
              <div className="rm-check warn"><span aria-hidden>!</span>
                {calcOut.error || calcOut.sizing?.reason}</div>
            ) : (
              <>
                <div className="rm-kv"><span>Maximum loss</span>
                  <b className="neg">{money(calcOut.sizing.planned_loss)}</b></div>
                <div className="rm-kv"><span>Position size</span>
                  <b>{calcOut.sizing.quantity.toLocaleString()}</b></div>
                <div className="rm-kv"><span>Position value</span>
                  <b>{money(calcOut.sizing.position_notional)}</b></div>
                <div className="rm-kv"><span>Worst case with costs</span>
                  <b className="neg">{money(calcOut.sizing.worst_case_with_costs)}</b></div>
                <div className="rm-kv"><span>Limited by</span>
                  <b>{calcOut.sizing.binding_constraint.replace(/_/g, ' ')}</b></div>
                <p className="rm-note">{calcOut.explanation}</p>
              </>
            )}
          </div>
        )}
      </div>

      <div className="sect"><h2>Settings</h2>
        <span className="meta">every value is configurable; risk above 2% per trade is discouraged</span></div>
      {overRisk && (
        <div className="safety"><span aria-hidden style={{ fontSize: 16 }}>⚠</span>
          <div><b>{s.default_risk_pct}% per trade is aggressive.</b> A run of six losing
            trades at this size costs roughly {(Number(s.default_risk_pct) * 6).toFixed(0)}% of
            the account. Most professional risk models sit at or below 1%.</div></div>
      )}
      <div className="panel">
        <h3>Quick risk presets</h3>
        <div className="row" style={{ gap: 7 }}>
          {RISK_OPTIONS.map((r) => (
            <button key={r} className={`tab ${Number(s.default_risk_pct) === r ? 'on' : ''}`}
              onClick={() => setDraft({ ...draft, default_risk_pct: r })}>
              {r.toFixed(2)}%
              {s.account_equity ? <small style={{ opacity: 0.7 }}>
                {' '}({money(Number(s.account_equity) * r / 100, 0)})</small> : null}
            </button>
          ))}
        </div>
      </div>
      {GROUPS.map(([title, keys]) => (
        <div className="panel" key={title}>
          <h3>{title}</h3>
          <div className="form-grid">
            {keys.map((k) => (
              <label className="field" key={k}>
                <Tip term={FIELD_HELP[k] || k}><span>{k.replace(/_/g, ' ')} ⓘ</span></Tip>
                <input value={s[k] ?? ''} inputMode="decimal"
                  onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className="btn-row">
        <button className="btn" onClick={save}>Save risk settings</button>
        {msg && <span className="save-note">{msg}</span>}
      </div>
    </main>
  );
}
