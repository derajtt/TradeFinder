'use client';
import { useEffect, useMemo, useState } from 'react';
import { DataTable, Details, EmptyState, EvidenceTag, SectionHeader, StatTile, StatusPill, type Column } from '../../components/ui';
import { apiPostBody, apiPut, usePollingState } from '../../lib/api';
import { fmtNum, money } from '../../lib/format';
import { useMode } from '../../lib/mode';
import { humanKey } from '../../lib/vocab';
import c from '../controls.module.css';

/* Spec §3.8 — Risk & position size. Calculator first in Simple; ceilings, breakers
   and costs sit behind a Details disclosure. */

interface RiskSettingsPayload {
  settings: Record<string, unknown>; defaults: Record<string, unknown>;
  risk_pct_options?: number[]; warn_above_pct?: number; note?: string;
}
interface RiskPosition { symbol: string; direction: string; profile: string; open_risk_dollars: number; correlation_group: string | null }
interface RiskPortfolio {
  portfolio: { open_positions: number; total_open_risk: number; total_open_risk_pct: number };
  positions: RiskPosition[];
  limits: { max_total_open_risk_pct: number; max_correlated_risk_pct: number; max_sector_risk_pct: number;
    daily_loss_limit_pct: number; weekly_loss_limit_pct: number };
  circuit_breaker: { paused: boolean; blocks: string[]; warnings: string[] };
  headroom_pct: number;
}
interface Sizing {
  valid: boolean; reason?: string; quantity?: number; planned_loss?: number; position_notional?: number;
  worst_case_with_costs?: number; binding_constraint?: string; uncapped_quantity?: number;
}
interface CalcOut { sizing?: Sizing; targets?: { price: number; r: number }[]; explanation?: string; error?: string }

const RISK_OPTIONS_FALLBACK = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0];
const WARN_ABOVE_FALLBACK = 2.0;

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
  drawdown_pause_pct: 'Account drawdown that pauses recommendations. Paper testing continues.',
  leverage: 'Leverage changes capital required, never the planned dollar risk.',
};

const BASIC_GROUPS: [string, string[]][] = [
  ['Account', ['account_equity', 'leverage', 'max_position_pct']],
  ['Risk per trade', ['default_risk_pct', 'max_risk_pct', 'min_rr', 'preferred_rr']],
];
const DEEP_GROUPS: [string, string[]][] = [
  ['Portfolio ceilings', ['max_total_open_risk_pct', 'max_correlated_risk_pct', 'max_sector_risk_pct']],
  ['Circuit breakers', ['daily_loss_limit_pct', 'weekly_loss_limit_pct',
    'consecutive_loss_trigger', 'consecutive_loss_pause',
    'drawdown_reduce_25_pct', 'drawdown_reduce_50_pct', 'drawdown_pause_pct']],
  ['Costs', ['slippage_pct', 'commission_pct']],
];

const SOURCE = 'Paper positions · all strategies';
/** Labels humanKey cannot produce on its own (abbreviations). */
const FIELD_LABEL: Record<string, string> = {
  min_rr: 'Min reward-to-risk (R)', preferred_rr: 'Preferred reward-to-risk (R)',
  max_position_pct: 'Max position (% of account)', account_equity: 'Account equity ($)',
};
const fieldLabelOf = (k: string) => FIELD_LABEL[k] ?? humanKey(k);
const str = (v: unknown): string => (v === null || v === undefined ? '' : String(v));

