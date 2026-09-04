'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ColorType, createChart, type IChartApi, type UTCTimestamp } from 'lightweight-charts';
import { apiGet } from '../lib/api';
import { fmtDateLabel, fmtEtClock, fmtPrice } from '../lib/format';
import { useMode } from '../lib/mode';
import { humanKey } from '../lib/vocab';
import { StatusPill } from './ui/StatusPill';
import { Term } from './ui/Popover';

/* ── /api/chart/bars, /api/chart/analyze (backend/app/routes/api.py, app/strategy/charting.py) ── */
interface Bar { time: number | string; open: number; high: number; low: number; close: number; volume: number }
type Tf = '5min' | '1hour' | 'daily';
type Tool = 'none' | 'hline' | 'trend' | 'fib';
interface Zone { level: number; touches: number; kind: 'support' | 'resistance' | string }
interface Trendline { kind: string; t1: number | string | null; t2: number | string | null; p1: number; p2: number }
interface Pattern {
  type: string; t1?: number | string | null; t2?: number | string | null; p1?: number; p2?: number;
  neck?: number; neck_t?: number | string | null; t_end?: number | string | null; level?: number;
  hi?: number; lo?: number; note?: string;
}
interface Signal { kind: string; time: number | string | null; price: number; level?: number; reason?: string }
interface Detect { zones?: Zone[]; trendlines?: Trendline[]; patterns?: Pattern[]; signals?: Signal[]; quality?: string; note?: string }

const TFS: { key: Tf; label: string }[] = [{ key: '5min', label: '5m' }, { key: '1hour', label: '1h' }, { key: 'daily', label: '1d' }];
const IND_DEFS: [string, string][] = [
  ['vwap', 'Average price today (VWAP)'], ['sma20', '20-bar average'], ['sma50', '50-bar average'],
  ['gauss', 'Gaussian channel'], ['vol', 'Volume'], ['rsi', 'RSI momentum'],
];
const AUTO_DEFS: [string, string][] = [
  ['sr', 'Ceilings and floors'], ['tl', 'Trendlines'], ['pat', 'Patterns'], ['sig', 'Buy/Sell marks'],
];
const TOOLS: { key: Tool; label: string }[] = [
  { key: 'none', label: 'Cursor' }, { key: 'hline', label: 'Horizontal line' },
  { key: 'trend', label: 'Trend line (two clicks)' }, { key: 'fib', label: 'Fibonacci (two clicks)' },
];

/* ── indicator math (causal) ── */
function sma(vals: number[], n: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i];
    if (i >= n) sum -= vals[i - n];
    out.push(i >= n - 1 ? sum / n : null);
  }
  return out;
}
function gaussF(vals: number[], period = 20, poles = 3): number[] {
  const beta = (1 - Math.cos((2 * Math.PI) / period)) / (Math.pow(2, 1 / poles) - 1);
  const a = -beta + Math.sqrt(beta * beta + 2 * beta);
  let seq = vals.slice();
  for (let p = 0; p < poles; p++) {
    const f = [seq[0]];
    for (let i = 1; i < seq.length; i++) f.push(a * seq[i] + (1 - a) * f[i - 1]);
    seq = f;
  }
  return seq;
}
function rsiSeries(vals: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = [null];
  let g = 0, l = 0;
  for (let i = 1; i < vals.length; i++) {
    const d = vals[i] - vals[i - 1];
    if (i <= n) { g += Math.max(0, d); l += Math.max(0, -d); out.push(null); continue; }
    g = (g * (n - 1) + Math.max(0, d)) / n;
    l = (l * (n - 1) + Math.max(0, -d)) / n;
    out.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
  }
  return out;
}

