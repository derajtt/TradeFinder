'use client';
import { useState } from 'react';

/** A signal is only useful if it answers, in order: what, why, where in, how
 *  much, where out, and what to do next. This card is that answer. Simple mode
 *  is the default; Advanced never removes anything, it only adds. */

export interface Roadmap {
  headline?: string; action?: string; direction?: string; stage?: string;
  strategy?: string;
  now?: { action: string; tone: string; title: string; detail: string };
  steps?: string[];
  checklist?: { ok: boolean; label: string }[];
  why?: string[];
  numbers?: any;
  no_trade_reason?: string;
  invalidation?: string; expires_at?: string;
}

const TONE: Record<string, string> = {
  positive: 'tone-pos', negative: 'tone-neg', amber: 'tone-amber',
  neutral: 'tone-neutral',
};
const ACTION_ICON: Record<string, string> = {
  ENTER: '▲', WAIT: '⏸', HOLD: '●', EXIT: '■', AVOID: '⚠', CLOSED: '✓',
};

export function money(v: any, dp = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return '$' + n.toLocaleString(undefined,
    { minimumFractionDigits: dp, maximumFractionDigits: n < 1 ? 6 : dp });
}

/** Small info bubble so a term never goes unexplained without adding clutter. */
export function Tip({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <span className="tipwrap" tabIndex={0}>
      {children}
      <span className="tip" role="tooltip">{term}</span>
    </span>
  );
}

