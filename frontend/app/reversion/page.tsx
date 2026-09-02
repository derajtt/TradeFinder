'use client';
import { useState } from 'react';
import { usePolling } from '../../lib/api';
import TradeRoadmap, { money, Tip } from '../../components/TradeRoadmap';

interface Sig {
  signal_uid: string; symbol: string; timeframe: string; direction: string;
  variant: string; score: number; score_band: string; status: string;
  win_loss: string; entry: number; stop: number; targets: any[];
  confirmed_at: string; expires_at: string; regime: string; session: string;
  actionable: boolean; no_trade_reason?: string; roadmap: any; trade_plan: any;
  why: string[]; indicators: any; parameters: any; events: any[];
  asset_class: string; exit?: any;
}

const TABS = ['Live signals', 'Performance', 'Backtest', 'Strategy lab'] as const;

function Stat({ label, value, tip, tone }: { label: string; value: any; tip?: string; tone?: string }) {
  const body = <span className="rm-score-lbl">{label}{tip && ' ⓘ'}</span>;
  return (
    <div className="card">
      <h3>{tip ? <Tip term={tip}>{body}</Tip> : body}</h3>
      <div className="big" style={tone === 'neg' ? { color: 'var(--risk)' } : tone === 'pos' ? { color: 'var(--buy)' } : undefined}>
        {value ?? '—'}
      </div>
    </div>
  );
}