/* ── colors: design tokens read from :root at mount (spec §3.15) ── */
interface Tokens { buy: string; risk: string; accent: string; warn: string; early: string; backtest: string; live: string; textDim: string; line: string; lineSoft: string }
const FALLBACK_TOKENS: Tokens = {
  buy: '#34d399', risk: '#f87171', accent: '#38bdf8', warn: '#fbbf24', early: '#67e8f9',
  backtest: '#a78bfa', live: '#f472b6', textDim: '#8b98b4', line: '#182135', lineSoft: '#1e2942',
};
function readTokens(): Tokens {
  try {
    const cs = getComputedStyle(document.documentElement);
    const g = (k: string, fb: string) => cs.getPropertyValue(k).trim() || fb;
    return {
      buy: g('--buy', FALLBACK_TOKENS.buy), risk: g('--risk', FALLBACK_TOKENS.risk),
      accent: g('--accent', FALLBACK_TOKENS.accent), warn: g('--warn', FALLBACK_TOKENS.warn),
      early: g('--early', FALLBACK_TOKENS.early), backtest: g('--backtest', FALLBACK_TOKENS.backtest),
      live: g('--live', FALLBACK_TOKENS.live), textDim: g('--text-dim', FALLBACK_TOKENS.textDim),
      line: g('--line', FALLBACK_TOKENS.line), lineSoft: g('--line-soft', FALLBACK_TOKENS.lineSoft),
    };
  } catch { return FALLBACK_TOKENS; }
}
/** "#rrggbb" | "rgb(r,g,b)" → "rgba(r,g,b,a)"; anything else is returned unchanged. */
function alpha(color: string, a: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/.exec(color);
  if (rgb) {
    const [r, g, b] = rgb[1].split(',').map((x) => x.trim());
    return `rgba(${r},${g},${b},${a})`;
  }
  return color;
}

/** bar time → "9:35 AM" (intraday) or "Thu Sep 4" (daily) */
function when(t: number | string | null | undefined): string {
  if (t === null || t === undefined) return '—';
  if (typeof t === 'number') return fmtEtClock(new Date(t * 1000).toISOString());
  return fmtDateLabel(t);
}

/* ── small dropdown menu: click/Esc/outside-click, never hover-only ── */
function Menu({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <div className="cp-menu" ref={ref}>
      <button type="button" className={`ptab ${open ? 'active' : ''}`} aria-haspopup="true" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}>{label} ▾</button>
      {open ? <div className="cp-menu-body" role="group" aria-label={label}>{children}</div> : null}
    </div>
  );
}

const CSS = `
.cp-pane { padding: 10px 12px; }
.cp-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
.cp-bar .switch { padding: 0; }
.cp-sym { width: 104px; font-weight: 700; }
.cp-seg { display: inline-flex; gap: 4px; }
.cp-seg .ptab { padding: 4px 10px; }
.cp-menu { position: relative; }
.cp-menu-body { position: absolute; top: calc(100% + 6px); left: 0; z-index: 60; min-width: 220px; padding: 10px 12px;
  background: #0d1424; border: 1px solid var(--line); border-radius: var(--r-sm); box-shadow: 0 10px 26px rgba(0,0,0,.5);
  display: flex; flex-direction: column; gap: 6px; }
.cp-menu-body .switch { padding: 3px 0; }
.cp-menu-body .ptab { justify-content: flex-start; }
.cp-menu-hd { font-size: var(--fs-eyebrow); text-transform: uppercase; letter-spacing: 1.2px; color: var(--text-faint); font-weight: 700; margin-top: 4px; }
.cp-badges { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.cp-replay { display: inline-flex; gap: 6px; align-items: center; }
.cp-replay .ptab { padding: 3px 9px; }
.cp-hint { font-size: var(--fs-note); color: var(--text-dim); }
`;