export default function TradeRoadmap({
  rm, symbol, timeframe, score, scoreBand, status, indicators, plan,
  parameters, events, compact,
}: {
  rm: Roadmap; symbol: string; timeframe?: string; score?: number;
  scoreBand?: string; status?: string; indicators?: any; plan?: any;
  parameters?: any; events?: any[]; compact?: boolean;
}) {
  const [adv, setAdv] = useState(false);
  const now = rm?.now;
  const n = rm?.numbers;
  const noTrade = !n;
  const dir = rm?.direction === 'short' ? 'SHORT' : 'LONG';
  const isBuy = rm?.action === 'BUY';

  return (
    <article className={`roadmap ${noTrade ? 'is-notrade' : isBuy ? 'is-buy' : 'is-sell'}`}>
      <header className="rm-head">
        <div>
          <div className="rm-sym">{symbol}
            {timeframe && <span className="rm-tf">{timeframe.replace('min', ' MIN').replace('hour', ' HOUR')}</span>}
          </div>
          <div className="rm-strat">{rm?.strategy || 'Strategy'}</div>
        </div>
        <div className="rm-act">
          {noTrade ? (
            <span className="badge warn">⚠ NO TRADE</span>
          ) : (
            <span className={`badge ${isBuy ? 'buy' : 'risk'}`}>
              {isBuy ? '▲' : '▼'} {rm?.action} — {status || 'READY'}
            </span>
          )}
        </div>
      </header>

      {score !== undefined && (
        <div className="rm-score">
          <div className="rm-score-num">{Math.round(score)}<small> / 100</small></div>
          <div>
            <div className="rm-score-band">{scoreBand}</div>
            <Tip term="Setup Quality measures how closely this setup matches the strategy's own conditions. It is not a probability of profit.">
              <span className="rm-score-lbl">Setup Quality ⓘ</span>
            </Tip>
          </div>
          <div className="rm-score-bar"><i style={{ width: `${Math.min(100, score)}%` }} /></div>
        </div>
      )}

      {now && (
        <section className={`rm-now ${TONE[now.tone] || 'tone-neutral'}`}>
          <div className="rm-now-lbl">What to do right now</div>
          <div className="rm-now-act">
            <span aria-hidden>{ACTION_ICON[now.action?.split(' ')[0]] || '•'}</span> {now.action}
          </div>
          <div className="rm-now-title">{now.title}</div>
          <div className="rm-now-detail">{now.detail}</div>
        </section>
      )}

      {n && (
        <>
          <section className="rm-levels">
            <div className="rm-lv">
              <span>Entry</span>
              <b>{money(n.entry)}</b>
              {n.entry_zone?.ideal && (
                <small>zone {money(n.entry_zone.ideal[0])}–{money(n.entry_zone.ideal[1])}</small>
              )}
            </div>
            <div className="rm-lv neg">
              <Tip term="If the trade moves against you, this is the planned exit that keeps the loss controlled.">
                <span>Stop loss ⓘ</span>
              </Tip>
              <b>{money(n.stop)}</b>
              <small>risking {money(n.max_loss)}</small>
            </div>
            {n.entry_zone?.no_chase !== undefined && (
              <div className="rm-lv amber">
                <Tip term="Past this price the move is already extended and the reward no longer justifies the risk.">
                  <span>Do not chase ⓘ</span>
                </Tip>
                <b>{money(n.entry_zone.no_chase)}</b>
              </div>
            )}
          </section>

          <section className="rm-targets">
            <div className="rm-sec-lbl">Take profit</div>
            {(n.targets || []).map((t: any) => (
              <div className="rm-tgt" key={t.name}>
                <span className="rm-tgt-name">{t.name}</span>
                <b>{money(t.price)}</b>
                <Tip term={`You are aiming for about ${t.r} times what you are risking on this portion.`}>
                  <span className="rm-tgt-r">{t.r}R ⓘ</span>
                </Tip>
                <span className="rm-tgt-alloc">{t.allocation_pct}% of position</span>
                <span className="rm-tgt-prof">{money(t.profit_at_target)}</span>
              </div>
            ))}
          </section>

          <section className="rm-risk">
            <div className="rm-sec-lbl">Your risk</div>
            <div className="rm-kv"><span>Account</span><b>{money(n.account, 0)}</b></div>
            <div className="rm-kv"><span>Risk per trade</span><b>{n.risk_pct?.toFixed(2)}%</b></div>
            <div className="rm-kv"><span>Maximum planned loss</span><b className="neg">{money(n.max_loss)}</b></div>
            <div className="rm-kv">
              <Tip term="A stop order reduces risk but can fill worse than the stop price in fast markets. This figure includes estimated spread, slippage and fees.">
                <span>Worst case with costs ⓘ</span>
              </Tip>
              <b className="neg">{money(n.worst_case_with_costs)}</b>
            </div>
            <div className="rm-kv"><span>Position size</span><b>{n.quantity_text}</b></div>
            <div className="rm-kv"><span>Position value</span><b>{money(n.position_value)}</b></div>
            <div className="rm-kv">
              <Tip term="Reward compared with risk. 2R means you are aiming to make twice what you are risking.">
                <span>Reward / risk ⓘ</span>
              </Tip>
              <b className={n.rr?.meets_preferred ? 'pos' : ''}>1 : {n.rr?.best}</b>
            </div>
          </section>
        </>
      )}

      {rm?.checklist?.length ? (
        <section className="rm-checks">
          <div className="rm-sec-lbl">Why this trade</div>
          {rm.checklist.map((c, i) => (
            <div key={i} className={`rm-check ${c.ok ? 'ok' : 'warn'}`}>
              <span aria-hidden>{c.ok ? '✓' : '!'}</span> {c.label}
            </div>
          ))}
        </section>
      ) : null}

      {rm?.steps?.length ? (
        <section className="rm-steps">
          <div className="rm-sec-lbl">Your plan, step by step</div>
          <ol>{rm.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </section>
      ) : null}

      {rm?.no_trade_reason && (
        <section className="rm-notrade">
          <div className="rm-sec-lbl">Why the system passed</div>
          <p>{rm.no_trade_reason}</p>
        </section>
      )}

      <button className="rm-adv-btn" onClick={() => setAdv((a) => !a)} aria-expanded={adv}>
        {adv ? '▾ Hide advanced details' : '▸ View advanced details'}
      </button>

      {adv && (
        <section className="rm-adv">
          {rm?.why?.length ? (
            <>
              <div className="rm-sec-lbl">Signal explanation</div>
              <ul className="rm-why">{rm.why.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </>
          ) : null}
          {indicators && (
            <>
              <div className="rm-sec-lbl">Indicators at signal</div>
              <div className="rm-grid">
                {([
                  ['RSI', indicators.rsi?.toFixed?.(1)],
                  ['ADX', indicators.adx?.toFixed?.(1)],
                  ['ATR', indicators.atr?.toFixed?.(4)],
                  ['BB basis', indicators.bb_basis?.toFixed?.(2)],
                  ['BB upper', indicators.bb_upper?.toFixed?.(2)],
                  ['BB lower', indicators.bb_lower?.toFixed?.(2)],
                  ['BB width', indicators.bb_width?.toFixed?.(4)],
                  ['BB width %ile', indicators.bb_width_pctile?.toFixed?.(0)],
                  ['Rel. volume', indicators.rvol?.toFixed?.(2)],
                  ['VWAP', indicators.vwap?.toFixed?.(2)],
                  ['EMA 20', indicators.ema20?.toFixed?.(2)],
                  ['EMA 200', indicators.ema200?.toFixed?.(2)],
                  ['Regime', indicators.regime],
                  ['Higher TF trend', indicators.htf_trend],
                ] as [string, any][]).filter(([, v]) => v !== undefined && v !== null)
                  .map(([k, v]) => (
                    <div className="rm-kv sm" key={k}><span>{k}</span><b>{String(v)}</b></div>
                  ))}
              </div>
            </>
          )}
          {plan?.risk?.reduction_reasons?.length ? (
            <>
              <div className="rm-sec-lbl">Risk adjustments applied</div>
              <ul className="rm-why">
                {plan.risk.reduction_reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}
              </ul>
            </>
          ) : null}
          {plan?.expected_value && (
            <>
              <div className="rm-sec-lbl">Historical context</div>
              <div className="rm-kv sm"><span>Basis</span><b>{plan.expected_value.basis}</b></div>
              <div className="rm-kv sm"><span>Sample</span><b>{plan.expected_value.sample} ({plan.expected_value.sample_label})</b></div>
              <div className="rm-kv sm"><span>Observed win rate</span><b>{plan.expected_value.observed_win_rate_pct}%</b></div>
              <p className="rm-note">{plan.expected_value.note}</p>
            </>
          )}
          {parameters && (
            <>
              <div className="rm-sec-lbl">Strategy parameters</div>
              <div className="rm-grid">
                {Object.entries(parameters)
                  .filter(([k]) => !['variant', 'strategy_version'].includes(k))
                  .slice(0, 18)
                  .map(([k, v]) => (
                    <div className="rm-kv sm" key={k}>
                      <span>{k.replace(/_/g, ' ')}</span><b>{String(v)}</b>
                    </div>
                  ))}
              </div>
            </>
          )}
          {events?.length ? (
            <>
              <div className="rm-sec-lbl">What has happened</div>
              <ul className="rm-events">
                {events.map((e: any, i: number) => (
                  <li key={i}>
                    <time>{String(e.t).slice(0, 19).replace('T', ' ')}</time>
                    <b>{String(e.e).replace(/_/g, ' ')}</b>
                    {e.price !== undefined && <span>@ {money(e.price)}</span>}
                    {e.detail && <span>{e.detail}</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      )}
    </article>
  );
}
