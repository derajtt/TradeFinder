'use client';
import { Fragment, useEffect, useMemo, useState } from 'react';
import { apiGet, usePolling } from '../../lib/api';
import { fmtEtDate } from '../../lib/format';
import { Tip } from '../../components/TradeRoadmap';

/* ── Quant Lab ──────────────────────────────────────────────────────────────
   Reads /api/lab/* and presents every strategy with the claim it was built on,
   its lifecycle stage and its evidence. Property access is deliberately
   defensive: the API mirrors LabStrategy / LabRun / LabTrade but may nest the
   run metrics under `metrics`, so every number is looked up through `pick`.  */

type Any = Record<string, any>;

const TABS = ['Overview', 'Strategies', 'Leaderboard', 'Compare', 'Ensemble', 'Portfolio'] as const;
type Tab = typeof TABS[number];

const STAGES = ['RESEARCH', 'BACKTESTING', 'VALIDATION', 'PAPER_TRADING',
  'PROMISING', 'PRODUCTION_CANDIDATE', 'FAILED'];

const SMALL_N = 30;
const MAX_COMPARE = 4;
const SERIES_COLORS = ['var(--accent)', 'var(--buy)', 'var(--warn)', 'var(--early)'];

/* Plain-English explanations. Every technical term on the page routes here. */
const T = {
  expectancy: 'Expectancy — the average result per trade after estimated costs. Measured in R (units of initial risk) or percent. Positive means the strategy made money on average; this matters far more than how often it wins.',
  pf: 'Profit Factor — gross profits divided by gross losses, after costs. 1.0 is breakeven; 2.0 means $2 made for every $1 lost. Below 1.0 loses money.',
  maxdd: 'Maximum Drawdown — the largest peak-to-trough fall in the equity curve, in R (units of initial risk) unless marked %. It describes the worst losing stretch you would have had to sit through.',
  sharpe: 'Sharpe ratio — average return per unit of volatility. Higher means smoother returns for the risk taken. Only shown when the backend computed it.',
  sortino: 'Sortino ratio — like Sharpe, but only downside volatility counts as risk. Upside swings are not penalised.',
  composite: 'Composite score — a rank-average from 0 to 1 across expectancy, profit factor, drawdown, Sortino, consistency and the Wilson lower bound of the win rate. Higher is better; 1.0 means best on every component. The raw win rate is deliberately not a component.',
  consistency: 'Consistency — the share of calendar months that finished positive. A strategy that made all its money in one lucky month scores low. n here is the number of months.',
  winrate: 'Win rate — winning trades divided by decided trades. A high win rate can still lose money if the losers are bigger than the winners, which is why it is never used for ranking here.',
  avgwin: 'Average winner — the mean result of winning trades. Compare it with the average loser to see whether the strategy relies on frequency or on size.',
  avgloss: 'Average loser — the mean result of losing trades, including costs.',
  rr: 'Average planned reward-to-risk — target distance divided by stop distance at entry, averaged over the trades taken. 2:1 means the plan aimed to make twice what it risked; whether it got there is what expectancy measures.',
  streak: 'Streak — the current run of consecutive wins or losses. Long losing streaks are normal for low win-rate strategies; the drawer shows the longest ever recorded.',
  regime: 'Market regime — a classification of current conditions (trend / range / event / high-risk / uncertain). Many strategies only trade in the regimes where they historically had an edge.',
  session: 'Session bucket — the part of the trading day the signal fired in (premarket, open, midday, close, after-hours). Edges are often concentrated in one part of the day.',
  stage: 'Lifecycle stage — RESEARCH → BACKTESTING → VALIDATION → PAPER_TRADING → PROMISING → PRODUCTION_CANDIDATE. FAILED means the evidence rejected the hypothesis. Nothing is promoted without out-of-sample and paper evidence.',
  family: 'Strategy family — groups correlated ideas (momentum, mean reversion, trend, breakout, ...). An ensemble never counts four flavours of the same family as four independent confirmations.',
  timeframe: 'Bar size the strategy works on. 5min = five-minute candles; 1day = daily bars. The preferred timeframe is the one that tested best.',
  montecarlo: 'Monte Carlo — the recorded trades are reshuffled thousands of times to see the range of outcomes the same edge could have produced. Percentiles (p5 / p50 / p95) show pessimistic, typical and optimistic paths.',
  robustness: 'Robustness — how much the results change when parameters are nudged, costs are raised or the period is shifted. A real edge survives small changes; an overfit one collapses.',
  walkforward: 'Walk-forward — repeatedly tune on the past, then test on the next unseen period. The honest version of a backtest.',
  smallsample: 'Fewer than 30 decided trades. With so few trades, every statistic here is mostly noise — treat it as a hypothesis, not a result.',
  equity: 'Equity curve — cumulative R (units of initial risk) after each closed trade, in order. Smooth and rising is good; a jagged curve with deep dips means the same edge came with a rough ride.',
  ensemble: 'Ensemble — a combination of strategies chosen so their signals are not all saying the same thing at once. Weights are capped per family to avoid hidden concentration.',
  correlation: 'Correlation of returns between two strategies, from −1 to +1. Near 0 means they win and lose at different times, which is what makes combining them useful.',
  portfolio: 'Paper portfolio — the simulated account the lab strategies trade in. Realistic fills and costs, no real money, no real orders.',
  active: 'Active signals — setups the strategy currently flags as live (entered or waiting for entry).',
  invalidation: 'Invalidation — the condition that would prove the trade idea wrong. If it happens, the position is closed regardless of price.',
  hold: 'Intended holding period: scalp (minutes), intraday (closed before the bell) or swing (days).',
  stopmethod: 'How the initial stop is placed — ATR multiple, below the swing low, structural level, VWAP, standard deviation or trailing.',
  n: 'n — decided trades, then the number of separate sessions they happened on (e.g. "49 / 6d"). Twenty trades in one market-wide move are closer to one observation than twenty, so the confidence label is capped by the session count. Any statistic without its n is meaningless, so it is always shown.',
  confidence: 'Confidence label — derived from sample size: very low (<30 trades), low (30–99), moderate (100–499), high (500+). It is about how much to trust the statistics, not about profit.',
  return: 'Total return — cumulative percent result over the recorded period, after estimated costs, with the lab\'s fixed position sizing.',
  cohort: 'Cohort — which evidence bucket the trades come from: backtest, paper or live. They are never mixed.',
  mfe: 'MFE / MAE — the best and worst unrealised excursion the trade saw after entry, in R. Diagnostics, not results.',
  options: 'Options data is not available on the current data plan, so options markets are listed but untested rather than assumed.',
  funnel: 'How many strategies sit in each lifecycle stage right now. Most ideas are expected to fail; that is the point of the funnel.',
  source: 'Where the regime reading comes from (for example SPY daily bars, VIX, breadth). The caption says which data produced it and when.',
  hypothesis: 'The hypothesis — the stated reason there should be an edge. Kept next to the results so a strategy is never judged without the claim it was built on.',
  version: 'Version of the strategy definition. Any change to logic or parameters bumps the version, and past results stay attached to the old one.',
  optimizations: 'How many times the parameters have been re-tuned. More tuning means a higher chance of overfitting, which the composite score penalises.',
  monthly: 'Result per calendar month. Used for the consistency score.',
  costs: 'Estimated trading costs baked into every result: spread, slippage and fees.',
  coverage: 'Data coverage — how many symbols and bars the test actually saw. Gaps are reported, never silently filled.',
  split: 'Evidence split — train (tuned on), validation (used to choose parameters), out-of-sample (untouched until the end) and forward (live paper after freezing). Only the last two predict anything.',
  wilson: 'Wilson lower bound — a conservative floor on the true win rate given the sample size. 3 wins in 3 trades has a low bound; 300 in 500 has a tight one. It stops tiny samples from looking perfect.',
  forward: 'Forward record — paper trades taken live after the strategy was frozen, kept apart from the backtest. Usually the smallest sample and the most honest one.',
  agreement: 'Agreement effect — expectancy on symbol-days where two or more families flagged the same trade, minus expectancy on days where only one did. Positive means confirmation across independent ideas helped.',
  diversification: 'Diversification benefit — combined portfolio max drawdown minus the best single leg\'s max drawdown, in R. Positive means the mix had a shallower worst stretch than any leg alone.',
  sizing: 'Equal-risk sizing — every leg is sized so that 1R (one unit of initial risk) is the same dollar amount, so no strategy dominates just because it trades bigger.',
  haircut: 'Win-rate haircut — a stress test that turns 10% of the winners into average losers to see whether the edge survives being a little less lucky than the record suggests.',
  rmult: 'R — result measured in units of the initial risk. Entry $10 with a stop at $9 risks $1; exiting at $12 is +2R. Comparable across symbols and account sizes.',
  symbol: 'Per-symbol results — a strategy whose whole edge sits in one or two symbols is fragile. The more evenly the edge is spread, the more it looks like a real pattern rather than a lucky ticker.',
};

/* ── small helpers ─────────────────────────────────────────────────────────── */