export default function ChartPane({ paneId, defaultSymbol }: { paneId: string; defaultSymbol: string }) {
  const { advanced } = useMode();
  const holder = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [symInput, setSymInput] = useState(defaultSymbol);
  const [tf, setTf] = useState<Tf>('5min');
  const [inds, setInds] = useState<Record<string, boolean>>({
    vwap: true, sma20: true, sma50: false, gauss: false, vol: true, rsi: false });
  const [tool, setTool] = useState<Tool>('none');
  const [bars, setBars] = useState<Bar[]>([]);
  const [quality, setQuality] = useState<string | null>(null);
  const [replayIdx, setReplayIdx] = useState<number | null>(null);
  const [det, setDet] = useState<Detect | null>(null);
  const [tokens, setTokens] = useState<Tokens>(FALLBACK_TOKENS);
  const [auto, setAuto] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem('ph_auto') || '') || {}; }
    catch { return { sr: true, tl: true, pat: true, sig: true, simple: true }; }
  });
  const [playing, setPlaying] = useState(false);
  const pendingPt = useRef<{ time: number; price: number } | null>(null);
  const drawKey = `ph_draw_${symbol}_${tf}`;

  useEffect(() => { setTokens(readTokens()); }, []);

  const setAutoK = (patch: Record<string, boolean>) => setAuto((s) => {
    const n = { ...s, ...patch };
    try { localStorage.setItem('ph_auto', JSON.stringify(n)); } catch { /* private mode */ }
    return n;
  });
  const analyze = !!(auto.sr || auto.tl || auto.pat || auto.sig);
  // Simple mode always uses plain labels; Advanced keeps the stored preference.
  const plainLabels = advanced ? auto.simple !== false : true;

  useEffect(() => {
    let alive = true;
    setQuality(null);
    apiGet<{ bars: Bar[]; quality: string }>(`/api/chart/bars?symbol=${encodeURIComponent(symbol)}&tf=${tf}`)
      .then((r) => { if (alive) { setBars(r.bars); setQuality(r.quality); setReplayIdx(null); } })
      .catch(() => { if (alive) setQuality('UNAVAILABLE'); });
    if (analyze) {
      apiGet<Detect>(`/api/chart/analyze?symbol=${encodeURIComponent(symbol)}&tf=${tf}`)
        .then((r) => { if (alive) setDet(r); })
        .catch(() => { if (alive) setDet(null); });
    } else setDet(null);
    return () => { alive = false; };
  }, [symbol, tf, analyze]);

  useEffect(() => {
    if (!playing || replayIdx === null) return;
    const id = setInterval(() => setReplayIdx((i) =>
      i !== null && i < bars.length ? i + 1 : (setPlaying(false), i)), 350);
    return () => clearInterval(id);
  }, [playing, replayIdx, bars.length]);

  const view = replayIdx === null ? bars : bars.slice(0, replayIdx);

  const draw = useCallback(() => {
    if (!holder.current) return;
    holder.current.innerHTML = '';
    const C = tokens;
    const chart = createChart(holder.current, {
      height: inds.rsi ? 430 : 360,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: C.textDim, fontSize: 12 },
      grid: { vertLines: { color: C.line }, horzLines: { color: C.line } },
      rightPriceScale: { borderColor: C.lineSoft },
      timeScale: { borderColor: C.lineSoft, timeVisible: tf !== 'daily' },
      crosshair: { horzLine: { color: C.accent }, vertLine: { color: C.accent } },
    });
    chartRef.current = chart;
    const candles = chart.addCandlestickSeries({
      upColor: C.buy, downColor: C.risk, borderVisible: false, wickUpColor: C.buy, wickDownColor: C.risk });
    const data = view.map((b) => ({ ...b, time: (typeof b.time === 'string' ? b.time : b.time as UTCTimestamp) as any }));
    candles.setData(data);
    const closes = view.map((b) => b.close);
    const times = data.map((d) => d.time);
    const lineFrom = (vals: (number | null)[], color: string, width = 1) => {
      const s = chart.addLineSeries({ color, lineWidth: width as any, priceLineVisible: false, lastValueVisible: false });
      s.setData(vals.map((v, i) => v === null ? null : ({ time: times[i], value: v })).filter(Boolean) as any);
      return s;
    };
    if (inds.sma20) lineFrom(sma(closes, 20), C.accent);
    if (inds.sma50) lineFrom(sma(closes, 50), C.backtest);
    if (inds.vwap && tf === '5min') {
      let num = 0, den = 0, day = '';
      const vw: (number | null)[] = [];
      view.forEach((b) => {
        const d = typeof b.time === 'number' ? new Date(b.time * 1000).toISOString().slice(0, 10) : String(b.time);
        if (d !== day) { day = d; num = 0; den = 0; }
        if (b.volume > 0) { num += ((b.high + b.low + b.close) / 3) * b.volume; den += b.volume; }
        vw.push(den ? num / den : null);
      });
      lineFrom(vw, C.warn, 2);
    }
    if (inds.gauss && closes.length > 25) {
      const g = gaussF(closes);
      const tr = view.map((b, i) => i === 0 ? b.high - b.low :
        Math.max(b.high - b.low, Math.abs(b.high - view[i - 1].close), Math.abs(b.low - view[i - 1].close)));
      const ftr = gaussF(tr);
      lineFrom(g, C.backtest, 2);
      lineFrom(g.map((v, i) => v + 1.4 * ftr[i]), alpha(C.backtest, 0.5));
      lineFrom(g.map((v, i) => v - 1.4 * ftr[i]), alpha(C.backtest, 0.5));
    }
    if (inds.vol) {
      const vs = chart.addHistogramSeries({ priceScaleId: 'v' });
      chart.priceScale('v').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
      vs.setData(view.map((b, i) => ({ time: times[i], value: b.volume,
        color: b.close >= b.open ? alpha(C.buy, 0.35) : alpha(C.risk, 0.35) })));
    }
    if (inds.rsi && closes.length > 20) {
      const rs = chart.addLineSeries({ priceScaleId: 'rsi', color: C.live, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      chart.priceScale('rsi').applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
      rs.setData(rsiSeries(closes).map((v, i) => v === null ? null : ({ time: times[i], value: v })).filter(Boolean) as any);
    }
    // ── auto-detected technicals (deterministic, non-repainting) ──
    if (det && replayIdx === null) {
      if (auto.sr) {
        const zones = plainLabels
          ? [...(det.zones ?? [])].sort((a, b) => b.touches - a.touches).slice(0, 3)
          : det.zones ?? [];
        for (const z of zones) {
          const res = z.kind === 'resistance';
          const word = res ? 'Ceiling' : 'Floor';
          candles.createPriceLine({ price: z.level,
            color: res ? alpha(C.risk, 0.65) : alpha(C.buy, 0.65),
            lineWidth: z.touches >= 3 ? 2 : 1, lineStyle: 0, axisLabelVisible: true,
            title: advanced ? `${word} — touched ${z.touches} times` : word });
        }
      }
      if (auto.tl) {
        for (const t of det.trendlines ?? []) {
          if (t.t1 == null || t.t2 == null) continue;
          const s = chart.addLineSeries({ color: t.kind.startsWith('up') ? C.buy : C.risk,
            lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
          s.setData([{ time: t.t1, value: t.p1 }, { time: t.t2, value: t.p2 }] as any);
        }
      }
      const allMarks: any[] = [];
      if (auto.pat) {
        const seg = (pts: { time: any; value: number }[], color: string, width = 2, style = 0) => {
          const s = chart.addLineSeries({ color, lineWidth: width as any, lineStyle: style as any,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          s.setData(pts as any);
        };
        for (const pat of det.patterns ?? []) {
          if (pat.type === 'double_top' && pat.t1 != null && pat.neck_t != null) {
            seg([{ time: pat.t1, value: pat.p1! }, { time: pat.neck_t, value: pat.neck! }, { time: pat.t2, value: pat.p2! }], alpha(C.warn, 0.9));
            seg([{ time: pat.t1, value: pat.neck! }, { time: pat.t_end ?? pat.t2, value: pat.neck! }], alpha(C.warn, 0.5), 1, 2);
            allMarks.push({ time: pat.t2, position: 'aboveBar', color: C.warn, shape: 'circle', text: plainLabels ? 'M pattern' : 'double top' });
          }
          if (pat.type === 'double_bottom' && pat.t1 != null && pat.neck_t != null) {
            seg([{ time: pat.t1, value: pat.p1! }, { time: pat.neck_t, value: pat.neck! }, { time: pat.t2, value: pat.p2! }], alpha(C.early, 0.9));
            seg([{ time: pat.t1, value: pat.neck! }, { time: pat.t_end ?? pat.t2, value: pat.neck! }], alpha(C.early, 0.5), 1, 2);
            allMarks.push({ time: pat.t2, position: 'belowBar', color: C.early, shape: 'circle', text: plainLabels ? 'W pattern' : 'double bottom' });
          }
          if (pat.type === 'compression' && pat.t1 != null && pat.t2 != null) {
            seg([{ time: pat.t1, value: pat.hi! }, { time: pat.t2, value: pat.hi! }], alpha(C.backtest, 0.7), 1, 3);
            seg([{ time: pat.t1, value: pat.lo! }, { time: pat.t2, value: pat.lo! }], alpha(C.backtest, 0.7), 1, 3);
            allMarks.push({ time: pat.t2, position: 'inBar', color: C.backtest, shape: 'square', text: plainLabels ? 'coil' : 'compression' });
          }
        }
      }
      if (auto.sig) {
        const sigs = plainLabels ? (det.signals ?? []).slice(-6) : det.signals ?? [];
        for (const s of sigs.filter((x) => x.time != null)) {
          const buy = s.kind.startsWith('buy');
          allMarks.push({ time: s.time, position: buy ? 'belowBar' : 'aboveBar', color: buy ? C.buy : C.risk,
            shape: buy ? 'arrowUp' : 'arrowDown', text: plainLabels ? (buy ? 'Buy' : 'Sell') : s.kind.replace(/_/g, ' ') });
        }
      }
      if (allMarks.length) {
        const seen = new Set<string>();
        const uniq = allMarks
          .sort((a: any, b: any) => ((a.time as number) > (b.time as number) ? 1 : -1))
          .filter((m: any) => { const k = `${m.time}|${m.text}`; if (seen.has(k)) return false; seen.add(k); return true; });
        candles.setMarkers(uniq as any);
      }
    }
    // saved drawings
    try {
      const saved = JSON.parse(localStorage.getItem(drawKey) || '[]');
      for (const d of saved) {
        if (d.type === 'hline') {
          candles.createPriceLine({ price: d.price, color: C.early, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'line' });
        } else if (d.type === 'trend') {
          const s = chart.addLineSeries({ color: C.early, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
          s.setData([{ time: d.t1, value: d.p1 }, { time: d.t2, value: d.p2 }] as any);
        } else if (d.type === 'fib') {
          const hi = Math.max(d.p1, d.p2), lo = Math.min(d.p1, d.p2);
          for (const f of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) {
            candles.createPriceLine({ price: lo + (hi - lo) * f, color: alpha(C.warn, 0.55), lineWidth: 1, lineStyle: 3,
              axisLabelVisible: true, title: `fib ${f}` });
          }
        }
      }
    } catch { /* ignore */ }
    // drawing interactions
    chart.subscribeClick((p) => {
      if (!p.point || !p.time) return;
      const price = candles.coordinateToPrice(p.point.y);
      if (price == null) return;
      const saveDraw = (d: any) => {
        try {
          const cur = JSON.parse(localStorage.getItem(drawKey) || '[]');
          cur.push(d);
          localStorage.setItem(drawKey, JSON.stringify(cur.slice(-40)));
        } catch { /* ignore */ }
        draw();
      };
      if (tool === 'hline') saveDraw({ type: 'hline', price });
      else if (tool === 'trend' || tool === 'fib') {
        if (!pendingPt.current) { pendingPt.current = { time: p.time as any, price }; }
        else {
          saveDraw({ type: tool, t1: pendingPt.current.time, p1: pendingPt.current.price, t2: p.time, p2: price });
          pendingPt.current = null;
        }
      }
    });
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: holder.current?.clientWidth ?? 500 }));
    ro.observe(holder.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [view, inds, tf, tool, drawKey, det, replayIdx, auto, tokens, plainLabels, advanced]);

  useEffect(() => { const c = draw(); return c; }, [draw]);

  const patternText = (p: Pattern) => {
    const base = plainLabels
      ? (p.type === 'double_top' ? 'hit the same ceiling twice'
        : p.type === 'double_bottom' ? 'bounced off the same floor twice'
        : p.type === 'compression' ? 'coiling tighter — expansion often follows'
        : humanKey(p.type).toLowerCase())
      : humanKey(p.type).toLowerCase();
    const lvl = p.level !== undefined ? `level ${fmtPrice(p.level)}`
      : p.hi !== undefined && p.lo !== undefined ? `range ${fmtPrice(p.lo)}–${fmtPrice(p.hi)}` : null;
    return [base, lvl, when(p.t2)].filter(Boolean).join(' · ');
  };
  const signalText = (s: Signal) => {
    const buy = s.kind.startsWith('buy');
    const head = plainLabels ? `${buy ? 'Buy' : 'Sell'} at ${fmtPrice(s.price)}` : `${humanKey(s.kind)} at ${fmtPrice(s.price)}`;
    const lvl = s.level !== undefined ? `level ${fmtPrice(s.level)}` : null;
    return [head, lvl, when(s.time)].filter(Boolean).join(' · ');
  };

  const shownPatterns = auto.pat ? (det?.patterns ?? []) : [];
  const shownSignals = auto.sig ? (det?.signals ?? []).slice(-4) : [];

  return (
    <div className="tbl-wrap cp-pane">
      <style href="tf-chartpane" precedence="default">{CSS}</style>
      <div className="cp-bar">
        <form onSubmit={(e) => { e.preventDefault(); if (symInput.trim()) setSymbol(symInput.trim().toUpperCase()); }}>
          <label className="sr-only" htmlFor={`${paneId}-sym`}>Symbol</label>
          <input id={`${paneId}-sym`} className="input cp-sym" value={symInput} onChange={(e) => setSymInput(e.target.value)}
            autoComplete="off" spellCheck={false} />
        </form>
        <div className="cp-seg" role="group" aria-label="Timeframe">
          {TFS.map((t) => (
            <button key={t.key} type="button" className={`ptab ${tf === t.key ? 'active' : ''}`} aria-pressed={tf === t.key}
              onClick={() => setTf(t.key)}>{t.label}</button>
          ))}
        </div>
        {quality === null ? <span className="fresh">Data: loading…</span>
          : quality === 'LIVE' ? <StatusPill size="sm" tone="buy" label="Data: live" raw={advanced ? quality : undefined} />
          : <StatusPill size="sm" tone="warn" label="Data: unavailable" raw={quality} />}

        {!advanced ? (
          <span className="switch">
            <label className="switch">
              <input type="checkbox" checked={analyze}
                onChange={(e) => setAutoK({ sr: e.target.checked, tl: e.target.checked, pat: e.target.checked, sig: e.target.checked })} />
              <span>Auto-mark levels</span>
            </label>
            <span className="cp-hint">(<Term k="ceiling_floor">Ceiling/Floor</Term>)</span>
          </span>
        ) : (
          <Menu label="Auto-mark">
            {AUTO_DEFS.map(([k, label]) => (
              <label key={k} className="switch">
                <input type="checkbox" checked={!!auto[k]} onChange={(e) => setAutoK({ [k]: e.target.checked })} />
                <span>{label}</span>
              </label>
            ))}
            <div className="cp-menu-hd">Labels</div>
            <label className="switch">
              <input type="checkbox" checked={auto.simple !== false} onChange={(e) => setAutoK({ simple: e.target.checked })} />
              <span>Plain labels (3 strongest levels, last 6 marks)</span>
            </label>
          </Menu>
        )}

        <Menu label="Indicators">
          {IND_DEFS.map(([k, label]) => (
            <label key={k} className="switch">
              <input type="checkbox" checked={!!inds[k]} onChange={(e) => setInds((s) => ({ ...s, [k]: e.target.checked }))} />
              <span>{label}{k === 'vwap' && tf !== '5min' ? <span className="dim"> (5m only)</span> : null}</span>
            </label>
          ))}
        </Menu>

        <Menu label="Tools">
          <div className="cp-menu-hd">Draw</div>
          {TOOLS.map((t) => (
            <button key={t.key} type="button" className={`ptab ${tool === t.key ? 'active' : ''}`} aria-pressed={tool === t.key}
              onClick={() => { setTool(t.key); pendingPt.current = null; }}>{t.label}</button>
          ))}
          <button type="button" className="ptab" onClick={() => { try { localStorage.removeItem(drawKey); } catch { /* ignore */ } draw(); }}>
            Clear drawings for {symbol}
          </button>
          <div className="cp-menu-hd">Replay</div>
          <button type="button" className="ptab" aria-pressed={replayIdx !== null}
            onClick={() => { setReplayIdx(replayIdx === null ? Math.max(10, bars.length - 60) : null); setPlaying(false); }}>
            {replayIdx === null ? 'Start replay (hides future bars)' : 'Exit replay'}
          </button>
        </Menu>

        {replayIdx !== null ? (
          <span className="cp-replay" role="group" aria-label="Replay controls">
            <button type="button" className="ptab" aria-label="Previous bar" onClick={() => setReplayIdx((i) => Math.max(10, (i ?? 0) - 1))}>‹</button>
            <button type="button" className="ptab" aria-label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying((p) => !p)}>{playing ? '⏸' : '▶'}</button>
            <button type="button" className="ptab" aria-label="Next bar" onClick={() => setReplayIdx((i) => Math.min(bars.length, (i ?? 0) + 1))}>›</button>
            <span className="cp-hint">bar {replayIdx} of {bars.length}</span>
          </span>
        ) : null}
        {tool !== 'none' ? <span className="cp-hint">{tool === 'hline' ? 'Click the chart to place a line' : 'Click two points on the chart'}</span> : null}
      </div>

      {shownPatterns.length || shownSignals.length ? (
        <div className="cp-badges">
          {shownPatterns.map((p, i) => <StatusPill key={`p${i}`} size="sm" tone="warn" label={patternText(p)} />)}
          {shownSignals.map((s, i) => (
            <StatusPill key={`s${i}`} size="sm" tone={s.kind.startsWith('buy') ? 'buy' : 'risk'} label={signalText(s)} />
          ))}
        </div>
      ) : null}
      {advanced && det?.note ? <p className="cp-hint" style={{ marginBottom: 6 }}>{det.note}</p> : null}
      <div ref={holder} style={{ width: '100%' }} />
    </div>
  );
}