export default function RiskPage() {
  const { advanced } = useMode();
  const { data: cfg, reload } = usePollingState<RiskSettingsPayload>('/api/risk/settings', 60000);
  const { data: pf, loaded: pfLoaded } = usePollingState<RiskPortfolio>('/api/risk/portfolio', 20000);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [calc, setCalc] = useState({ entry: '', stop: '', risk_pct: '', account_equity: '', direction: 'long' });
  const [calcOut, setCalcOut] = useState<CalcOut | null>(null);

  useEffect(() => {
    if (cfg?.settings && !Object.keys(draft).length) setDraft(cfg.settings);
  }, [cfg?.settings]); // eslint-disable-line react-hooks/exhaustive-deps

  const s = useMemo(() => ({ ...(cfg?.settings ?? {}), ...draft }), [cfg?.settings, draft]);
  const riskOptions = cfg?.risk_pct_options ?? RISK_OPTIONS_FALLBACK;
  const warnAbove = cfg?.warn_above_pct ?? WARN_ABOVE_FALLBACK;
  const riskPct = Number(s.default_risk_pct);
  const overRisk = Number.isFinite(riskPct) && riskPct > warnAbove;

  async function save() {
    try {
      const payload: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(draft)) {
        payload[k] = typeof (cfg?.defaults ?? {})[k] === 'number' ? Number(v) : v;
      }
      const r = await apiPut<{ ok: boolean; warning?: string | null }>('/api/risk/settings', payload);
      setMsg(r.warning || 'Saved.');
      reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); }
  }

  async function runCalc() {
    try {
      const body: Record<string, unknown> = { entry: Number(calc.entry), stop: Number(calc.stop), direction: calc.direction };
      if (calc.risk_pct) body.risk_pct = Number(calc.risk_pct);
      if (calc.account_equity) body.account_equity = Number(calc.account_equity);
      setCalcOut(await apiPostBody<CalcOut>('/api/risk/calculator', body));
    } catch (e) { setCalcOut({ error: e instanceof Error ? e.message : String(e) }); }
  }

  const tilesLoaded = pfLoaded && !!pf;
  const openPct = pf?.portfolio?.total_open_risk_pct ?? 0;
  const ceiling = pf?.limits?.max_total_open_risk_pct ?? null;
  const openN = pf?.portfolio?.open_positions;
  const paused = !!pf?.circuit_breaker?.paused;
  const blocks = pf?.circuit_breaker?.blocks ?? [];
  const warnings = pf?.circuit_breaker?.warnings ?? [];
  const overCeiling = ceiling !== null && openPct > ceiling;

  const posCols: Column<RiskPosition>[] = [
    { key: 'symbol', header: 'Stock', align: 'l', simple: true, sortValue: (r) => r.symbol, cell: (r) => <span className="sym">{r.symbol}</span> },
    { key: 'profile', header: 'Strategy', align: 'l', simple: true, sortValue: (r) => r.profile, cell: (r) => humanKey(r.profile) },
    { key: 'group', header: 'Moves with', align: 'l', simple: true, sortValue: (r) => r.correlation_group,
      cell: (r) => (r.correlation_group ? humanKey(r.correlation_group) : '—') },
    { key: 'risk', header: 'Open risk', simple: true, sortValue: (r) => r.open_risk_dollars, cell: (r) => money(r.open_risk_dollars) },
  ];

  /* ── sections ─────────────────────────────────────────────────────────── */

  const exposure = (
    <>
      <SectionHeader title="Paper portfolio risk right now" id="exposure"
        question="If every open stop filled, how much would the paper account lose?"
        caption={SOURCE} evidence="PAPER" />
      <div className="stat-grid">
        <StatTile label="Open risk" evidence="PAPER" source={SOURCE} loaded={tilesLoaded}
          n={openN} unit="open paper trades" tone={overCeiling ? 'risk' : undefined}
          nLabel={openN === 0 ? `No open paper trades · nothing at risk` : undefined}
          value={ceiling !== null ? `${fmtNum(openPct)}% of ${fmtNum(ceiling, 1)}% ceiling` : `${fmtNum(openPct)}%`}
          sub={ceiling !== null ? (
            <div className={`meter${overCeiling ? ' over' : ''}`} aria-hidden>
              <i style={{ width: `${Math.min(100, (openPct / ceiling) * 100)}%` }} />
            </div>
          ) : undefined} />
        <StatTile label="Open paper trades" evidence="PAPER" source={SOURCE} loaded={tilesLoaded}
          n={openN} unit="open paper trades" value={openN != null ? fmtNum(openN, 0) : null}
          nLabel={`${money(pf?.portfolio?.total_open_risk)} at risk if every stop fills`} />
        <StatTile label="Room for new trades" evidence="PAPER" source={SOURCE} loaded={tilesLoaded}
          n={openN} unit="open paper trades" tone={pf && pf.headroom_pct < 0 ? 'risk' : undefined}
          nLabel={openN === 0 && ceiling !== null ? `No open paper trades · the full ${fmtNum(ceiling, 1)}% ceiling is available` : undefined}
          value={pf ? `${fmtNum(pf.headroom_pct)}%` : null}
          sub={pf && pf.headroom_pct < 0
            ? `Already ${fmtNum(Math.abs(pf.headroom_pct))} points over the ${fmtNum(ceiling ?? 0, 1)}% ceiling — no room for new paper trades`
            : ceiling !== null ? `of the ${fmtNum(ceiling, 1)}% total open-risk ceiling` : undefined} />
        {/* Not a number, so a status card rather than a StatTile (spec: "Circuit breaker as StatusPill"). */}
        <div className="stat" aria-busy={!tilesLoaded}>
          <EvidenceTag evidence="PAPER" />
          <div className="stat-label">Circuit breaker</div>
          <div className="stat-value">
            {!tilesLoaded ? <span className="skel" style={{ display: 'inline-block', width: 90 }}>&nbsp;</span>
              : <StatusPill label={paused ? 'Paused' : 'Clear'} tone={paused ? 'risk' : 'buy'} />}
          </div>
          <div className="stat-n">
            {tilesLoaded ? (blocks.length || warnings.length
              ? `${blocks.length} block${blocks.length === 1 ? '' : 's'} · ${warnings.length} warning${warnings.length === 1 ? '' : 's'}`
              : 'No blocks or warnings') : ''}
          </div>
          <div className="stat-src">{SOURCE} · paper recording always continues</div>
        </div>
      </div>

      {pfLoaded && (blocks.length || warnings.length) ? (
        <div className="panel">
          <h3>Active protections</h3>
          {blocks.map((b, i) => <div className="rm-check warn" key={`b${i}`}><span aria-hidden>!</span> {b}</div>)}
          {warnings.map((w, i) => <div className="rm-check warn" key={`w${i}`}><span aria-hidden>!</span> {w}</div>)}
        </div>
      ) : null}

      {pfLoaded && pf?.positions?.length ? (
        <>
          <SectionHeader title="Where the risk sits" question="Which open paper trades carry the risk?" caption={SOURCE} evidence="PAPER" />
          <DataTable<RiskPosition> rows={pf.positions} columns={posCols} rowKey={(r) => `${r.profile}:${r.symbol}`}
            defaultSort={{ key: 'risk', dir: 'desc' }} minWidth={520} dense />
        </>
      ) : null}
    </>
  );

  const calculator = (
    <>
      <SectionHeader title="Position size calculator" id="calculator"
        question="How many shares can I buy so that a stop-out loses only what I planned?"
        caption="A what-if tool — it changes nothing and places no order" />
      <div className="panel">
        <div className="form-grid">
          <div className="field">
            <label htmlFor="calc-entry">Entry price</label>
            <input id="calc-entry" value={calc.entry} inputMode="decimal" placeholder="50.00"
              onChange={(e) => setCalc({ ...calc, entry: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="calc-stop">Stop price</label>
            <input id="calc-stop" value={calc.stop} inputMode="decimal" placeholder="49.00"
              onChange={(e) => setCalc({ ...calc, stop: e.target.value })} />
            <span className="hint">Where you admit the trade is wrong. Size comes from the entry-to-stop distance.</span>
          </div>
          <div className="field">
            <label htmlFor="calc-risk">Risk per trade (%)</label>
            <input id="calc-risk" value={calc.risk_pct} inputMode="decimal" placeholder={str(s.default_risk_pct) || '1'}
              onChange={(e) => setCalc({ ...calc, risk_pct: e.target.value })} />
            <span className="hint">Blank = your default ({str(s.default_risk_pct) || '—'}%).</span>
          </div>
          <div className="field">
            <label htmlFor="calc-equity">Account ($)</label>
            <input id="calc-equity" value={calc.account_equity} inputMode="decimal" placeholder={str(s.account_equity) || '10000'}
              onChange={(e) => setCalc({ ...calc, account_equity: e.target.value })} />
            <span className="hint">Blank = your setting ({s.account_equity != null ? money(s.account_equity, 0) : '—'}).</span>
          </div>
          <div className="field">
            <label htmlFor="calc-dir">Direction</label>
            <select id="calc-dir" value={calc.direction} onChange={(e) => setCalc({ ...calc, direction: e.target.value })}>
              <option value="long">Long</option><option value="short">Short</option>
            </select>
          </div>
        </div>
        <div className="btn-row"><button type="button" className="btn primary" onClick={runCalc}>Calculate</button></div>
        {calcOut && (
          <div style={{ marginTop: 12 }}>
            {calcOut.error || !calcOut.sizing || calcOut.sizing.valid === false ? (
              <div className="rm-check warn"><span aria-hidden>!</span>
                {calcOut.error || calcOut.sizing?.reason || 'The calculator could not size this trade.'}</div>
            ) : (
              <>
                <div className="rm-kv"><span>Maximum loss</span><b className="neg">{money(calcOut.sizing.planned_loss)}</b></div>
                <div className="rm-kv"><span>Position size</span>
                  <b>{calcOut.sizing.quantity != null ? `${calcOut.sizing.quantity.toLocaleString()} shares` : '—'}</b></div>
                <div className="rm-kv"><span>Position value</span><b>{money(calcOut.sizing.position_notional)}</b></div>
                <div className="rm-kv"><span>Worst case with costs</span><b className="neg">{money(calcOut.sizing.worst_case_with_costs)}</b></div>
                <div className="rm-kv"><span>Limited by</span><b>{humanKey(calcOut.sizing.binding_constraint)}</b></div>
                {calcOut.explanation ? <p className="rm-note">{calcOut.explanation}</p> : null}
              </>
            )}
          </div>
        )}
      </div>
    </>
  );

  const renderGroup = ([title, keys]: [string, string[]]) => (
    <div className="panel" key={title}>
      <h3>{title}</h3>
      <div className="form-grid">
        {keys.map((k) => (
          <div className="field" key={k}>
            <label htmlFor={`rs-${k}`}>{fieldLabelOf(k)}</label>
            <input id={`rs-${k}`} value={str(s[k])} inputMode="decimal"
              onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
            {FIELD_HELP[k] ? <span className="hint">{FIELD_HELP[k]}</span> : null}
          </div>
        ))}
      </div>
    </div>
  );

  const settings = (
    <>
      <SectionHeader title="Risk settings" id="settings"
        question="How much may one trade lose, and where does the system stop me?"
        caption={cfg?.note ?? `Every value is configurable; risk above ${warnAbove}% per trade is discouraged`} />
      {overRisk && (
        <div className="safety"><span aria-hidden style={{ fontSize: 16 }}>⚠</span>
          <div><b>{fmtNum(riskPct)}% per trade is aggressive.</b> A run of six losing trades at this size
            costs roughly {(riskPct * 6).toFixed(0)}% of the account. Most professional risk models sit at or below 1%.</div></div>
      )}
      {!cfg ? <div className="skel" style={{ height: 220 }} /> : (
        <>
          <div className="panel">
            <h3>Quick risk presets</h3>
            <div className={c.seg} role="group" aria-label="Risk per trade presets">
              {riskOptions.map((r) => (
                <button key={r} type="button" className={`tab${riskPct === r ? ' on' : ''}`} aria-pressed={riskPct === r}
                  onClick={() => setDraft({ ...draft, default_risk_pct: r })}>
                  {r.toFixed(2)}%
                  {s.account_equity ? <span className="faint"> ({money(Number(s.account_equity) * r / 100, 0)})</span> : null}
                </button>
              ))}
            </div>
            <div className={c.hint} style={{ marginTop: 8 }}>The dollar figure is what one losing trade costs at your account size.</div>
          </div>
          {BASIC_GROUPS.map(renderGroup)}
          {advanced ? DEEP_GROUPS.map(renderGroup) : (
            <Details summary="Show portfolio ceilings, circuit breakers and costs">
              {DEEP_GROUPS.map(renderGroup)}
            </Details>
          )}
          <div className="btn-row">
            <button type="button" className="btn" onClick={save}>Save risk settings</button>
            {msg && <span className="save-note" role="status">{msg}</span>}
          </div>
        </>
      )}
    </>
  );

  return (
    <>
      <SectionHeader level={1} title="Risk & position size"
        question="How much could I lose right now, and how big should a position be?" />
      <p className={c.lead}>
        Position size is always calculated from the distance between your entry and your stop,
        never from a flat percentage of the account. A wider stop therefore gives a smaller
        position for the same planned loss. Everything here is paper — simulated money.
      </p>
      {advanced ? (<>{exposure}{calculator}{settings}</>) : (<>{calculator}{exposure}{settings}</>)}
      {pfLoaded && !pf ? (
        <EmptyState headline="Portfolio risk unavailable" reason="The risk service did not answer; the calculator still works." tone="warn" />
      ) : null}
    </>
  );
}