function MetricTable({ title, groups, note }: { title: string; groups: any; note?: string }) {
  const rows = Object.entries(groups || {}).filter(([, m]: any) => (m.resolved ?? m.trades ?? 0) > 0);
  if (!rows.length) return (
    <div className="panel"><h3>{title}</h3>
      <p className="muted" style={{ fontSize: 12.5 }}>No resolved trades in this breakdown yet.</p></div>
  );
  return (
    <div className="panel">
      <h3>{title}</h3>
      {note && <p className="muted" style={{ fontSize: 11.5, marginTop: -4 }}>{note}</p>}
      <table className="tbl">
        <thead><tr>
          <th>Group</th><th>Trades</th><th>Win rate</th>
          <th>Expectancy (R)</th><th>Profit factor</th><th>Sample</th>
        </tr></thead>
        <tbody>
          {rows.sort((a: any, b: any) => (b[1].expectancy_r ?? -9) - (a[1].expectancy_r ?? -9))
            .map(([k, m]: any) => (
              <tr key={k}>
                <td>{String(k).replace(/_/g, ' ')}</td>
                <td className="mono">{m.resolved ?? m.trades}</td>
                <td className="mono">{m.win_rate}%</td>
                <td className="mono" style={{ color: (m.expectancy_r ?? 0) > 0 ? 'var(--buy)' : 'var(--risk)' }}>
                  {m.expectancy_r}
                </td>
                <td className="mono">{m.profit_factor ?? '—'}</td>
                <td><span className="badge neutral">{m.sample}</span></td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ReversionPage() {
  const [tab, setTab] = useState<typeof TABS[number]>('Live signals');
  const [filter, setFilter] = useState('live');
  const [sigs] = usePolling<{ signals: Sig[]; variants: any; score_bands: any }>(
    `/api/reversion/signals?status=${filter}&limit=60`, 20000);
  const [perf] = usePolling<any>('/api/reversion/performance', 60000);
  const [cfg] = usePolling<any>('/api/reversion/config', 120000);

  const rows = sigs?.signals ?? [];
  const live = rows.filter((r) => ['CONFIRMED', 'ENTRY_ZONE', 'ACTIVE', 'TP1_HIT', 'TP2_HIT'].includes(r.status));
  const paper = perf?.paper?.overall;
  const bt = perf?.backtest;
  const chosen = bt?.chosen;

  return (
    <main className="wrap">
      <header className="page-head">
        <h1>Extreme Reversion <span className="badge neutral">EXTREME_BB_RSI</span></h1>
        <p className="muted">
          Fades statistically extreme dislocations — but only after price has closed
          back inside the band and momentum has turned. Paper research only; no
          orders are placed.
        </p>
      </header>

      <div className="safety">
        <span aria-hidden style={{ fontSize: 16 }}>⚠</span>
        <div>
          <b>These are research signals, not guaranteed outcomes.</b> Risk management
          limits planned exposure but cannot eliminate losses, slippage, gaps or
          market risk. A stop order can fill worse than the stop price.
        </div>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t}
            className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === 'Live signals' && (
        <>
          <div className="cards">
            <Stat label="Live signals" value={live.length} />
            <Stat label="Recorded (this dataset)" value={paper?.signals ?? 0} />
            <Stat label="Resolved" value={paper?.resolved ?? 0} />
            <Stat label="Paper win rate"
              tip="Observed frequency on resolved paper trades. Not a probability that the next trade wins."
              value={paper?.resolved ? `${paper.win_rate}%` : '—'} />
            <Stat label="Expectancy (R)" tone={(paper?.expectancy_r ?? 0) > 0 ? 'pos' : 'neg'}
              tip="Average result per trade measured in units of risk. This matters more than win rate."
              value={paper?.resolved ? paper.expectancy_r : '—'} />
          </div>

          <div className="row" style={{ gap: 8, marginBottom: 14 }}>
            {['live', 'CLOSED', 'NO_TRADE', 'EXPIRED', ''].map((f) => (
              <button key={f || 'all'} className={`tab ${filter === f ? 'on' : ''}`}
                onClick={() => setFilter(f)}>
                {f === '' ? 'All' : f === 'live' ? 'Live' : f.replace(/_/g, ' ')}
              </button>
            ))}
          </div>

          {!rows.length ? (
            <div className="panel">
              <h3>No signals in this view yet</h3>
              <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.65 }}>
                The strategy requires price to pierce a {cfg?.current?.bb_dev ?? 3}σ Bollinger
                band while RSI is at an extreme, and then close back inside the band with
                RSI turning. That combination is deliberately rare — long quiet stretches
                are expected and are not a fault. The worker records every scan; see
                System Health for its heartbeat.
              </p>
            </div>
          ) : (
            <div className="roadmap-grid">
              {rows.map((s) => (
                <TradeRoadmap key={s.signal_uid} rm={s.roadmap} symbol={s.symbol}
                  timeframe={s.timeframe} score={s.score} scoreBand={s.score_band}
                  status={s.status} indicators={s.indicators} plan={s.trade_plan}
                  parameters={s.parameters} events={s.events} />
              ))}
            </div>
          )}
        </>
      )}

      {tab === 'Performance' && perf && (
        <>
          <div className="panel">
            <h3>Three separate records, never blended</h3>
            <p className="muted" style={{ fontSize: 12.5 }}>{perf.separation_note}</p>
          </div>
          <MetricTable title="By variant" groups={perf.paper.by_variant} />
          <MetricTable title="By asset class" groups={perf.paper.by_asset_class}
            note="Stock and crypto statistics are kept apart — they are different instruments." />
          <MetricTable title="By timeframe" groups={perf.paper.by_timeframe} />
          <MetricTable title="By market regime" groups={perf.paper.by_regime} />
          <MetricTable title="By setup-quality band" groups={perf.paper.by_score_band}
            note="If a higher score does not produce better results, the score is not yet informative and gets reweighted." />
          <MetricTable title="By session" groups={perf.paper.by_session} />
          <MetricTable title="By symbol" groups={perf.paper.by_symbol} />
        </>
      )}

      {tab === 'Backtest' && (
        <>
          {bt?.verdict?.headline && (
            <div className="panel" style={{ borderColor: 'rgba(248,113,113,.4)',
                                            background: 'rgba(248,113,113,.06)' }}>
              <h3 style={{ color: 'var(--risk)' }}>Backtest verdict — {bt.verdict.headline}</h3>
              <p style={{ fontSize: 13, lineHeight: 1.7, color: 'var(--text)' }}>
                {bt.verdict.summary}
              </p>
              <div className="rm-grid" style={{ marginTop: 10 }}>
                <div className="rm-kv sm"><span>Evaluations</span><b>{bt.verdict.configs_evaluated}</b></div>
                <div className="rm-kv sm"><span>Positive in-sample</span>
                  <b style={{ color: 'var(--risk)' }}>{bt.verdict.positive_expectancy_in_sample}</b></div>
                <div className="rm-kv sm"><span>Holdout expectancy</span>
                  <b style={{ color: 'var(--risk)' }}>{bt.verdict.holdout_expectancy_r}R</b></div>
                <div className="rm-kv sm"><span>Holdout profit factor</span>
                  <b style={{ color: 'var(--risk)' }}>{bt.verdict.holdout_profit_factor}</b></div>
              </div>
              <p className="muted" style={{ fontSize: 12.5, marginTop: 10, lineHeight: 1.65 }}>
                {bt.verdict.recommendation}
              </p>
              <ul className="rm-why" style={{ marginTop: 8, paddingLeft: 18 }}>
                {(bt.verdict.caveats || []).map((c: string, i: number) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
          {!bt ? (
            <div className="panel"><h3>No backtest on disk yet</h3>
              <p className="muted">Run the study script to populate this tab.</p></div>
          ) : bt.verdict && !bt.chosen ? (
            <div className="panel">
              <h3>Backtest verdict: {String(bt.verdict).replace(/_/g, ' ')}</h3>
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.65 }}>{bt.reason}</p>
              <p className="muted" style={{ fontSize: 12.5 }}>
                Nothing is promoted on an insufficient or unvalidated sample. This is
                reported exactly as measured.
              </p>
            </div>
          ) : (
            <>
              <div className="panel">
                <h3>Protocol</h3>
                <div className="rm-grid">
                  {Object.entries(bt.protocol || {}).map(([k, v]) => (
                    <div className="rm-kv sm" key={k}>
                      <span>{k.replace(/_/g, ' ')}</span><b>{String(v)}</b></div>
                  ))}
                </div>
              </div>
              <div className="cards">
                <Stat label="Train trades" value={chosen?.train?.trades} />
                <Stat label="Validation trades" value={chosen?.validation?.trades} />
                <Stat label="Holdout trades" value={chosen?.test?.trades} />
                <Stat label="Holdout win rate" value={chosen?.test?.win_rate ? `${chosen.test.win_rate}%` : '—'} />
                <Stat label="Holdout expectancy (R)"
                  tone={(chosen?.test?.expectancy_r ?? 0) > 0 ? 'pos' : 'neg'}
                  value={chosen?.test?.expectancy_r} />
              </div>
              {chosen && (
                <div className="panel">
                  <h3>Selected configuration</h3>
                  <div className="rm-grid">
                    {Object.entries({ ...chosen.entry, ...chosen.exit }).map(([k, v]) => (
                      <div className="rm-kv sm" key={k}>
                        <span>{k.replace(/_/g, ' ')}</span><b>{String(v)}</b></div>
                    ))}
                  </div>
                </div>
              )}
              {bt.test_breakdowns && Object.entries(bt.test_breakdowns).map(([k, g]: any) => (
                <MetricTable key={k} title={`Holdout — ${k.replace('by_', '').replace(/_/g, ' ')}`} groups={g} />
              ))}
              {bt.coverage && (
                <div className="panel">
                  <h3>Data coverage</h3>
                  <p className="muted" style={{ fontSize: 12 }}>
                    {bt.coverage.filter((c: any) => c.bars > 0).length} of {bt.coverage.length} series
                    returned data — {bt.coverage.reduce((a: number, c: any) => a + c.bars, 0).toLocaleString()} bars.
                    1-minute and 3-minute bars are not available on the current data plan and are
                    reported untested rather than assumed.
                  </p>
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === 'Strategy lab' && cfg && (
        <>
          <div className="panel">
            <h3>Variants — kept permanently, never overwritten</h3>
            <table className="tbl">
              <thead><tr><th>Variant</th><th>Version</th><th>What it adds</th></tr></thead>
              <tbody>
                {Object.entries(cfg.variants).map(([k, v]: any) => (
                  <tr key={k}>
                    <td><b>{v.label}</b><br /><small className="muted">{k}</small></td>
                    <td className="mono">{cfg.versions[k]}</td>
                    <td style={{ maxWidth: 460 }}>{v.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="panel">
            <h3>Setup-quality weights</h3>
            <p className="muted" style={{ fontSize: 12 }}>
              A starting hypothesis, not a validated model. Weights get reweighted from
              realised results once the sample supports it.
            </p>
            <div className="rm-grid">
              {Object.entries(cfg.score_weights).map(([k, v]: any) => (
                <div className="rm-kv sm" key={k}>
                  <span>{k.replace(/_/g, ' ')}</span><b>{v}</b></div>
              ))}
            </div>
          </div>
          <div className="panel">
            <h3>Tested parameter ranges</h3>
            <div className="rm-grid">
              {Object.entries(cfg.test_ranges).map(([k, v]: any) => (
                <div className="rm-kv sm" key={k}>
                  <span>{k.replace(/_/g, ' ')}</span><b>{(v as any[]).join(', ')}</b></div>
              ))}
            </div>
          </div>
        </>
      )}
    </main>
  );
}