function num(v: any): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** First non-null value among candidate keys. */
function pick(o: Any | null | undefined, keys: string[]): any {
  if (!o) return undefined;
  for (const k of keys) {
    const v = o[k];
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return undefined;
}

/** Flatten a strategy record and any nested metric bags into one lookup object. */
function mx(s: Any | null | undefined): Any {
  if (!s) return {};
  const bags = ['metrics', 'stats', 'performance', 'summary', 'best', 'best_run', 'oos', 'latest', 'headline'];
  const out: Any = {};
  for (const b of bags) {
    const bag = s[b];
    if (!bag || typeof bag !== 'object' || Array.isArray(bag)) continue;
    if (bag.metrics && typeof bag.metrics === 'object' && !Array.isArray(bag.metrics)) Object.assign(out, bag.metrics); // raw harness keys
    Object.assign(out, bag);                                                                                        // normalised keys win
  }
  Object.assign(out, s);
  return out;
}

function fmt(v: any, d = 2): string {
  const n = num(v);
  if (n === null) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtSigned(v: any, d = 2, unit = ''): string {
  const n = num(v);
  if (n === null) return '—';
  return (n > 0 ? '+' : '') + fmt(n, d) + unit;
}
function fmtInt(v: any): string {
  const n = num(v);
  return n === null ? '—' : Math.round(n).toLocaleString('en-US');
}
function label(s: any): string { return String(s ?? '').replace(/_/g, ' '); }
function tone(v: any): string | undefined {
  const n = num(v);
  if (n === null) return undefined;
  return n > 0 ? 'pos' : n < 0 ? 'neg' : undefined;
}

/* Metric getters — each returns {v, unit} so the unit always travels with the value. */
interface MV { v: number | null; unit: string; }
function getM(m: Any, spec: [string, string][]): MV {
  for (const [k, unit] of spec) {
    const n = num(m[k]);
    if (n !== null) return { v: n, unit };
  }
  return { v: null, unit: '' };
}
const G = {
  n: (m: Any) => num(pick(m, ['n', 'trades', 'trade_count', 'num_trades', 'total_trades', 'resolved', 'decided'])),
  wins: (m: Any) => num(pick(m, ['wins', 'win_count', 'winners'])),
  losses: (m: Any) => num(pick(m, ['losses', 'loss_count', 'losers'])),
  winRate: (m: Any): number | null => {
    const r = num(pick(m, ['win_rate', 'win_rate_pct', 'wr', 'winrate']));
    if (r === null) {
      const w = G.wins(m), l = G.losses(m);
      return w !== null && l !== null && w + l > 0 ? (w / (w + l)) * 100 : null;
    }
    return r <= 1 ? r * 100 : r;
  },
  pf: (m: Any) => num(pick(m, ['profit_factor', 'pf'])),
  /* The harness stores expectancy in R (expectancy_r / expectancy); a percent variant is kept as a fallback. */
  expectancy: (m: Any) => getM(m, [['expectancy_r', 'R'], ['exp_r', 'R'], ['avg_r', 'R'], ['expectancy', 'R'], ['expectancy_pct', '%'], ['exp_pct', '%']]),
  avgWin: (m: Any) => getM(m, [['avg_winner_r', 'R'], ['avg_win_r', 'R'], ['avg_winner_pct', '%'], ['avg_win_pct', '%'], ['avg_winner', ''], ['avg_win', '']]),
  avgLoss: (m: Any) => getM(m, [['avg_loser_r', 'R'], ['avg_loss_r', 'R'], ['avg_loser_pct', '%'], ['avg_loss_pct', '%'], ['avg_loser', ''], ['avg_loss', '']]),
  totalReturn: (m: Any) => num(pick(m, ['total_return_pct', 'return_pct', 'net_return_pct', 'total_return', 'cum_return_pct'])),
  totalR: (m: Any) => num(pick(m, ['total_r', 'cum_r', 'net_r'])),
  /* Drawdown travels with its unit: the harness writes max_drawdown_r (a positive magnitude in R). */
  maxDD: (m: Any) => getM(m, [['max_drawdown_pct', '%'], ['max_dd_pct', '%'], ['max_drawdown_r', 'R'], ['max_dd_r', 'R'], ['max_drawdown', ''], ['max_dd', ''], ['maxdd', '']]),
  sharpe: (m: Any) => num(pick(m, ['sharpe', 'sharpe_ratio'])),
  sortino: (m: Any) => num(pick(m, ['sortino', 'sortino_ratio'])),
  avgRR: (m: Any) => num(pick(m, ['avg_rr', 'avg_planned_rr', 'avg_reward_risk', 'avg_rr_realized'])),
  streak: (m: Any) => pick(m, ['streak', 'current_streak']),
  maxWinStreak: (m: Any) => num(pick(m, ['max_win_streak', 'longest_win_streak', 'best_streak']) ?? m.streaks?.max_win),
  maxLossStreak: (m: Any) => num(pick(m, ['max_loss_streak', 'longest_loss_streak', 'worst_streak']) ?? m.streaks?.max_loss),
  consistency: (m: Any) => num(pick(m, ['consistency', 'consistency_score', 'pct_profitable_months', 'profitable_months_pct'])),
  months: (m: Any) => num(pick(m, ['months', 'n_months'])),
  wilson: (m: Any) => num(pick(m, ['wilson_lb', 'win_rate_lb', 'wr_lb'])),
  composite: (m: Any) => num(pick(m, ['composite_score', 'composite', 'score'])),
};

/** Max drawdown as text, sign and unit included (blank unit when the backend did not say). */
function ddText(m: Any): string {
  const d = G.maxDD(m);
  if (d.v === null) return '—';
  return fmt(-Math.abs(d.v), d.unit === '%' ? 1 : 2) + d.unit;
}
/** Fractions (0..1) and percents (0..100) both render as a percent. */
function pctText(v: number | null, d = 0): string {
  if (v === null) return '—';
  return fmt(Math.abs(v) <= 1 ? v * 100 : v, d) + '%';
}
/** The timeframe to headline a card with: the tested-best one when it is a real timeframe, else the first declared. */
function preferredTf(s: Any): string {
  const tfs: string[] = Array.isArray(s.timeframes) ? s.timeframes.map(String) : [];
  const best = String(pick(s, ['preferred_timeframe', 'best_timeframe']) ?? '');
  if (best && tfs.includes(best)) return best;
  if (best && !tfs.length && !/\s/.test(best)) return best;
  return tfs[0] ?? '';
}

function sid(s: Any): string { return String(s?.strategy_id ?? s?.id ?? s?.name ?? ''); }
function listOf(d: any, keys: string[]): Any[] {
  if (Array.isArray(d)) return d;
  for (const k of keys) if (Array.isArray(d?.[k])) return d[k];
  return [];
}
function activeCount(s: Any): number | null {
  const a = pick(s, ['active_signals', 'active', 'open_signals', 'live_signals']);
  if (Array.isArray(a)) return a.length;
  if (a !== undefined) return num(a);
  const fo = num(s?.forward?.open);
  if (fo !== null) return fo;
  const bm = s?.by_market;
  if (bm && typeof bm === 'object') {
    let sum = 0, seen = false;
    for (const mk of Object.values(bm) as Any[]) {
      const tfs = mk?.timeframes;
      if (!tfs || typeof tfs !== 'object') continue;
      for (const cell of Object.values(tfs) as Any[]) {
        const o = num(cell?.forward?.open);
        if (o !== null) { sum += o; seen = true; }
      }
    }
    if (seen) return sum;
  }
  return null;
}
function lastSignal(s: Any): string | null {
  const v = pick(s, ['last_signal', 'last_signal_at', 'last_signal_time', 'last_signal_ts']);
  if (typeof v === 'string') return v;
  if (v && typeof v === 'object') return v.signal_time ?? v.time ?? v.at ?? null;
  const rt = Array.isArray(s?.recent_trades) ? s.recent_trades : [];
  const first = rt.map((t: Any) => String(t?.signal_time ?? '')).filter(Boolean).sort().pop();
  return first ?? null;
}
function confidenceLabel(s: Any, n: number | null): string {
  const given = pick(s, ['confidence_label', 'confidence_band', 'evidence_label'])
    ?? (typeof s?.confidence === 'string' ? s.confidence : undefined)
    ?? (typeof s?.headline?.confidence === 'string' ? s.headline.confidence : undefined);
  if (typeof given === 'string') return given.replace(/_/g, ' ').toLowerCase();
  if (n === null) return 'no data';
  if (n < SMALL_N) return 'very low';
  if (n < 100) return 'low';
  if (n < 500) return 'moderate';
  return 'high';
}
function stageClass(stage: string): string {
  switch (stage) {
    case 'PRODUCTION_CANDIDATE': case 'PROMISING': return 'buy';
    case 'PAPER_TRADING': return 'st-paper';
    case 'VALIDATION': return 'early';
    case 'FAILED': return 'risk';
    default: return 'neutral';
  }
}

/** Normalise the many equity-curve encodings to a plain number[]. */
function curveOf(s: Any | null | undefined): number[] {
  if (!s) return [];
  const raw = pick(s, ['equity_curve', 'equity', 'curve', 'cum_returns', 'cumulative']) ?? s?.best_run?.equity_curve;
  if (!Array.isArray(raw)) return [];
  const out: number[] = [];
  for (const p of raw) {
    if (typeof p === 'number') out.push(p);
    else if (Array.isArray(p)) { const v = num(p.length >= 2 ? p[1] : p[0]); if (v !== null) out.push(v); } // [t, cum_r, cum_pct]
    else if (p && typeof p === 'object') {
      const v = num(pick(p, ['equity', 'value', 'v', 'cum_r', 'r', 'e', 'y', 'cum', 'balance', 'pnl']));
      if (v !== null) out.push(v);
    }
  }
  return out;
}


/* ── tiny local fetch hook (conditional paths, one shot) ───────────────────── */
function useGet<T>(path: string | null, deps: any[] = []): [T | null, Error | null, boolean] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) { setData(null); setErr(null); return; }
    let alive = true;
    setLoading(true);
    apiGet<T>(path)
      .then((d) => { if (alive) { setData(d); setErr(null); } })
      .catch((e) => { if (alive) { setErr(e); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  return [data, err, loading];
}

/* ── presentational atoms ──────────────────────────────────────────────────── */

function L({ tip, children }: { tip: string; children: React.ReactNode }) {
  return <Tip term={tip}>{children}</Tip>;
}

function NBadge({ n }: { n: number | null }) {
  return (
    <L tip={T.n}>
      <span className={`badge ${n !== null && n < SMALL_N ? 'warn' : 'neutral'}`} style={{ fontSize: 9.5, padding: '1px 7px' }}>
        n={n === null ? '—' : fmtInt(n)}
      </span>
    </L>
  );
}

/** A value that never appears without its sample size. */
function Metric({ label: lbl, tip, value, n, tn, small }: {
  label: string; tip: string; value: string; n: number | null; tn?: string; small?: boolean;
}) {
  const color = tn === 'pos' ? 'var(--buy)' : tn === 'neg' ? 'var(--risk)' : undefined;
  return (
    <div className="kv" style={small ? { padding: '8px 11px' } : undefined}>
      <div className="k"><L tip={tip}>{lbl}</L></div>
      <div className="v" style={{ color, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span>{value}</span>
        <span style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--text-faint)', fontFamily: 'var(--sans)' }}>
          n={n === null ? '—' : fmtInt(n)}
        </span>
      </div>
    </div>
  );
}

function StagePill({ stage }: { stage: string }) {
  const cls = stageClass(stage);
  const isSt = cls.startsWith('st-');
  return (
    <L tip={T.stage}>
      <span className={isSt ? `st ${cls}` : `badge ${cls}`}>{label(stage) || 'UNKNOWN'}</span>
    </L>
  );
}

function ConfBadge({ label: lbl }: { label: string }) {
  const u = lbl.toUpperCase();
  const cls = u === 'HIGH' || u === 'GOOD' ? 'buy' : u === 'MODERATE' ? 'neutral' : 'warn';
  return <L tip={T.confidence}><span className={`badge ${cls}`} style={{ fontSize: 9.5 }}>{lbl.toLowerCase()}</span></L>;
}

function SmallSampleBanner({ n }: { n: number | null }) {
  if (n !== null && n >= SMALL_N) return null;
  return (
    <div role="status" style={{
      margin: '10px 0', padding: '10px 14px', borderRadius: 10, fontSize: 12.5, lineHeight: 1.5,
      background: 'var(--warn-soft)', border: '1px solid rgba(251, 191, 36, 0.3)', color: 'var(--warn)',
    }}>
      <b>SMALL SAMPLE</b> — {n === null ? 'no decided trades recorded yet' : `only ${fmtInt(n)} decided trades`}.{' '}
      <L tip={T.smallsample}><span>Every statistic below is provisional.</span></L>
    </div>
  );
}

function Sparkline({ curve, color = 'var(--accent)', height = 44, width = 220 }: {
  curve: number[]; color?: string; height?: number; width?: number;
}) {
  if (curve.length < 2) return <span className="faint" style={{ fontSize: 11 }}>no equity curve yet</span>;
  const min = Math.min(...curve), max = Math.max(...curve);
  const span = max - min || 1;
  const pts = curve.map((v, i) => {
    const x = (i / (curve.length - 1)) * (width - 2) + 1;
    const y = height - 2 - ((v - min) / span) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const up = curve[curve.length - 1] >= curve[0];
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height }} role="img"
      aria-label="Equity sparkline" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={up ? color : 'var(--risk)'} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

function MultiEquity({ series, unit = '%' }: { series: { name: string; curve: number[]; color: string }[]; unit?: string }) {
  const W = 720, H = 220, padL = 44, padR = 12, padT = 12, padB = 26;
  const valid = series.filter((s) => s.curve.length >= 2);
  if (!valid.length) return <div className="empty">Select strategies with recorded equity curves to overlay them.</div>;
  const all = valid.flatMap((s) => s.curve);
  const min = Math.min(0, ...all), max = Math.max(0, ...all);
  const span = max - min || 1;
  const maxLen = Math.max(...valid.map((s) => s.curve.length));
  const y = (v: number) => padT + (1 - (v - min) / span) * (H - padT - padB);
  const x = (i: number, len: number) => padL + (i / Math.max(1, len - 1)) * (W - padL - padR);
  const ticks = [min, min + span / 2, max];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }} role="img"
      aria-label="Overlaid equity curves from a common start">
      {ticks.map((t, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} stroke="var(--line-soft)" />
          <text x={padL - 6} y={y(t) + 3} fontSize="9" fill="var(--text-faint)" textAnchor="end">{fmtSigned(t, 1, unit)}</text>
        </g>
      ))}
      <line x1={padL} x2={W - padR} y1={y(0)} y2={y(0)} stroke="var(--line)" strokeDasharray="3 3" />
      {valid.map((s) => (
        <polyline key={s.name} fill="none" stroke={s.color} strokeWidth="1.8" strokeLinejoin="round"
          points={s.curve.map((v, i) => `${x(i, s.curve.length).toFixed(1)},${y(v).toFixed(1)}`).join(' ')} />
      ))}
      <text x={padL} y={H - 8} fontSize="9" fill="var(--text-faint)">trade 1</text>
      <text x={W - padR} y={H - 8} fontSize="9" fill="var(--text-faint)" textAnchor="end">trade {maxLen}</text>
      {valid.map((s, i) => (
        <g key={s.name}>
          <rect x={padL + i * 160} y={2} width={9} height={9} fill={s.color} rx={2} />
          <text x={padL + i * 160 + 13} y={10} fontSize="9.5" fill="var(--text-dim)">{s.name.slice(0, 22)}</text>
        </g>
      ))}
    </svg>
  );
}

/** Group → metrics table (by regime / session / symbol / market / timeframe). */
function BreakdownTable({ title, tip, groups }: { title: string; tip: string; groups: any }) {
  const rows: [string, Any][] = Array.isArray(groups)
    ? groups.map((g: Any, i: number) => [String(g.key ?? g.name ?? g.group ?? g.regime ?? g.session ?? g.symbol ?? i), g])
    : Object.entries(groups || {}).map(([k, v]) => [k, (v && typeof v === 'object' ? v : { value: v }) as Any]);
  const filtered = rows.filter(([, m]) => G.n(m) !== null || G.expectancy(m).v !== null);
  if (!filtered.length) {
    return (
      <div className="panel"><h3><L tip={tip}>{title}</L></h3>
        <p className="muted" style={{ fontSize: 12.5 }}>No decided trades in this breakdown yet.</p></div>
    );
  }
  filtered.sort((a, b) => (G.expectancy(b[1]).v ?? -1e9) - (G.expectancy(a[1]).v ?? -1e9));
  return (
    <div className="panel">
      <h3><L tip={tip}>{title}</L></h3>
      <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
        <table className="tbl" style={{ minWidth: 560 }}>
          <thead><tr>
            <th className="l">Group</th>
            <th><L tip={T.n}>n</L></th>
            <th><L tip={T.winrate}>Win rate</L></th>
            <th><L tip={T.expectancy}>Expectancy</L></th>
            <th><L tip={T.pf}>PF</L></th>
            <th><L tip={T.maxdd}>Max DD</L></th>
          </tr></thead>
          <tbody>
            {filtered.map(([k, m]) => {
              const n = G.n(m); const e = G.expectancy(m); const wr = G.winRate(m); const dd = G.maxDD(m).v;
              return (
                <tr key={k} style={{ cursor: 'default' }}>
                  <td className="l">{label(k)}</td>
                  <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                  <td>{wr === null ? '—' : fmt(wr, 0) + '%'}</td>
                  <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</td>
                  <td>{fmt(G.pf(m))}</td>
                  <td className={dd !== null ? 'neg' : ''}>{ddText(m)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MonteCarloTable({ mc }: { mc: any }) {
  if (!mc || typeof mc !== 'object') return null;
  const src: Any = mc.metrics && typeof mc.metrics === 'object' ? mc.metrics : mc;
  const n = num(src.n);
  const iters = num(pick(src, ['iterations', 'runs', 'n_sims']));
  const where = [mc.market, mc.timeframe].filter(Boolean).join(' / ');
  type Row = { name: string; tip: string; p5: any; p50: any; p95: any; unit: string; neg?: boolean };
  const rows: Row[] = [];
  const dd = src.drawdown_r;
  if (dd && typeof dd === 'object') {
    rows.push({ name: 'Max drawdown with the trade order reshuffled', tip: T.maxdd, p5: dd.p5, p50: dd.p50, p95: dd.p95, unit: 'R', neg: true });
  }
  const stress: Any | null = src.stress && typeof src.stress === 'object' && !Array.isArray(src.stress) ? src.stress : null;
  const stressName: Record<string, string> = { base: 'base costs', 'slip_x1.5': 'slippage ×1.5', slip_x2: 'slippage ×2' };
  if (stress) {
    for (const [k, v] of Object.entries(stress)) {
      const o = v as Any;
      if (!o || typeof o !== 'object') continue;
      rows.push({ name: `Expectancy under ${stressName[k] ?? label(k)}`, tip: T.costs, p5: o.expectancy_p5, p50: o.expectancy_p50, p95: o.expectancy_p95, unit: 'R' });
    }
  }
  const hair: Any | null = src.winrate_minus_10pp && typeof src.winrate_minus_10pp === 'object' ? src.winrate_minus_10pp : null;
  if (hair) {
    rows.push({ name: `Expectancy with win rate cut by 10 points (${fmtInt(hair.flipped)} winners re-priced as losers)`, tip: T.haircut, p5: hair.p5, p50: hair.p50, p95: hair.p95, unit: 'R' });
  }
  const scalars: [string, any][] = [];
  if (!rows.length) {
    for (const [k, v] of Object.entries(src)) {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        const o = v as Any;
        rows.push({ name: label(k), tip: T.montecarlo, p5: pick(o, ['p5', 'p05', 'pessimistic', 'low', 'worst']), p50: pick(o, ['p50', 'median', 'typical', 'mean']), p95: pick(o, ['p95', 'optimistic', 'high', 'best']), unit: '' });
      } else if (typeof v !== 'object' && k !== 'n' && k !== 'note') scalars.push([k, v]);
    }
  }
  if (!rows.length && !scalars.length && !src.note) return null;
  const cell = (r: Row, v: any) => {
    const x = num(v);
    if (x === null) return <td className="faint">{v === undefined || v === null ? '—' : String(v)}</td>;
    if (r.neg) return <td className="neg">{fmt(-Math.abs(x), 2)}{r.unit}</td>;
    return <td className={tone(x)}>{fmtSigned(x, 2, r.unit)}</td>;
  };
  return (
    <div className="panel">
      <h3>
        <L tip={T.montecarlo}>Monte Carlo</L>
        <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500, marginLeft: 8 }}>
          {n !== null ? `${fmtInt(n)} trades` : ''}{iters !== null ? ` · ${fmtInt(iters)} reshuffles` : ''}{where ? ` · ${where}` : ''}
        </span>
      </h3>
      {src.note && <p className="muted" style={{ fontSize: 12.5 }}>{String(src.note)}</p>}
      {rows.length > 0 && (
        <>
          <p className="muted" style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.55 }}>
            p5 is the unlucky path, p50 the typical one, p95 the lucky one — except for drawdown, where p95 is the deep end.
            An edge that keeps a positive p5 expectancy under doubled slippage does not depend on perfect fills.
          </p>
          <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
            <table className="tbl" style={{ minWidth: 560 }}>
              <thead><tr><th className="l">Distribution</th><th>p5</th><th>p50 (median)</th><th>p95</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.name} style={{ cursor: 'default' }}>
                    <td className="l" style={{ whiteSpace: 'normal' }}><L tip={r.tip}>{r.name}</L></td>
                    {cell(r, r.p5)}{cell(r, r.p50)}{cell(r, r.p95)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {stress && (
        <div className="tbl-wrap" style={{ boxShadow: 'none', marginTop: 10 }}>
          <table className="tbl" style={{ minWidth: 480 }}>
            <thead><tr>
              <th className="l"><L tip={T.costs}>Cost level</L></th><th><L tip={T.expectancy}>Expectancy</L></th>
              <th><L tip={T.winrate}>Win rate</L></th><th><L tip={T.pf}>PF</L></th><th><L tip={T.n}>n</L></th>
            </tr></thead>
            <tbody>
              {Object.entries(stress).map(([k, v]) => {
                const o = v as Any;
                if (!o || typeof o !== 'object') return null;
                const ex = num(o.expectancy_r);
                const w = G.winRate(o);
                return (
                  <tr key={k} style={{ cursor: 'default' }}>
                    <td className="l">{stressName[k] ?? label(k)}</td>
                    <td className={tone(ex)}>{fmtSigned(ex, 2, 'R')}</td>
                    <td>{w === null ? '—' : fmt(w, 0) + '%'}</td>
                    <td>{fmt(G.pf(o))}</td>
                    <td>{fmtInt(n)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {scalars.length > 0 && (
        <div className="rm-grid" style={{ padding: 0, marginTop: 8 }}>
          {scalars.map(([k, v]) => (
            <div className="rm-kv sm" key={k} style={{ padding: '3px 0' }}><span>{label(k)}</span><b>{typeof v === 'number' ? fmt(v) : String(v)}</b></div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Generic flat key/value grid for dictionaries whose exact keys are backend-defined. */
function KVGrid({ obj, tipFor }: { obj: any; tipFor?: (k: string) => string | undefined }) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null;
  const entries = Object.entries(obj).filter(([, v]) => v === null || typeof v !== 'object');
  if (!entries.length) return null;
  return (
    <div className="rm-grid" style={{ padding: 0 }}>
      {entries.map(([k, v]) => {
        const tip = tipFor?.(k);
        const shown = typeof v === 'number' ? fmt(v, Number.isInteger(v) ? 0 : 2)
          : typeof v === 'boolean' ? (v ? 'yes' : 'no') : v === null || v === undefined ? '—' : String(v);
        return (
          <div className="rm-kv sm" key={k} style={{ padding: '4px 0' }}>
            <span>{tip ? <L tip={tip}>{label(k)}</L> : label(k)}</span><b>{shown}</b>
          </div>
        );
      })}
    </div>
  );
}

/** Array of records → table over the union of their scalar keys. */
function GenericTable({ rows, max = 60 }: { rows: Any[]; max?: number }) {
  if (!rows?.length) return null;
  const keys: string[] = [];
  for (const r of rows.slice(0, max)) for (const k of Object.keys(r || {})) {
    if (!keys.includes(k) && (r[k] === null || typeof r[k] !== 'object')) keys.push(k);
  }
  if (!keys.length) return null;
  return (
    <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
      <table className="tbl" style={{ minWidth: Math.max(480, keys.length * 110) }}>
        <thead><tr>{keys.map((k, i) => <th key={k} className={i === 0 ? 'l' : ''}>{label(k)}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, max).map((r, i) => (
            <tr key={i} style={{ cursor: 'default' }}>
              {keys.map((k, j) => {
                const v = r?.[k];
                const s = typeof v === 'number' ? fmt(v, Number.isInteger(v) ? 0 : 2)
                  : typeof v === 'boolean' ? (v ? 'yes' : 'no')
                    : v === null || v === undefined ? '—' : /(_at|_time|time)$/.test(k) ? fmtEtDate(String(v)) : String(v);
                return <td key={k} className={j === 0 ? 'l' : ''}>{s}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Recursive renderer for a backend object whose exact shape is not yet fixed. */
function AutoSection({ data, tipFor, depth = 0 }: { data: any; tipFor?: (k: string) => string | undefined; depth?: number }) {
  if (!data || typeof data !== 'object') return null;
  const scalars: Any = {};
  const arrays: [string, any[]][] = [];
  const objects: [string, Any][] = [];
  for (const [k, v] of Object.entries(data)) {
    if (v === null || typeof v !== 'object') scalars[k] = v;
    else if (Array.isArray(v)) arrays.push([k, v]);
    else objects.push([k, v as Any]);
  }
  return (
    <>
      {Object.keys(scalars).length > 0 && <KVGrid obj={scalars} tipFor={tipFor} />}
      {arrays.map(([k, arr]) => (
        <div key={k} style={{ marginTop: 12 }}>
          <div className="subhead" style={{ margin: '12px 0 8px' }}>{label(k)} <span className="faint" style={{ marginLeft: 6, letterSpacing: 0 }}>({arr.length})</span></div>
          {arr.length === 0 ? <p className="faint" style={{ fontSize: 12 }}>empty</p>
            : typeof arr[0] === 'object' ? <GenericTable rows={arr} />
              : <div className="row" style={{ gap: 6 }}>{arr.slice(0, 40).map((x, i) => <span key={i} className="badge neutral">{String(x)}</span>)}</div>}
        </div>
      ))}
      {depth < 2 && objects.map(([k, o]) => (
        <div key={k} className="panel" style={{ marginTop: 12, marginBottom: 0 }}>
          <h3>{tipFor?.(k) ? <L tip={tipFor(k)!}>{label(k)}</L> : label(k)}</h3>
          <AutoSection data={o} tipFor={tipFor} depth={depth + 1} />
        </div>
      ))}
    </>
  );
}

function tipForKey(k: string): string | undefined {
  const s = k.toLowerCase();
  if (s.includes('expectancy')) return T.expectancy;
  if (s.includes('profit_factor') || s === 'pf') return T.pf;
  if (s.includes('drawdown') || s.includes('max_dd') || s.includes('maxdd')) return T.maxdd;
  if (s.includes('sharpe')) return T.sharpe;
  if (s.includes('sortino')) return T.sortino;
  if (s.includes('composite')) return T.composite;
  if (s.includes('consistency')) return T.consistency;
  if (s.includes('win_rate') || s === 'wr') return T.winrate;
  if (s.includes('correlation') || s.includes('corr')) return T.correlation;
  if (s.includes('regime')) return T.regime;
  if (s.includes('session')) return T.session;
  if (s.includes('stage')) return T.stage;
  if (s.includes('family')) return T.family;
  if (s.includes('timeframe')) return T.timeframe;
  if (s.includes('cohort')) return T.cohort;
  if (s.includes('return')) return T.return;
  if (s.includes('streak')) return T.streak;
  if (s.includes('cost') || s.includes('slippage') || s.includes('spread')) return T.costs;
  if (s.includes('coverage')) return T.coverage;
  if (s === 'n' || s.includes('trades')) return T.n;
  return undefined;
}

/* ── regime banner ─────────────────────────────────────────────────────────── */

function regimeClass(lbl: any): string {
  const s = String(lbl ?? '').toLowerCase();
  if (s.startsWith('trend')) return 'regime-trend';
  if (s === 'range' || s === 'low_vol') return 'regime-range';
  if (s === 'high_vol' || s === 'bear' || s === 'high_risk') return 'regime-high_risk';
  return 'regime-uncertain';
}

const SOURCE_CAPTION: Record<string, string> = {
  scheduler: 'live regime controller — the scheduler\'s current read of SPY trend and volatility',
  latest_lab_trade: 'regime tag carried by the most recent lab signal — a fallback, not a live reading',
  none: 'no regime reading is available right now',
};

function RegimeBanner({ data, err }: { data: any; err: Error | null }) {
  if (!data && !err) return <div className="skel" style={{ height: 64, margin: '0 0 14px' }} />;
  if (!data) return <div className="err-box">Regime service unavailable — {err?.message}</div>;
  const cur: Any = data.current && typeof data.current === 'object' ? data.current : {};
  const lbl = pick(cur, ['label', 'state', 'regime']);
  const source = String(cur.source ?? 'none');
  const asOf = pick(cur, ['as_of', 'updated_at', 'ts']);
  const raw: Any | null = cur.raw && typeof cur.raw === 'object' ? cur.raw : null;
  const why = raw ? pick(raw, ['why', 'reason', 'detail']) : undefined;
  const rows: Any[] = listOf(data, ['strategies']);
  const fav = rows.filter((r) => r.favoured_now === true);
  const avoid = rows.filter((r) => r.avoid_now === true);
  const all: string[] = Array.isArray(data.regimes) ? data.regimes.map(String) : [];
  return (
    <div className="panel">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 22px', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <L tip={T.regime}><span className="rm-now-lbl">Market regime</span></L>
          <span className={`regime-chip ${regimeClass(lbl)}`}>{lbl ? label(lbl) : 'unknown'}</span>
        </div>
        {why && <div className="muted" style={{ fontSize: 12.5, maxWidth: '58ch', lineHeight: 1.5 }}>{String(why)}</div>}
        <div className="faint" style={{ fontSize: 11, marginLeft: 'auto', textAlign: 'right', lineHeight: 1.5, maxWidth: '46ch' }}>
          <L tip={T.source}><span>source</span></L>: <b style={{ color: 'var(--text-dim)' }}>{label(source)}</b> — {SOURCE_CAPTION[source] ?? 'reported by the backend'}
          {asOf && <><br />as of {fmtEtDate(String(asOf))}</>}
        </div>
      </div>
      {(fav.length > 0 || avoid.length > 0) && (
        <div className="row" style={{ gap: '6px 16px', marginTop: 10, fontSize: 12 }}>
          {fav.length > 0 && (
            <span className="row" style={{ gap: 5 }}>
              <span className="faint">Historically strongest here:</span>
              {fav.map((r) => <span key={sid(r)} className="badge buy" style={{ fontSize: 9.5 }}>{r.name ?? sid(r)}</span>)}
            </span>
          )}
          {avoid.length > 0 && (
            <span className="row" style={{ gap: 5 }}>
              <span className="faint">Historically weakest here:</span>
              {avoid.map((r) => <span key={sid(r)} className="badge risk" style={{ fontSize: 9.5 }}>{r.name ?? sid(r)}</span>)}
            </span>
          )}
        </div>
      )}
      {all.length > 0 && (
        <p className="faint" style={{ fontSize: 11, marginTop: 8 }}>
          Lab regimes: {all.map(label).join(' · ')}. A strategy&apos;s best and worst regime come from its out-of-sample trades, not from opinion.
        </p>
      )}
    </div>
  );
}

/** Per-strategy fit with the current regime (from /api/lab/regimes). */
function RegimeFitTable({ data, onOpen, byId }: { data: any; onOpen: (s: Any) => void; byId: Record<string, Any> }) {
  const rows: Any[] = listOf(data, ['strategies']);
  const cur = pick(data?.current ?? {}, ['label', 'state', 'regime']);
  if (!rows.length || !cur) return null;
  const sorted = [...rows].sort((a, b) => {
    const ea = G.expectancy(mx(a.in_current_regime ?? {})).v ?? -1e9;
    const eb = G.expectancy(mx(b.in_current_regime ?? {})).v ?? -1e9;
    return eb - ea;
  });
  return (
    <>
      <div className="sect"><h2><L tip={T.regime}>Fit with the current regime</L></h2>
        <span className="meta">{label(cur)} · each strategy&apos;s recorded result in this regime, with n</span></div>
      <div className="tbl-wrap">
        <table className="tbl" style={{ minWidth: 720 }}>
          <thead><tr>
            <th className="l">Strategy</th><th className="l"><L tip={T.stage}>Stage</L></th>
            <th><L tip={T.expectancy}>Expectancy here</L></th><th><L tip={T.n}>n here</L></th>
            <th className="l"><L tip={T.regime}>Best regime</L></th><th className="l"><L tip={T.regime}>Worst regime</L></th><th className="l">Now</th>
          </tr></thead>
          <tbody>
            {sorted.map((r) => {
              const m = mx(r.in_current_regime ?? {});
              const n = G.n(m);
              const e = G.expectancy(m);
              const full = byId[sid(r)];
              return (
                <tr key={sid(r)} onClick={() => full && onOpen(full)} style={full ? undefined : { cursor: 'default' }}>
                  <td className="l"><b>{r.name ?? sid(r)}</b> <span className="badge neutral" style={{ marginLeft: 6, fontSize: 9 }}>{label(r.family)}</span></td>
                  <td className="l"><StagePill stage={String(r.stage ?? '')} /></td>
                  <td className={tone(e.v)}>{e.v === null ? <span className="faint">no trades here</span> : fmtSigned(e.v, 2, e.unit)}</td>
                  <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                  <td className="l">{label(r.best_regime) || '—'}</td>
                  <td className="l">{label(r.worst_regime) || '—'}</td>
                  <td className="l">
                    {r.favoured_now ? <span className="badge buy" style={{ fontSize: 9 }}>favoured</span>
                      : r.avoid_now ? <span className="badge risk" style={{ fontSize: 9 }}>avoid</span>
                        : <span className="faint">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ── strategy card + drawer ────────────────────────────────────────────────── */

function MarketChips({ s }: { s: Any }) {
  const markets: string[] = Array.isArray(s.markets) ? s.markets.map(String) : [];
  const best = String(pick(s, ['primary_market', 'best_market']) ?? '');
  const primary = markets.includes(best) ? best : '';
  const ordered = primary ? [primary, ...markets.filter((m) => m !== primary)] : markets;
  if (!ordered.length) return <span className="faint" style={{ fontSize: 11 }}>markets not set</span>;
  return (
    <div className="row" style={{ gap: 5 }}>
      {ordered.map((m) => m === 'options'
        ? <L key={m} tip={T.options}><span className="badge est">options · not available on data plan</span></L>
        : <span key={m} className={`badge ${m === primary ? 'buy' : 'neutral'}`} style={{ fontSize: 9.5, padding: '1px 8px' }}>{m}{m === primary ? ' · best' : ''}</span>)}
    </div>
  );
}

function StrategyCard({ s, onOpen }: { s: Any; onOpen: () => void }) {
  const m = mx(s);
  const n = G.n(m);
  const e = G.expectancy(m);
  const wr = G.winRate(m);
  const dd = G.maxDD(m).v;
  const ret = G.totalReturn(m);
  const act = activeCount(s);
  const tf = preferredTf(s);
  return (
    <div className="card" style={{ gap: 10, cursor: 'pointer' }} onClick={onOpen} role="button" tabIndex={0}
      onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); onOpen(); } }}
      aria-label={`Open ${s.name ?? sid(s)} details`}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 750, fontSize: 14.5, lineHeight: 1.25 }}>{s.name ?? sid(s)}</div>
          <div className="row" style={{ gap: 6, marginTop: 5 }}>
            <L tip={T.family}><span className="badge neutral" style={{ fontSize: 9.5, padding: '1px 8px' }}>{label(s.family) || 'family —'}</span></L>
            {tf && <L tip={T.timeframe}><span className="badge neutral" style={{ fontSize: 9.5, padding: '1px 8px' }}>{tf}</span></L>}
          </div>
        </div>
        <div style={{ marginLeft: 'auto', textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 5, alignItems: 'flex-end' }}>
          <StagePill stage={String(s.stage ?? '')} />
          {act !== null && act > 0 && (
            <L tip={T.active}><span className="st st-live">{act} active</span></L>
          )}
        </div>
      </div>
      <MarketChips s={s} />
      {n !== null && n < SMALL_N && (
        <L tip={T.smallsample}><span className="badge warn" style={{ alignSelf: 'flex-start' }}>SMALL SAMPLE · n={fmtInt(n)}</span></L>
      )}
      {n === null && <span className="badge warn" style={{ alignSelf: 'flex-start' }}>NO DECIDED TRADES YET</span>}
      <div className="rm-grid" style={{ padding: 0, gap: '2px 14px' }}>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.expectancy}>Expectancy</L></span><b className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</b></div>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.pf}>Profit factor</L></span><b>{fmt(G.pf(m))}</b></div>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.winrate}>Win / loss</L></span><b>{wr === null ? '—' : `${fmt(wr, 0)}% / ${fmt(100 - wr, 0)}%`}</b></div>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.return}>Total return</L></span><b className={tone(ret ?? G.totalR(m))}>{ret === null ? (G.totalR(m) === null ? '—' : fmtSigned(G.totalR(m), 2, 'R')) : fmtSigned(ret, 1, '%')}</b></div>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.maxdd}>Max drawdown</L></span><b className={dd !== null ? 'neg' : undefined}>{ddText(m)}</b></div>
        <div className="rm-kv sm" style={{ padding: '3px 0' }}><span><L tip={T.n}>Decided trades</L></span><b>{fmtInt(n)}</b></div>
      </div>
      <div className="faint" style={{ fontSize: 10.5, display: 'flex', justifyContent: 'space-between' }}>
        <span>{s.hold ? label(s.hold) : ''}{s.hold && s.stop_method ? ' · ' : ''}{s.stop_method ? `${label(s.stop_method)} stop` : ''}</span>
        <span>open details ›</span>
      </div>
    </div>
  );
}

function TradesTable({ trades }: { trades: Any[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!trades?.length) return <p className="muted" style={{ fontSize: 12.5 }}>No recorded trades yet.</p>;
  return (
    <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
      <table className="tbl" style={{ minWidth: 720 }}>
        <thead><tr>
          <th className="l">Signal time</th><th className="l">Symbol</th><th className="l">Dir</th>
          <th><L tip={T.cohort}>Cohort</L></th><th>Entry</th><th>Stop</th><th>Exit</th>
          <th><L tip={T.rmult}>R</L></th><th className="l">Result</th><th className="l"><L tip={T.regime}>Regime</L></th>
        </tr></thead>
        <tbody>
          {trades.slice(0, 40).map((t, i) => {
            const r = num(t.r_multiple);
            const reasons: string[] = Array.isArray(t.reasons) ? t.reasons.map(String) : [];
            const res = String(t.result ?? '').toUpperCase();
            const resCls = res.startsWith('WIN') ? 'pos' : res.startsWith('LOSS') ? 'neg' : 'dim';
            return (
              <Fragment key={t.id ?? i}>
                <tr onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
                  <td className="l" style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>{fmtEtDate(t.signal_time ?? t.entry_time)}</td>
                  <td className="l"><span className="sym">{t.symbol ?? '—'}</span> <span className="faint" style={{ fontSize: 10 }}>{t.timeframe ?? ''}</span></td>
                  <td className="l">{t.direction ?? '—'}</td>
                  <td>{t.cohort ?? '—'}</td>
                  <td>{fmt(t.entry_price)}</td>
                  <td>{fmt(t.stop_price)}</td>
                  <td>{fmt(t.exit_price)}</td>
                  <td className={tone(r)}>{r === null ? '—' : fmtSigned(r, 2, 'R')}</td>
                  <td className={`l ${resCls}`}>{res || '—'} <span className="faint" style={{ fontSize: 10 }}>{t.exit_reason ? label(t.exit_reason) : ''}</span></td>
                  <td className="l">{label(t.regime) || '—'}</td>
                </tr>
                {open === i && (
                  <tr style={{ cursor: 'default' }}>
                    <td colSpan={10} className="l" style={{ whiteSpace: 'normal', background: 'rgba(94,118,169,.05)' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14, fontSize: 12.5, lineHeight: 1.55 }}>
                        <div>
                          <div className="rm-now-lbl">Why it fired</div>
                          {reasons.length ? <ul className="rm-why" style={{ padding: '4px 0 0 16px' }}>{reasons.map((x, j) => <li key={j}>{x}</li>)}</ul>
                            : <span className="faint">no reasons recorded</span>}
                        </div>
                        <div>
                          <div className="rm-now-lbl"><L tip={T.invalidation}>Invalidation</L></div>
                          <div className="muted" style={{ marginTop: 4 }}>{t.invalidation || <span className="faint">not recorded</span>}</div>
                          <div className="row" style={{ gap: 14, marginTop: 8, fontSize: 11.5 }}>
                            <span><L tip={T.mfe}>MFE / MAE</L>: <b className="mono">{fmtSigned(t.mfe_r, 2, 'R')} / {fmtSigned(t.mae_r, 2, 'R')}</b></span>
                            <span><L tip={T.session}>Session</L>: <b>{label(t.session_bucket) || '—'}</b></span>
                            {num(t.confidence) !== null && <span>Confidence: <b className="mono">{fmt(t.confidence, 0)}</b></span>}
                            {num(t.return_pct) !== null && <span>Return: <b className={`mono ${tone(t.return_pct) ?? ''}`}>{fmtSigned(t.return_pct, 2, '%')}</b></span>}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const SPLIT_TIP: Record<string, string> = {
  train: 'Train — the period the parameters were tuned on. Flattering by construction; it never decides anything.',
  validation: 'Validation — unseen data used to choose between candidate parameter sets.',
  oos: 'Out-of-sample — untouched data evaluated after the parameters were locked. The only split that predicts anything.',
  forward: 'Forward — paper trades recorded live after the strategy was frozen. The most honest evidence, and usually the smallest sample.',
  all: 'All — the whole period in one run, without a train/test split.',
};

/** by_market → one row per market × timeframe, one cell per evidence split. */
function ByMarketTable({ bm }: { bm: any }) {
  if (!bm || typeof bm !== 'object' || Array.isArray(bm)) return null;
  const splits = ['train', 'validation', 'oos', 'forward'];
  const rows: { market: string; tf: string; cell: Any }[] = [];
  const unavailable: [string, string][] = [];
  for (const [mk, v] of Object.entries(bm)) {
    const o = v as Any;
    if (!o || typeof o !== 'object') continue;
    if (typeof o.status === 'string' && !o.timeframes) { unavailable.push([mk, o.status]); continue; }
    const tfs = o.timeframes && typeof o.timeframes === 'object' ? o.timeframes : {};
    for (const [tf, cell] of Object.entries(tfs)) rows.push({ market: mk, tf, cell: (cell ?? {}) as Any });
  }
  if (!rows.length && !unavailable.length) return null;
  const cellText = (c: any) => {
    if (!c || typeof c !== 'object') return <span className="faint">—</span>;
    const m = mx(c);
    const n = G.n(m);
    const e = G.expectancy(m);
    return (
      <>
        <span className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</span>{' '}
        <span className={n !== null && n < SMALL_N ? 'badge warn' : 'faint'} style={{ fontSize: 9.5, padding: '0 5px' }}>n={fmtInt(n)}</span>
      </>
    );
  };
  return (
    <div className="panel">
      <h3><L tip={T.split}>By market and timeframe</L> <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· expectancy with n, one column per evidence split</span></h3>
      <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
        <table className="tbl" style={{ minWidth: 640 }}>
          <thead><tr>
            <th className="l">Market</th><th className="l"><L tip={T.timeframe}>Timeframe</L></th>
            {splits.map((s) => <th key={s}><L tip={SPLIT_TIP[s]}>{label(s)}</L></th>)}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.market}/${r.tf}`} style={{ cursor: 'default' }}>
                <td className="l">{r.market}</td>
                <td className="l">{r.tf}</td>
                {splits.map((s) => <td key={s}>{cellText(r.cell[s])}</td>)}
              </tr>
            ))}
            {unavailable.map(([mk, st]) => (
              <tr key={mk} style={{ cursor: 'default' }}>
                <td className="l">{mk}</td>
                <td className="l" colSpan={splits.length + 1}><L tip={T.options}><span className="badge est">{st}</span></L></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Table of run summaries (robustness perturbations, or every stored result run). */
function RunsTable({ runs, title, tip, note }: { runs: Any[]; title: string; tip: string; note?: string }) {
  if (!runs?.length) return null;
  return (
    <div className="panel">
      <h3><L tip={tip}>{title}</L> <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· {runs.length} runs</span></h3>
      {note && <p className="muted" style={{ fontSize: 12, marginBottom: 8, lineHeight: 1.55 }}>{note}</p>}
      <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
        <table className="tbl" style={{ minWidth: 860 }}>
          <thead><tr>
            <th className="l">Kind</th><th className="l"><L tip={T.split}>Split</L></th><th className="l">Market / TF</th><th className="l">Period</th><th className="l">Params</th>
            <th><L tip={T.n}>n</L></th><th><L tip={T.expectancy}>Expectancy</L></th><th><L tip={T.pf}>PF</L></th>
            <th><L tip={T.maxdd}>Max DD</L></th><th><L tip={T.sharpe}>Sharpe</L></th><th className="l"><L tip={T.confidence}>Confidence</L></th>
          </tr></thead>
          <tbody>
            {runs.slice(0, 60).map((r, i) => {
              const m = mx(r);
              const n = G.n(m);
              const e = G.expectancy(m);
              const params = r.params && typeof r.params === 'object'
                ? Object.entries(r.params).map(([k, v]) => `${k}=${typeof v === 'number' ? fmt(v, Number.isInteger(v) ? 0 : 2) : String(v)}`).join('  ')
                : '';
              return (
                <tr key={r.run_id ?? i} style={{ cursor: 'default' }}>
                  <td className="l">{label(r.kind) || '—'}</td>
                  <td className="l">{label(r.split) || '—'}</td>
                  <td className="l">{[r.market, r.timeframe].filter(Boolean).join(' / ') || '—'}</td>
                  <td className="l" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{r.period_start || r.period_end ? `${r.period_start ?? ''} → ${r.period_end ?? ''}` : '—'}</td>
                  <td className="l" style={{ fontFamily: 'var(--mono)', fontSize: 10.5, maxWidth: 240, whiteSpace: 'normal' }}>{params || '—'}</td>
                  <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                  <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</td>
                  <td>{fmt(G.pf(m))}</td>
                  <td className="neg">{ddText(m)}</td>
                  <td>{fmt(G.sharpe(m))}</td>
                  <td className="l"><ConfBadge label={confidenceLabel(r, n)} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Forward (paper / live) record — a separate cohort, never blended with the backtest. */
function ForwardPanel({ f }: { f: any }) {
  if (!f || typeof f !== 'object') return null;
  const m = mx(f);
  const n = G.n(m);
  const e = G.expectancy(m);
  const wr = G.winRate(m);
  const wl = G.wilson(m);
  const hold = num(f.avg_hold_minutes);
  return (
    <div className="panel">
      <h3><L tip={T.forward}>Forward paper record</L> <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· separate cohort, never blended with the backtest</span></h3>
      <SmallSampleBanner n={n} />
      <div className="kv-grid" style={{ margin: '6px 0 0' }}>
        <Metric label="Expectancy" tip={T.expectancy} value={fmtSigned(e.v, 2, e.unit || 'R')} n={n} tn={tone(e.v)} small />
        <Metric label="Profit factor" tip={T.pf} value={fmt(G.pf(m))} n={n} small />
        <Metric label="Win rate" tip={T.winrate} value={wr === null ? '—' : fmt(wr, 0) + '%'} n={n} small />
        <Metric label="Wilson LB" tip={T.wilson} value={pctText(wl)} n={n} small />
        <Metric label="Total R" tip={T.rmult} value={fmtSigned(f.total_r, 2, 'R')} n={n} tn={tone(f.total_r)} small />
        <Metric label="Open now" tip={T.active} value={fmtInt(f.open)} n={n} small />
        <Metric label="Avg hold" tip={T.hold} value={hold === null ? '—' : `${fmt(hold, 0)} min`} n={n} small />
      </div>
      {f.warning && <p className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>{String(f.warning)}</p>}
    </div>
  );
}

function MonthlyTable({ monthly }: { monthly: any }) {
  if (!monthly || typeof monthly !== 'object' || Array.isArray(monthly)) return null;
  const all = Object.entries(monthly).filter(([, v]) => v && typeof v === 'object');
  const entries = [...all].sort(([a], [b]) => b.localeCompare(a)).slice(0, 24);
  if (!entries.length) return null;
  const pos = all.filter(([, v]) => (num((v as Any).r) ?? 0) > 0).length;
  return (
    <div className="panel">
      <h3><L tip={T.monthly}>Monthly results</L> <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· {pos} of {all.length} months positive{all.length > entries.length ? ` · latest ${entries.length} shown` : ''}</span></h3>
      <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
        <table className="tbl" style={{ minWidth: 380 }}>
          <thead><tr><th className="l">Month</th><th><L tip={T.n}>n</L></th><th><L tip={T.rmult}>Total R</L></th><th><L tip={T.return}>Return</L></th></tr></thead>
          <tbody>
            {entries.map(([k, v]) => {
              const o = v as Any;
              const r = num(o.r);
              const p = num(o.pct);
              return (
                <tr key={k} style={{ cursor: 'default' }}>
                  <td className="l mono">{k}</td>
                  <td>{fmtInt(o.n)}</td>
                  <td className={tone(r)}>{fmtSigned(r, 2, 'R')}</td>
                  <td className={tone(p)}>{p === null ? '—' : fmtSigned(p, 2, '%')}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function decidedTrades(trades: Any[]): Any[] {
  return trades.filter((t) => num(t?.r_multiple) !== null)
    .sort((a, b) => String(b.signal_time ?? '').localeCompare(String(a.signal_time ?? '')));
}
function streakFromTrades(trades: Any[]): { count: number; kind: string; n: number } | null {
  const d = decidedTrades(trades);
  if (!d.length) return null;
  const side = (r: number) => (r > 0 ? 'wins' : r < 0 ? 'losses' : 'flat');
  const kind = side(num(d[0].r_multiple) ?? 0);
  let count = 0;
  for (const t of d) { if (side(num(t.r_multiple) ?? 0) !== kind) break; count++; }
  return { count, kind, n: d.length };
}
function recentStats(trades: Any[]): { avgWin: number | null; avgLoss: number | null; wins: number; losses: number; n: number } {
  const d = decidedTrades(trades).map((t) => num(t.r_multiple) ?? 0);
  const w = d.filter((r) => r > 0);
  const l = d.filter((r) => r < 0);
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  return { avgWin: mean(w), avgLoss: mean(l), wins: w.length, losses: l.length, n: d.length };
}

function StrategyDrawer({ base, onClose }: { base: Any; onClose: () => void }) {
  const id = sid(base);
  const [detail, dErr, dLoading] = useGet<Any>(id ? `/api/lab/strategies/${encodeURIComponent(id)}` : null);
  const s: Any = useMemo(() => ({ ...base, ...(detail && !Array.isArray(detail) ? (detail.strategy ?? detail) : {}) }), [base, detail]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const m = mx(s);
  const head: Any = s.headline && typeof s.headline === 'object' ? s.headline : {};
  const n = G.n(m);
  const trades: Any[] = listOf(pick(s, ['recent_trades', 'trades', 'last_trades']), []);
  const rs = recentStats(trades);
  const e = G.expectancy(m);
  const awM = G.avgWin(m);
  const alM = G.avgLoss(m);
  const aw = awM.v !== null ? { v: awM.v, unit: awM.unit, n: G.wins(m) ?? n, recent: false } : { v: rs.avgWin, unit: 'R', n: rs.wins, recent: true };
  const al = alM.v !== null ? { v: alM.v, unit: alM.unit, n: G.losses(m) ?? n, recent: false } : { v: rs.avgLoss, unit: 'R', n: rs.losses, recent: true };
  const wr = G.winRate(m);
  const ret = G.totalReturn(m);
  const totR = G.totalR(m);
  const sh = G.sharpe(m);
  const so = G.sortino(m);
  const rr = G.avgRR(m);
  const stk = streakFromTrades(trades);
  const streakRaw = G.streak(m);
  const plural = (c: number, one: string, many: string) => `${c} ${c === 1 ? one : many}`;
  const streakText = typeof streakRaw === 'number'
    ? (streakRaw > 0 ? plural(streakRaw, 'win', 'wins') : streakRaw < 0 ? plural(Math.abs(streakRaw), 'loss', 'losses') : 'none')
    : stk ? (stk.kind === 'flat' ? 'flat' : plural(stk.count, stk.kind === 'wins' ? 'win' : 'loss', stk.kind)) : '—';
  const curve = curveOf(s);
  const ddCurve = num(s.max_drawdown_from_curve);
  const conf = confidenceLabel(s, n);
  const last = lastSignal(s);
  const act = activeCount(s);
  const warnings: string[] = Array.isArray(s.warnings) ? s.warnings.map(String) : [];
  const costs = pick(head, ['costs']) ?? pick(s, ['costs']);
  const coverage = pick(head, ['data_coverage', 'coverage']) ?? pick(s, ['data_coverage', 'coverage']);
  const params = pick(s, ['params', 'parameters']);
  const grid: Any | null = s.param_grid && typeof s.param_grid === 'object' && !Array.isArray(s.param_grid) ? s.param_grid : null;
  const judged = [
    head.split ? `${label(head.split)} split` : '',
    [head.market, head.timeframe].filter(Boolean).join(' / '),
    head.period_start || head.period_end ? `${head.period_start ?? ''} → ${head.period_end ?? ''}` : '',
  ].filter(Boolean).join(' · ');
  const robustness: Any[] = listOf(pick(s, ['robustness', 'robustness_report']), []);
  const runs: Any[] = listOf(pick(s, ['runs']), []);
  const mc = pick(s, ['monte_carlo', 'montecarlo', 'mc']);
  const byRegime = pick(s, ['by_regime']) ?? s.breakdowns?.regime;
  const bySession = pick(s, ['by_session']) ?? s.breakdowns?.session;
  const bySymbol = pick(s, ['by_symbol']) ?? s.breakdowns?.symbol;

  return (
    <>
      <div className="drawer-veil" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={`${s.name ?? id} details`}>
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 8, marginBottom: 4 }}>
              <StagePill stage={String(s.stage ?? '')} />
              <L tip={T.family}><span className="badge neutral">{label(s.family) || 'family —'}</span></L>
              {s.category && <span className="badge neutral">{label(s.category)}</span>}
              <L tip={T.version}><span className="badge neutral mono">v{s.version ?? '?'}</span></L>
              {act !== null && act > 0 && <L tip={T.active}><span className="st st-live">{act} active</span></L>}
            </div>
            <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: -0.2 }}>{s.name ?? id}</div>
            <div className="faint mono" style={{ fontSize: 11 }}>{id}</div>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">Close ✕</button>
        </div>

        <SmallSampleBanner n={n} />
        {dLoading && !detail && <div className="skel" style={{ height: 36, margin: '6px 0' }} />}
        {dErr && <p className="faint" style={{ fontSize: 11, margin: '4px 0 8px' }}>Detail endpoint unavailable — showing the summary record only.</p>}
        {warnings.length > 0 && (
          <div className="panel" style={{ borderColor: 'rgba(251,191,36,.3)' }}>
            <h3 style={{ color: 'var(--warn)' }}>Data warnings</h3>
            <ul className="rm-why" style={{ padding: '0 0 0 16px' }}>
              {warnings.slice(0, 12).map((w, i) => <li key={i}>{w}</li>)}
            </ul>
            {warnings.length > 12 && <p className="faint" style={{ fontSize: 11 }}>+{warnings.length - 12} more</p>}
          </div>
        )}

        <div className="panel">
          <h3><L tip={T.hypothesis}>Hypothesis</L></h3>
          <p style={{ fontSize: 13.5, lineHeight: 1.65 }}>{s.hypothesis || <span className="faint">No hypothesis recorded.</span>}</p>
          {s.stage_reason && (
            <p className="muted" style={{ fontSize: 12.5, marginTop: 8, lineHeight: 1.6, whiteSpace: 'pre-line' }}>
              <b style={{ color: 'var(--text)' }}>Why it is in {label(s.stage)}:</b>{'\n'}{s.stage_reason}
            </p>
          )}
          <div className="row" style={{ gap: 8, marginTop: 10 }}>
            <MarketChips s={s} />
            {Array.isArray(s.timeframes) && s.timeframes.length > 0 && (
              <L tip={T.timeframe}><span className="badge neutral" style={{ fontSize: 9.5 }}>timeframes: {s.timeframes.join(', ')}</span></L>
            )}
            {s.hold && <L tip={T.hold}><span className="badge neutral" style={{ fontSize: 9.5 }}>{label(s.hold)}</span></L>}
            {s.stop_method && <L tip={T.stopmethod}><span className="badge neutral" style={{ fontSize: 9.5 }}>{label(s.stop_method)} stop</span></L>}
            {num(s.optimization_count) !== null && <L tip={T.optimizations}><span className="badge neutral" style={{ fontSize: 9.5 }}>{fmtInt(s.optimization_count)} re-tunes</span></L>}
            {num(s.runs_count) !== null && <span className="badge neutral" style={{ fontSize: 9.5 }}>{fmtInt(s.runs_count)} stored runs</span>}
          </div>
        </div>

        <div className="subhead">Results
          <span className="faint" style={{ marginLeft: 8, letterSpacing: 0, textTransform: 'none', fontWeight: 500, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            confidence: <ConfBadge label={conf} />{judged && <span>· judged on {judged}</span>}
          </span>
        </div>
        <div className="kv-grid">
          <Metric label="Expectancy" tip={T.expectancy} value={fmtSigned(e.v, 2, e.unit)} n={n} tn={tone(e.v)} />
          <Metric label="Profit factor" tip={T.pf} value={fmt(G.pf(m))} n={n} />
          <Metric label="Win / loss" tip={T.winrate} value={wr === null ? '—' : `${fmt(wr, 0)}% / ${fmt(100 - wr, 0)}%`} n={n} />
          <Metric label={aw.recent ? 'Avg winner (recent)' : 'Avg winner'} tip={T.avgwin} value={fmtSigned(aw.v, 2, aw.unit)} n={aw.n} tn="pos" />
          <Metric label={al.recent ? 'Avg loser (recent)' : 'Avg loser'} tip={T.avgloss} value={al.v === null ? '—' : fmtSigned(-Math.abs(al.v), 2, al.unit)} n={al.n} tn="neg" />
          <Metric label="Total return" tip={T.return} value={ret === null ? (totR === null ? '—' : fmtSigned(totR, 2, 'R')) : fmtSigned(ret, 1, '%')} n={n} tn={tone(ret ?? totR)} />
          <Metric label="Max drawdown" tip={T.maxdd} value={ddText(m)} n={n} tn={G.maxDD(m).v !== null ? 'neg' : undefined} />
          {sh !== null && <Metric label="Sharpe" tip={T.sharpe} value={fmt(sh)} n={n} tn={tone(sh)} />}
          {so !== null && <Metric label="Sortino" tip={T.sortino} value={fmt(so)} n={n} tn={tone(so)} />}
          <Metric label="Avg R:R planned" tip={T.rr} value={rr === null ? '—' : `${fmt(rr, 2)}:1`} n={n} />
          <Metric label="Streak" tip={T.streak} value={streakText} n={stk ? stk.n : n} />
          <Metric label="Composite" tip={T.composite} value={fmt(G.composite(m), 2)} n={n} />
          {G.consistency(m) !== null && <Metric label="Consistency" tip={T.consistency} value={pctText(G.consistency(m))} n={G.months(m) ?? n} />}
          {G.wilson(m) !== null && <Metric label="Wilson LB" tip={T.wilson} value={pctText(G.wilson(m))} n={n} />}
        </div>
        {(G.maxWinStreak(m) !== null || G.maxLossStreak(m) !== null) && (
          <p className="faint" style={{ fontSize: 11.5, marginTop: -6 }}>
            Longest win streak {fmtInt(G.maxWinStreak(m))} · longest losing streak {fmtInt(G.maxLossStreak(m))} · n={fmtInt(n)}
          </p>
        )}

        <div className="kv-grid">
          <div className="kv"><div className="k">Best market</div><div className="v">{label(s.best_market) || '—'}</div></div>
          <div className="kv"><div className="k"><L tip={T.timeframe}>Best timeframe</L></div><div className="v">{label(s.best_timeframe) || '—'}</div></div>
          <div className="kv"><div className="k"><L tip={T.regime}>Best regime</L></div><div className="v" style={{ color: 'var(--buy)' }}>{label(s.best_regime) || '—'}</div></div>
          <div className="kv"><div className="k"><L tip={T.regime}>Worst regime</L></div><div className="v" style={{ color: 'var(--risk)' }}>{label(s.worst_regime) || '—'}</div></div>
          <div className="kv"><div className="k"><L tip={T.active}>Active signals</L></div><div className="v">{act === null ? '—' : fmtInt(act)}</div></div>
          <div className="kv"><div className="k">Last signal</div><div className="v" style={{ fontSize: 12.5 }}>{last ? fmtEtDate(last) : 'none yet'}</div></div>
        </div>

        <div className="panel">
          <h3><L tip={T.equity}>Equity curve</L>
            <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500, marginLeft: 8 }}>
              · cumulative R after each closed trade · {curve.length ? `${curve.length} trades` : 'none yet'}
              {ddCurve !== null && <> · deepest dip {fmt(-Math.abs(ddCurve), 2)}R</>}
            </span>
          </h3>
          <Sparkline curve={curve} height={110} width={700} />
        </div>

        <ForwardPanel f={s.forward} />
        <ByMarketTable bm={s.by_market} />
        <RunsTable runs={robustness} title="Robustness" tip={T.robustness}
          note="The same strategy re-run with nudged parameters on the train split. A real edge should not collapse when a threshold moves a little." />
        <MonteCarloTable mc={mc} />

        <BreakdownTable title="By regime" tip={T.regime} groups={byRegime} />
        <BreakdownTable title="By session" tip={T.session} groups={bySession} />
        <BreakdownTable title="By symbol" tip={T.symbol} groups={bySymbol} />
        <MonthlyTable monthly={s.monthly} />
        <RunsTable runs={runs} title="All stored result runs" tip={T.walkforward}
          note="Every backtest and walk-forward run kept for this strategy, newest first. Results are never overwritten; a re-run is a new row." />

        <div className="panel">
          <h3>Recent trades <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· newest first · click a row for reasons and invalidation</span></h3>
          <TradesTable trades={trades} />
        </div>

        {(params || grid || costs || coverage) && (
          <div className="panel">
            <h3>Configuration &amp; data</h3>
            {params && <><div className="subhead" style={{ margin: '4px 0 8px' }}>Parameters</div><KVGrid obj={params} /></>}
            {grid && (
              <>
                <div className="subhead" style={{ margin: '12px 0 8px' }}><L tip={T.optimizations}>Parameter grid searched</L></div>
                <div className="rm-grid" style={{ padding: 0 }}>
                  {Object.entries(grid).map(([k, v]) => (
                    <div className="rm-kv sm" key={k} style={{ padding: '4px 0' }}>
                      <span>{label(k)}</span><b style={{ fontSize: 11 }}>{Array.isArray(v) ? v.map(String).join(', ') : String(v)}</b>
                    </div>
                  ))}
                </div>
              </>
            )}
            {costs && <><div className="subhead" style={{ margin: '12px 0 8px' }}><L tip={T.costs}>Costs</L></div><KVGrid obj={costs} /></>}
            {coverage && <><div className="subhead" style={{ margin: '12px 0 8px' }}><L tip={T.coverage}>Data coverage</L></div><AutoSection data={coverage} tipFor={tipForKey} depth={1} /></>}
          </div>
        )}
        <p className="disclaimer">Research and paper results only. Nothing here is a recommendation, and no orders are placed.</p>
      </aside>
    </>
  );
}

/* ── tabs ──────────────────────────────────────────────────────────────────── */

function Overview({ strategies, stages, regimes, regErr, loading, onOpen }: {
  strategies: Any[]; stages: string[]; regimes: any; regErr: Error | null; loading: boolean; onOpen: (s: Any) => void;
}) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const st of stages) c[st] = 0;
    for (const s of strategies) { const k = String(s.stage ?? 'RESEARCH'); c[k] = (c[k] ?? 0) + 1; }
    return c;
  }, [strategies, stages]);
  /* The composite is a rank-average over the whole field, produced at the end
     of a full backtest run.  Until then every strategy carries 0/null, and
     ranking by it would crown an arbitrary — possibly losing — strategy.  Fall
     back to out-of-sample expectancy and say so. */
  const composites = useMemo(
    () => strategies.map((s) => G.composite(mx(s))).filter((c) => c !== null && c > 0),
    [strategies]);
  const scored = composites.length > 0;
  const top = useMemo(() => {
    const rows = strategies.map((s) => ({ s, m: mx(s) })).filter((x) => G.n(x.m) !== null);
    const key = scored
      ? (x: { m: Any }) => G.composite(x.m) ?? -1e9
      : (x: { m: Any }) => G.expectancy(x.m).v ?? -1e9;
    return rows.filter((x) => (scored ? G.composite(x.m) !== null : G.expectancy(x.m).v !== null))
      .sort((a, b) => key(b) - key(a)).slice(0, 5);
  }, [strategies, scored]);
  const byId = useMemo(() => Object.fromEntries(strategies.map((s) => [sid(s), s])), [strategies]);
  const total = strategies.length;
  const failed = counts.FAILED ?? 0;
  return (
    <>
      <RegimeBanner data={regimes} err={regErr} />
      <div className="panel">
        <h3>How to read this page</h3>
        <p className="muted" style={{ fontSize: 13, lineHeight: 1.7, maxWidth: '90ch' }}>
          Every strategy here starts as a written hypothesis and has to earn its way through the funnel below
          with evidence. The two numbers that matter are <L tip={T.expectancy}>expectancy</L> (average result per
          trade, in <L tip={T.rmult}>R</L>) and <L tip={T.maxdd}>max drawdown</L> (the worst losing stretch); <L tip={T.winrate}>win rate</L> is
          shown but never used to rank, because a strategy can win often and still lose money. Every figure is
          followed by its <L tip={T.n}>n</L> — the number of trades behind it — and anything under {SMALL_N} trades
          is flagged as a small sample. Hover or focus any dotted term for a plain-English explanation. Green never
          means guaranteed profit; these are paper results.
        </p>
      </div>

      <div className="sect"><h2><L tip={T.funnel}>Stage funnel</L></h2>
        <span className="meta">{total} strategies registered{failed ? ` · ${failed} failed on evidence` : ''}</span></div>
      {loading && !total ? <div className="skel" style={{ height: 84 }} /> : (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.max(1, stages.length)}, minmax(0, 1fr))`, gap: 6 }}>
          {stages.map((st, i) => {
            const cls = stageClass(st);
            const color = cls === 'buy' ? 'var(--buy)' : cls === 'risk' ? 'var(--risk)' : cls === 'early' ? 'var(--early)' : cls === 'st-paper' ? 'var(--accent)' : 'var(--text-dim)';
            return (
              <div key={st} className="kv" style={{ textAlign: 'center', padding: '10px 6px', borderColor: counts[st] ? 'var(--line)' : 'var(--line-soft)', opacity: counts[st] ? 1 : 0.65 }}>
                <div className="k" style={{ fontSize: 8.5, letterSpacing: 0.6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={label(st)}>
                  {i < stages.length - 1 && i > 0 ? '→ ' : ''}{label(st)}
                </div>
                <div className="v" style={{ fontSize: 22, color }}>{counts[st] ?? 0}</div>
              </div>
            );
          })}
        </div>
      )}

      <div className="sect">
        <h2><L tip={T.composite}>{scored ? 'Top 5 by composite' : 'Top 5 by out-of-sample expectancy'}</L></h2>
        <span className="meta">{scored
          ? 'composite excludes win rate on purpose · confidence reflects sample size, not profit'
          : 'composite scores need a completed run across the whole field · ranked by expectancy until then'}</span></div>
      {!top.length ? (
        <div className="tbl-wrap"><div className="empty"><b>No scored strategies yet</b>
          Composite scores appear once a strategy has a completed backtest run with decided trades.</div></div>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 760 }}>
            <thead><tr>
              <th className="l">#</th><th className="l">Strategy</th><th className="l"><L tip={T.stage}>Stage</L></th>
              <th><L tip={T.composite}>Composite</L></th><th><L tip={T.expectancy}>Expectancy</L></th>
              <th><L tip={T.maxdd}>Max DD</L></th><th><L tip={T.n}>n</L></th><th className="l"><L tip={T.confidence}>Confidence</L></th>
            </tr></thead>
            <tbody>
              {top.map(({ s, m }, i) => {
                const n = G.n(m);
                const e = G.expectancy(m);
                return (
                  <tr key={sid(s)} onClick={() => onOpen(s)}>
                    <td className="l faint">{i + 1}</td>
                    <td className="l"><b>{s.name ?? sid(s)}</b> <span className="badge neutral" style={{ marginLeft: 6, fontSize: 9 }}>{label(s.family)}</span></td>
                    <td className="l"><StagePill stage={String(s.stage ?? '')} /></td>
                    <td><b>{scored ? fmt(G.composite(m), 2) : <span className="faint">not scored yet</span>}</b></td>
                    <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</td>
                    <td className={G.maxDD(m).v !== null ? 'neg' : ''}>{ddText(m)}</td>
                    <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                    <td className="l"><ConfBadge label={confidenceLabel(s, n)} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <RegimeFitTable data={regimes} onOpen={onOpen} byId={byId} />
    </>
  );
}

function Strategies({ strategies, stages, loading, err, onOpen }: { strategies: Any[]; stages: string[]; loading: boolean; err: Error | null; onOpen: (s: Any) => void }) {
  const [family, setFamily] = useState('');
  const [stage, setStage] = useState('');
  const families = useMemo(() => Array.from(new Set(strategies.map((s) => String(s.family ?? '')).filter(Boolean))).sort(), [strategies]);
  const shown = strategies.filter((s) => (!family || s.family === family) && (!stage || s.stage === stage));
  if (err && !strategies.length) return <div className="err-box">Could not load strategies — {err.message}</div>;
  if (loading && !strategies.length) return <div className="cards">{[0, 1, 2].map((i) => <div key={i} className="skel" style={{ height: 260 }} />)}</div>;
  if (!strategies.length) return (
    <div className="tbl-wrap"><div className="empty"><b>No strategies registered yet</b>
      The lab registers strategies from backend/app/lab/strategies on startup. Once the first backtest completes they appear here.</div></div>
  );
  return (
    <>
      <div className="row" style={{ gap: 8, marginBottom: 12 }}>
        <button className={`tab ${!family ? 'on' : ''}`} onClick={() => setFamily('')}>All families</button>
        {families.map((f) => <button key={f} className={`tab ${family === f ? 'on' : ''}`} onClick={() => setFamily(f)}>{label(f)}</button>)}
        <span className="faint" style={{ margin: '0 4px' }}>|</span>
        <button className={`tab ${!stage ? 'on' : ''}`} onClick={() => setStage('')}>All stages</button>
        {stages.filter((st) => strategies.some((s) => s.stage === st)).map((st) => (
          <button key={st} className={`tab ${stage === st ? 'on' : ''}`} onClick={() => setStage(st)}>{label(st)}</button>
        ))}
      </div>
      <p className="faint" style={{ fontSize: 11.5, marginBottom: 6 }}>
        {shown.length} of {strategies.length} shown · each card keeps six numbers on its face; everything else is one click away in the drawer.
      </p>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', margin: '8px 0 20px' }}>
        {shown.map((s) => <StrategyCard key={sid(s)} s={s} onOpen={() => onOpen(s)} />)}
      </div>
    </>
  );
}

const SORTS: { key: string; label: string; tip: string }[] = [
  { key: 'composite', label: 'Composite', tip: T.composite },
  { key: 'expectancy', label: 'Expectancy', tip: T.expectancy },
  { key: 'profit_factor', label: 'Profit factor', tip: T.pf },
  { key: 'max_drawdown', label: 'Max DD', tip: T.maxdd + ' Sorting puts the shallowest drawdown first.' },
  { key: 'sharpe', label: 'Sharpe', tip: T.sharpe },
  { key: 'sortino', label: 'Sortino', tip: T.sortino },
  { key: 'trades', label: 'Trades', tip: T.n },
  { key: 'consistency', label: 'Consistency', tip: T.consistency },
  { key: 'stocks', label: 'Stocks', tip: 'Expectancy of the most honest stored run on the stocks market only.' },
  { key: 'crypto', label: 'Crypto', tip: 'Expectancy of the most honest stored run on the crypto market only.' },
  { key: 'etf', label: 'ETF', tip: 'Expectancy of the most honest stored run on the ETF market only.' },
];
const SPLIT_ORDER = ['oos', 'validation', 'all', 'train'];

/** Expectancy for one market: the server's field first, else derived from by_market cells. */
function marketExp(row: Any, mkt: string): MV {
  const direct = num(row.by_market_expectancy?.[mkt]);
  if (direct !== null) return { v: direct, unit: 'R' };
  const tfs = row.by_market?.[mkt]?.timeframes;
  if (tfs && typeof tfs === 'object') {
    for (const split of SPLIT_ORDER) {
      for (const cell of Object.values(tfs) as Any[]) {
        const e = G.expectancy(mx(cell?.[split] ?? {}));
        if (e.v !== null) return e;
      }
    }
  }
  return { v: null, unit: '' };
}
function sortValue(row: Any, key: string): number | null {
  const m = mx(row);
  switch (key) {
    case 'composite': return G.composite(m);
    case 'expectancy': return G.expectancy(m).v;
    case 'profit_factor': return G.pf(m);
    case 'max_drawdown': { const d = G.maxDD(m).v; return d === null ? null : -Math.abs(d); }
    case 'sharpe': return G.sharpe(m);
    case 'sortino': return G.sortino(m);
    case 'trades': return G.n(m);
    case 'consistency': return G.consistency(m);
    default: return marketExp(row, key).v;
  }
}

function Leaderboard({ strategies, onOpen }: { strategies: Any[]; onOpen: (s: Any) => void }) {
  const [sort, setSort] = useState('composite');
  const [lb, lbErr] = usePolling<any>(`/api/lab/leaderboard?sort=${encodeURIComponent(sort)}`, 60000);
  const serverRows = listOf(lb, ['rows', 'leaderboard', 'strategies', 'items']);
  const byId = useMemo(() => Object.fromEntries(strategies.map((s) => [sid(s), s])), [strategies]);
  const rows = useMemo(() => {
    if (serverRows.length) return serverRows.map((r) => ({ ...(byId[sid(r)] ?? {}), ...r })); // server order is authoritative
    return [...strategies].sort((a, b) => (sortValue(b, sort) ?? -1e12) - (sortValue(a, sort) ?? -1e12));
  }, [serverRows, strategies, byId, sort]);
  const scale: Any | null = lb?.confidence_scale && typeof lb.confidence_scale === 'object' ? lb.confidence_scale : null;
  // The server reports whether composites exist yet; before a full run they do
  // not, and it ranks by expectancy instead rather than by a field of zeros.
  const composedReady = lb?.composites_ready !== false;
  const sortedBy = typeof lb?.sorted_by === 'string' ? lb.sorted_by : sort;
  const lbNote = typeof lb?.note === 'string' ? lb.note : '';
  const th = (key: string, lbl: string, tip: string) => (
    <th key={key} onClick={() => setSort(key)} aria-sort={sort === key ? 'descending' : 'none'}
      style={sort === key ? { color: 'var(--accent)' } : undefined}>
      <L tip={tip}>{lbl}</L>{sort === key ? ' ▾' : ''}
    </th>
  );
  return (
    <>
      <div className="panel">
        <h3><L tip={T.composite}>Why there is no win-rate column to sort by</L></h3>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.65, maxWidth: '90ch' }}>
          The composite score is a rank-average of expectancy, profit factor, drawdown, Sortino, consistency and
          the <L tip={T.wilson}>Wilson lower bound</L> of the win rate — and deliberately leaves the raw win rate out.
          A strategy that wins 80% of the time with small winners and one huge loser is a losing strategy; ranking
          by win rate would put it on top. Win rate is still shown on each card for context. Click any column heading
          or button to re-rank; every figure carries its n and a confidence label.
        </p>
        {scale && (
          <p className="faint" style={{ fontSize: 11.5, marginTop: 4 }}>
            Confidence scale: {Object.entries(scale).map(([k, v]) => `${k.toLowerCase()} = ${String(v)} trades`).join(' · ')}
          </p>
        )}
        {lbNote && <p className="faint" style={{ fontSize: 12 }}>{lbNote}</p>}
        {!lbNote && sortedBy !== sort && <p className="faint" style={{ fontSize: 12 }}>Ranked by {label(sortedBy)}.</p>}
        {lbErr && !serverRows.length && <p className="faint" style={{ fontSize: 12 }}>Leaderboard endpoint unavailable ({lbErr.message}) — ranking the strategy list locally.</p>}
      </div>
      <div className="row" style={{ gap: 6, marginBottom: 10 }}>
        <span className="faint" style={{ fontSize: 11.5, marginRight: 4 }}>Sort by</span>
        {SORTS.map((s) => (
          <button key={s.key} className={`tab ${sort === s.key ? 'on' : ''}`} onClick={() => setSort(s.key)} title={s.tip}>{s.label}</button>
        ))}
      </div>
      {!rows.length ? (
        <div className="tbl-wrap"><div className="empty"><b>Nothing to rank yet</b>Strategies appear here once they have a completed run.</div></div>
      ) : (
        <div className="tbl-wrap">
          <table className="tbl" style={{ minWidth: 1280 }}>
            <thead><tr>
              <th className="l">#</th><th className="l">Strategy</th><th className="l"><L tip={T.stage}>Stage</L></th>
              {th('trades', 'n', T.n)}
              <th className="l"><L tip={T.confidence}>Confidence</L></th>
              {th('composite', 'Composite', T.composite)}
              {th('expectancy', 'Expectancy', T.expectancy)}
              {th('profit_factor', 'PF', T.pf)}
              {th('max_drawdown', 'Max DD', T.maxdd)}
              {th('sharpe', 'Sharpe', T.sharpe)}
              {th('sortino', 'Sortino', T.sortino)}
              {th('consistency', 'Consist.', T.consistency)}
              {th('stocks', 'Stocks exp.', SORTS[8].tip)}
              {th('crypto', 'Crypto exp.', SORTS[9].tip)}
              {th('etf', 'ETF exp.', SORTS[10].tip)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const m = mx(r);
                const n = G.n(m);
                const e = G.expectancy(m);
                const small = n === null || n < SMALL_N;
                const wl = G.wilson(m);
                const mk = (k: string) => { const v = marketExp(r, k); return <td className={tone(v.v)}>{fmtSigned(v.v, 2, v.unit)}</td>; };
                return (
                  <tr key={sid(r) || i} onClick={() => onOpen(byId[sid(r)] ?? r)}>
                    <td className="l faint">{num(r.rank) ?? i + 1}</td>
                    <td className="l"><b>{r.name ?? sid(r)}</b> <span className="badge neutral" style={{ marginLeft: 6, fontSize: 9 }}>{label(r.family)}</span>
                      {small && <span className="badge warn" style={{ marginLeft: 6, fontSize: 9 }}>small sample</span>}</td>
                    <td className="l"><StagePill stage={String(r.stage ?? '')} /></td>
                    <td className={small ? 'faint' : ''}>{fmtInt(n)}
                      {num(r.signal_dates) !== null && (
                        <span className="faint" style={{ marginLeft: 5 }}>
                          / {fmtInt(num(r.signal_dates))}d
                        </span>
                      )}
                    </td>
                    <td className="l"><ConfBadge label={confidenceLabel(r, n)} />{wl !== null && <span className="faint" style={{ fontSize: 10, marginLeft: 5 }}>LB {pctText(wl)}</span>}</td>
                    <td><b>{composedReady ? fmt(G.composite(m), 2) : <span className="faint">not scored yet</span>}</b></td>
                    <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</td>
                    <td>{fmt(G.pf(m))}</td>
                    <td className={G.maxDD(m).v !== null ? 'neg' : ''}>{ddText(m)}</td>
                    <td>{fmt(G.sharpe(m))}</td>
                    <td>{fmt(G.sortino(m))}</td>
                    <td>{pctText(G.consistency(m))}</td>
                    {mk('stocks')}{mk('crypto')}{mk('etf')}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
        Market columns show expectancy from the most honest stored run on that market (out-of-sample first). Options are not testable on the current data plan and have no column.
      </p>
    </>
  );
}

const COMPARE_ROWS: { label: string; tip: string; get: (m: Any, s: Any) => string; cls?: (m: Any) => string | undefined }[] = [
  { label: 'Stage', tip: T.stage, get: (m) => label(m.stage) || '—' },
  { label: 'Family', tip: T.family, get: (m) => label(m.family) || '—' },
  { label: 'Judged on', tip: T.split, get: (_m, s) => { const h: Any = s.headline ?? {}; return [h.split ? `${label(h.split)} split` : '', [h.market, h.timeframe].filter(Boolean).join(' / ')].filter(Boolean).join(' · ') || '—'; } },
  { label: 'Decided trades (n)', tip: T.n, get: (m) => fmtInt(G.n(m)), cls: (m) => { const n = G.n(m); return n !== null && n < SMALL_N ? 'faint' : undefined; } },
  { label: 'Confidence', tip: T.confidence, get: (m, s) => confidenceLabel(s, G.n(m)) },
  { label: 'Composite', tip: T.composite, get: (m) => fmt(G.composite(m), 2) },
  { label: 'Expectancy', tip: T.expectancy, get: (m) => { const e = G.expectancy(m); return fmtSigned(e.v, 2, e.unit); }, cls: (m) => tone(G.expectancy(m).v) },
  { label: 'Profit factor', tip: T.pf, get: (m) => fmt(G.pf(m)) },
  { label: 'Win rate', tip: T.winrate, get: (m) => { const w = G.winRate(m); return w === null ? '—' : fmt(w, 0) + '%'; } },
  { label: 'Wilson LB', tip: T.wilson, get: (m) => pctText(G.wilson(m)) },
  { label: 'Avg winner', tip: T.avgwin, get: (m) => { const a = G.avgWin(m); return fmtSigned(a.v, 2, a.unit); } },
  { label: 'Avg loser', tip: T.avgloss, get: (m) => { const a = G.avgLoss(m); return a.v === null ? '—' : fmtSigned(-Math.abs(a.v), 2, a.unit); } },
  { label: 'Total return', tip: T.return, get: (m) => { const r = G.totalReturn(m); return r === null ? '—' : fmtSigned(r, 1, '%'); }, cls: (m) => tone(G.totalReturn(m)) },
  { label: 'Max drawdown', tip: T.maxdd, get: (m) => ddText(m), cls: (m) => (G.maxDD(m).v !== null ? 'neg' : undefined) },
  { label: 'Max DD from curve', tip: T.maxdd, get: (_m, s) => { const d = num(s.max_drawdown_from_curve); return d === null ? '—' : fmt(-Math.abs(d), 2) + 'R'; }, cls: () => 'neg' },
  { label: 'Sharpe', tip: T.sharpe, get: (m) => fmt(G.sharpe(m)) },
  { label: 'Sortino', tip: T.sortino, get: (m) => fmt(G.sortino(m)) },
  { label: 'Avg R:R planned', tip: T.rr, get: (m) => { const r = G.avgRR(m); return r === null ? '—' : `${fmt(r, 2)}:1`; } },
  { label: 'Consistency', tip: T.consistency, get: (m) => pctText(G.consistency(m)) },
  { label: 'Avg hold', tip: T.hold, get: (m, s) => { const b = num(m.avg_hold_bars); const f = num(s.avg_hold_minutes_forward); return [b !== null ? `${fmt(b, 1)} bars` : '', f !== null ? `${fmt(f, 0)} min forward` : ''].filter(Boolean).join(' · ') || '—'; } },
  { label: 'Best regime', tip: T.regime, get: (m) => label(m.best_regime) || '—' },
  { label: 'Worst regime', tip: T.regime, get: (m) => label(m.worst_regime) || '—' },
  { label: 'Best market / timeframe', tip: T.timeframe, get: (m) => [m.best_market, m.best_timeframe].filter(Boolean).map(label).join(' / ') || '—' },
  { label: 'Markets with runs', tip: T.options, get: (_m, s) => (Array.isArray(s.markets_with_runs) && s.markets_with_runs.length ? s.markets_with_runs.join(', ') : '—') },
];

/** Shift a curve so every strategy starts at 0 — curves are cumulative R, so this is "R gained since first trade". */
function fromStart(curve: number[]): number[] {
  if (!curve.length) return [];
  const first = curve[0];
  return curve.map((v) => v - first);
}

function Compare({ strategies }: { strategies: Any[] }) {
  const [sel, setSel] = useState<string[]>([]);
  const idsParam = sel.length ? sel.map(encodeURIComponent).join(',') : '';
  const [cmp, cmpErr, cmpLoading] = useGet<any>(idsParam ? `/api/lab/compare?ids=${idsParam}` : null);
  const byId = useMemo(() => Object.fromEntries(strategies.map((s) => [sid(s), s])), [strategies]);
  const serverRows = listOf(cmp, ['strategies', 'rows', 'items', 'compare']);
  const chosen: Any[] = sel.map((id) => ({ ...(byId[id] ?? { strategy_id: id }), ...(serverRows.find((r) => sid(r) === id) ?? {}) }));
  const compat: Any | null = cmp?.market_compatibility && typeof cmp.market_compatibility === 'object' ? cmp.market_compatibility : null;
  const toggle = (id: string) => setSel((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= MAX_COMPARE ? cur : [...cur, id]));
  const minN = chosen.length ? Math.min(...chosen.map((s) => G.n(mx(s)) ?? 0)) : null;
  return (
    <>
      <div className="panel">
        <h3>Pick up to {MAX_COMPARE} strategies</h3>
        <p className="muted" style={{ fontSize: 12.5, marginBottom: 10, lineHeight: 1.6 }}>
          The table puts their statistics side by side; the chart overlays their <L tip={T.equity}>equity curves</L> as
          R gained since each one&apos;s first trade, so they share a starting line. Two strategies from the same <L tip={T.family}>family</L> are
          flavours of one idea, not independent evidence — the compatibility note below says when that happens.
        </p>
        <div className="row" style={{ gap: 6 }}>
          {strategies.map((s) => {
            const id = sid(s);
            const on = sel.includes(id);
            const idx = sel.indexOf(id);
            return (
              <button key={id} className={`tab ${on ? 'on' : ''}`} onClick={() => toggle(id)}
                disabled={!on && sel.length >= MAX_COMPARE} aria-pressed={on}
                style={on ? { borderColor: SERIES_COLORS[idx], color: SERIES_COLORS[idx] } : undefined}>
                {on ? '■ ' : ''}{s.name ?? id}
              </button>
            );
          })}
          {!strategies.length && <span className="faint">No strategies registered yet.</span>}
        </div>
        {sel.length > 0 && (
          <p className="faint" style={{ fontSize: 11, marginTop: 8 }}>
            {sel.length}/{MAX_COMPARE} selected{cmpLoading ? ' · loading…' : ''}{cmpErr ? ` · compare endpoint unavailable (${cmpErr.message}), using summary records` : ''}
          </p>
        )}
      </div>

      {chosen.length > 0 && (
        <>
          {compat && (
            <div className="panel">
              <h3>Compatibility</h3>
              <div className="row" style={{ gap: '6px 18px', fontSize: 12.5 }}>
                <span className="muted">Common markets: <b style={{ color: 'var(--text)' }}>{Array.isArray(compat.common_markets) && compat.common_markets.length ? compat.common_markets.join(', ') : 'none'}</b></span>
                <span className="muted">Distinct families: <b style={{ color: 'var(--text)' }}>{fmtInt(compat.distinct_families)}</b> of {chosen.length}</span>
                {Array.isArray(compat.same_family_pairs) && compat.same_family_pairs.length > 0 && (
                  <L tip={T.family}><span className="badge warn">same family: {compat.same_family_pairs.map((p: any) => (Array.isArray(p) ? p.join(' + ') : String(p))).join(', ')} — not independent</span></L>
                )}
                {compat.options?.status && <L tip={T.options}><span className="badge est">options · {String(compat.options.status)}</span></L>}
              </div>
              {Array.isArray(cmp?.missing) && cmp.missing.length > 0 && <p className="faint" style={{ fontSize: 11.5, marginTop: 6 }}>Not found on the server: {cmp.missing.join(', ')}</p>}
            </div>
          )}
          <div className="panel">
            <h3><L tip={T.equity}>Overlaid equity</L> <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· R gained since each strategy&apos;s first trade</span></h3>
            <MultiEquity unit="R" series={chosen.map((s, i) => ({ name: String(s.name ?? sid(s)), curve: fromStart(curveOf(s)), color: SERIES_COLORS[i] }))} />
          </div>
          <SmallSampleBanner n={minN} />
          <div className="tbl-wrap">
            <table className="tbl" style={{ minWidth: 520 + chosen.length * 160 }}>
              <thead><tr>
                <th className="l">Metric</th>
                {chosen.map((s, i) => <th key={sid(s)} className="l" style={{ color: SERIES_COLORS[i] }}>{s.name ?? sid(s)}</th>)}
              </tr></thead>
              <tbody>
                {COMPARE_ROWS.map((row) => (
                  <tr key={row.label} style={{ cursor: 'default' }}>
                    <td className="l"><L tip={row.tip}>{row.label}</L></td>
                    {chosen.map((s) => { const m = mx(s); return <td key={sid(s)} className={`l mono ${row.cls?.(m) ?? ''}`}>{row.get(m, s)}</td>; })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

const AGREEMENT_BUCKETS: [string, string][] = [
  ['1', 'One family alone'], ['2', 'Two families agree'], ['3+', 'Three or more agree'], ['2+', 'Any agreement (2 or more)'],
];

function Ensemble({ strategies, onOpen }: { strategies: Any[]; onOpen: (s: Any) => void }) {
  const [ens, err] = usePolling<any>('/api/lab/ensemble', 60000);
  const byId = useMemo(() => Object.fromEntries(strategies.map((s) => [sid(s), s])), [strategies]);
  if (!ens && !err) return <div className="skel" style={{ height: 240 }} />;
  if (!ens) return <div className="err-box">Ensemble endpoint unavailable — {err?.message}</div>;
  const ba: Any = ens.by_agreement && typeof ens.by_agreement === 'object' ? ens.by_agreement : {};
  const improved = ens.agreement_improves_expectancy;
  const delta = num(ens.expectancy_delta_vs_singles);
  const families: string[] = Array.isArray(ens.families) ? ens.families.map(String) : [];
  const recent: Any[] = listOf(ens, ['recent_agreements']);
  const nAgree = G.n(mx(ba['2+'] ?? {}));
  const nSingle = G.n(mx(ba['1'] ?? {}));
  const small = (nAgree ?? 0) < SMALL_N || (nSingle ?? 0) < SMALL_N;
  return (
    <>
      <div className="panel">
        <h3><L tip={T.ensemble}>Ensemble — do independent families agree?</L></h3>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.65, maxWidth: '90ch' }}>
          Instead of weighting strategies, the lab asks a simpler question: on the days when two or more <L tip={T.family}>families</L> flagged
          the same symbol in the same direction, did the trades do better than days when only one family spoke? Two strategies
          from one family never count as two votes. {ens.note ? String(ens.note) : ''}
        </p>
      </div>
      <div className="cards" style={{ margin: '0 0 16px' }}>
        <div className="card">
          <h3>Symbol-days tracked</h3>
          <div className="big">{fmtInt(ens.symbol_days_total)}</div>
          <div className="sub">{fmtInt(ens.symbol_days_with_agreement)} with two or more families agreeing</div>
        </div>
        <div className={`card ${improved === true && !small ? 'glow-buy' : ''}`}>
          <h3><L tip={T.agreement}>Agreement effect</L></h3>
          <div className="big" style={{ color: delta === null ? undefined : delta > 0 ? 'var(--buy)' : 'var(--risk)' }}>{delta === null ? '—' : fmtSigned(delta, 2, 'R')}</div>
          <div className="sub">
            {improved === null || improved === undefined ? 'not enough decided trades in both buckets yet'
              : improved ? 'agreeing symbol-days beat singles on expectancy' : 'agreement did not improve expectancy — treat confirmation with suspicion'}
            {' '}· n={fmtInt(nAgree)} vs n={fmtInt(nSingle)}
          </div>
        </div>
        <div className="card">
          <h3><L tip={T.family}>Families</L></h3>
          <div className="big">{families.length || '—'}</div>
          <div className="sub">{families.map(label).join(', ') || 'no families reported'}</div>
        </div>
      </div>
      {small && <SmallSampleBanner n={Math.min(nAgree ?? 0, nSingle ?? 0)} />}

      <div className="panel">
        <h3>By number of agreeing families</h3>
        <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
          <table className="tbl" style={{ minWidth: 720 }}>
            <thead><tr>
              <th className="l">Bucket</th><th><L tip={T.n}>n</L></th><th><L tip={T.expectancy}>Expectancy</L></th>
              <th><L tip={T.winrate}>Win rate</L></th><th><L tip={T.wilson}>Wilson LB</L></th><th><L tip={T.pf}>PF</L></th><th className="l"><L tip={T.confidence}>Confidence</L></th>
            </tr></thead>
            <tbody>
              {AGREEMENT_BUCKETS.map(([k, name]) => {
                const b: Any = ba[k] && typeof ba[k] === 'object' ? ba[k] : {};
                const m = mx(b);
                const n = G.n(m);
                const e = G.expectancy(m);
                const wr = G.winRate(m);
                return (
                  <tr key={k} style={{ cursor: 'default', fontWeight: k === '2+' ? 700 : undefined }}>
                    <td className="l">{name}</td>
                    <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                    <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit || 'R')}</td>
                    <td>{wr === null ? '—' : fmt(wr, 0) + '%'}</td>
                    <td>{pctText(G.wilson(m))}</td>
                    <td>{fmt(G.pf(m))}</td>
                    <td className="l"><ConfBadge label={confidenceLabel(b, n)} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
          One observation per family per symbol-day (the mean R of that family&apos;s closed trades there), so a family with three strategies firing at once still counts once.
        </p>
      </div>

      <div className="panel">
        <h3>Recent agreements <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· {recent.length}</span></h3>
        {!recent.length ? (
          <p className="muted" style={{ fontSize: 12.5 }}>No symbol-day yet where two families agreed. That is normal early on — families are meant to fire at different times.</p>
        ) : (
          <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
            <table className="tbl" style={{ minWidth: 820 }}>
              <thead><tr>
                <th className="l">Day</th><th className="l">Symbol</th><th className="l">Dir</th><th>Families</th>
                <th className="l"><L tip={T.family}>Which</L></th><th className="l">Strategies</th><th><L tip={T.rmult}>Outcome (mean R)</L></th>
              </tr></thead>
              <tbody>
                {recent.slice(0, 50).map((d, i) => {
                  const out: Any = d.tracked_outcome && typeof d.tracked_outcome === 'object' ? d.tracked_outcome : {};
                  const mr = num(out.mean_r);
                  const closed = num(out.n_families_closed);
                  const ids: string[] = Array.isArray(d.strategies) ? d.strategies.map(String) : [];
                  return (
                    <tr key={`${d.day}-${d.symbol}-${i}`} style={{ cursor: 'default' }}>
                      <td className="l mono" style={{ fontSize: 11.5 }}>{d.day ?? '—'}</td>
                      <td className="l"><span className="sym">{d.symbol ?? '—'}</span></td>
                      <td className="l">{d.direction ?? '—'}</td>
                      <td>{fmtInt(d.families_agreeing)}</td>
                      <td className="l">{Array.isArray(d.families) ? d.families.map(label).join(', ') : '—'}</td>
                      <td className="l">
                        <span className="row" style={{ gap: 4 }}>
                          {ids.map((id) => (
                            <button key={id} className="badge neutral" style={{ border: '1px solid var(--line-soft)', cursor: byId[id] ? 'pointer' : 'default', fontSize: 9.5 }}
                              onClick={() => byId[id] && onOpen(byId[id])} title={byId[id]?.name ?? id}>{byId[id]?.name ?? id}</button>
                          ))}
                        </span>
                      </td>
                      <td className={tone(mr)}>{mr === null ? <span className="faint">open</span> : fmtSigned(mr, 2, 'R')} <span className="faint" style={{ fontSize: 10 }}>n={fmtInt(closed)}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function Portfolio({ strategies, onOpen }: { strategies: Any[]; onOpen: (s: Any) => void }) {
  const [pf, err] = usePolling<any>('/api/lab/portfolio', 60000);
  const byId = useMemo(() => Object.fromEntries(strategies.map((s) => [sid(s), s])), [strategies]);
  if (!pf && !err) return <div className="skel" style={{ height: 240 }} />;
  if (!pf) return <div className="err-box">Portfolio endpoint unavailable — {err?.message}</div>;
  const legs: Any[] = listOf(pf, ['legs']);
  const constraint: Any = pf.constraint && typeof pf.constraint === 'object' ? pf.constraint : {};
  const combined: Any | null = pf.combined && typeof pf.combined === 'object' ? pf.combined : null;
  const best: Any | null = pf.best_single && typeof pf.best_single === 'object' ? pf.best_single : null;
  const pts: number[] = Array.isArray(combined?.points)
    ? combined!.points.map((p: any) => num(p?.value ?? p)).filter((v: number | null): v is number => v !== null) : [];
  const benefit = num(pf.diversification_benefit);
  const famCounts: [string, number][] = pf.families && typeof pf.families === 'object'
    ? Object.entries(pf.families).map(([k, v]) => [k, num(v) ?? 0] as [string, number]) : [];
  const withCurve = legs.filter((l) => l.has_curve).length;
  const legsN = legs.map((l) => G.n(mx(l))).filter((v): v is number => v !== null);
  const minN = legsN.length ? Math.min(...legsN) : null;
  const bestName = best ? (byId[String(best.strategy_id)]?.name ?? legs.find((l) => sid(l) === String(best.strategy_id))?.name ?? best.strategy_id) : null;
  return (
    <>
      <div className="panel">
        <h3><L tip={T.portfolio}>Model portfolio</L></h3>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.65, maxWidth: '90ch' }}>
          The top {fmtInt(constraint.top_n ?? 5)} strategies by composite, capped at {fmtInt(constraint.max_per_family ?? 2)} per <L tip={T.family}>family</L>,
          combined with <L tip={T.sizing}>{String(constraint.sizing ?? 'equal-risk sizing')}</L>. The point is not the return — it is whether
          mixing legs that lose at different times makes the worst stretch shallower than any single leg&apos;s. Paper only; nothing is traded.
        </p>
        {pf.note && <p className="faint" style={{ fontSize: 12 }}>{String(pf.note)}</p>}
      </div>

      <div className="cards" style={{ margin: '0 0 16px' }}>
        <div className="card">
          <h3>Legs</h3>
          <div className="big">{legs.length}</div>
          <div className="sub">{withCurve} with a stored equity curve{famCounts.length ? ` · ${famCounts.map(([f, c]) => `${label(f)} ×${c}`).join(', ')}` : ''}</div>
        </div>
        <div className="card">
          <h3><L tip={T.rmult}>Combined gain</L></h3>
          <div className="big" style={{ color: num(combined?.final) === null ? undefined : (num(combined?.final)! >= 0 ? 'var(--buy)' : 'var(--risk)') }}>
            {num(combined?.final) === null ? '—' : fmtSigned(combined?.final, 2, 'R')}
          </div>
          <div className="sub">mean R across legs, equal risk per leg · n={fmtInt(minN)} (smallest leg)</div>
        </div>
        <div className="card">
          <h3><L tip={T.maxdd}>Combined max drawdown</L></h3>
          <div className="big" style={{ color: num(combined?.max_drawdown) === null ? undefined : 'var(--risk)' }}>
            {num(combined?.max_drawdown) === null ? '—' : fmt(-Math.abs(num(combined?.max_drawdown)!), 2) + 'R'}
          </div>
          <div className="sub">{best ? `best single leg (${bestName}): ${fmt(-Math.abs(num(best.max_drawdown_from_curve) ?? 0), 2)}R` : 'no single-leg curve to compare'}</div>
        </div>
        <div className={`card ${benefit !== null && benefit > 0 ? 'glow-buy' : ''}`}>
          <h3><L tip={T.diversification}>Diversification benefit</L></h3>
          <div className="big" style={{ color: benefit === null ? undefined : benefit > 0 ? 'var(--buy)' : 'var(--risk)' }}>{benefit === null ? '—' : fmtSigned(benefit, 2, 'R')}</div>
          <div className="sub">{benefit === null ? 'needs at least one leg with a curve' : benefit > 0 ? 'shallower worst stretch than the best single leg' : 'no shallower than the best single leg — legs lose together'}</div>
        </div>
      </div>
      <SmallSampleBanner n={minN} />

      {pts.length > 1 && (
        <div className="panel">
          <h3><L tip={T.equity}>Combined equity</L>
            <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500, marginLeft: 8 }}>
              · mean cumulative R across legs · {pts.length} points · aligned on {String(combined?.aligned_on ?? 'index')}
            </span>
          </h3>
          <Sparkline curve={pts} height={120} width={700} />
        </div>
      )}

      <div className="panel">
        <h3>Legs <span className="faint" style={{ letterSpacing: 0, textTransform: 'none', fontWeight: 500 }}>· click a row for the full record</span></h3>
        {!legs.length ? (
          <p className="muted" style={{ fontSize: 12.5 }}>No strategies qualify yet — legs are chosen by composite score once runs exist.</p>
        ) : (
          <div className="tbl-wrap" style={{ boxShadow: 'none' }}>
            <table className="tbl" style={{ minWidth: 900 }}>
              <thead><tr>
                <th className="l">Strategy</th><th className="l"><L tip={T.family}>Family</L></th><th className="l"><L tip={T.stage}>Stage</L></th>
                <th><L tip={T.sizing}>Weight</L></th><th><L tip={T.composite}>Composite</L></th><th><L tip={T.n}>n</L></th>
                <th><L tip={T.expectancy}>Expectancy</L></th><th><L tip={T.rmult}>Gain (curve)</L></th><th><L tip={T.maxdd}>Max DD (curve)</L></th><th className="l"><L tip={T.confidence}>Confidence</L></th>
              </tr></thead>
              <tbody>
                {legs.map((l, i) => {
                  const full = { ...(byId[sid(l)] ?? {}), ...l };
                  const m = mx(full);
                  const n = G.n(m);
                  const e = G.expectancy(m);
                  const w = num(l.weight);
                  const gain = num(l.gain_from_curve);
                  const dd = num(l.max_drawdown_from_curve);
                  return (
                    <tr key={sid(l) || i} onClick={() => onOpen(byId[sid(l)] ?? l)}>
                      <td className="l"><b>{full.name ?? sid(l)}</b></td>
                      <td className="l">{label(full.family) || '—'}</td>
                      <td className="l">{full.stage ? <StagePill stage={String(full.stage)} /> : '—'}</td>
                      <td>{w === null ? <span className="faint">no curve</span> : fmt(w * 100, 0) + '%'}</td>
                      <td>{fmt(G.composite(m), 2)}</td>
                      <td className={n !== null && n < SMALL_N ? 'faint' : ''}>{fmtInt(n)}</td>
                      <td className={tone(e.v)}>{fmtSigned(e.v, 2, e.unit)}</td>
                      <td className={tone(gain)}>{gain === null ? '—' : fmtSigned(gain, 2, 'R')}</td>
                      <td className={dd !== null ? 'neg' : ''}>{dd === null ? '—' : fmt(-Math.abs(dd), 2) + 'R'}</td>
                      <td className="l"><ConfBadge label={confidenceLabel(full, n)} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/* ── page ──────────────────────────────────────────────────────────────────── */

export default function QuantLabPage() {
  const [tab, setTab] = useState<Tab>('Overview');
  const [open, setOpen] = useState<Any | null>(null);
  const [stratResp, stratErr] = usePolling<any>('/api/lab/strategies', 30000);
  const [regimes, regErr] = usePolling<any>('/api/lab/regimes', 60000);
  const strategies: Any[] = useMemo(() => listOf(stratResp, ['strategies', 'items', 'rows']), [stratResp]);
  const stages: string[] = useMemo(
    () => (Array.isArray(stratResp?.stages) && stratResp.stages.length ? stratResp.stages.map(String) : STAGES),
    [stratResp]);
  const loading = !stratResp && !stratErr;
  const openStrategy = (s: Any) => setOpen(s);
  const optionsNote = stratResp?.options?.status ? String(stratResp.options.status) : 'not available on data plan';

  return (
    <main className="wrap">
      <header className="page-head">
        <h1>Quant Lab <span className="badge neutral">{strategies.length} strategies</span>
          <L tip={T.options}><span className="badge est">options · {optionsNote}</span></L></h1>
        <p className="muted">
          Independent strategy research: every idea is written down as a hypothesis, tested on data it was not
          tuned on, and promoted or failed on evidence. Paper research only; no orders are placed.
        </p>
      </header>

      <div className="safety">
        <span aria-hidden style={{ fontSize: 16 }}>⚠</span>
        <div>
          <b>Backtests and paper results are not predictions.</b> A strategy that looks good on {SMALL_N} trades is a
          coin that came up heads a few times. Statistics tighten as n grows; until then, everything here is a hypothesis.
        </div>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t}
            className={`tab ${tab === t ? 'on' : ''}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {stratErr && !strategies.length && tab !== 'Overview' && (
        <div className="err-box">Could not reach /api/lab/strategies — {stratErr.message}. The backend lab routes may not be running yet.</div>
      )}

      {tab === 'Overview' && <Overview strategies={strategies} stages={stages} regimes={regimes} regErr={regErr} loading={loading} onOpen={openStrategy} />}
      {tab === 'Strategies' && <Strategies strategies={strategies} stages={stages} loading={loading} err={stratErr} onOpen={openStrategy} />}
      {tab === 'Leaderboard' && <Leaderboard strategies={strategies} onOpen={openStrategy} />}
      {tab === 'Compare' && <Compare strategies={strategies} />}
      {tab === 'Ensemble' && <Ensemble strategies={strategies} onOpen={openStrategy} />}
      {tab === 'Portfolio' && <Portfolio strategies={strategies} onOpen={openStrategy} />}

      {open && <StrategyDrawer base={open} onClose={() => setOpen(null)} />}

      <p className="disclaimer">
        Quant Lab reports exactly what was measured. Green never means guaranteed profit. Options markets are listed
        where a strategy supports them but are not testable on the current data plan.
      </p>
    </main>
  );
}
