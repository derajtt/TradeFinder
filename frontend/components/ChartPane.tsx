'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ColorType, createChart, type IChartApi, type ISeriesApi,
         type UTCTimestamp } from 'lightweight-charts';
import { apiGet } from '../lib/api';

interface Bar { time: number | string; open: number; high: number; low: number;
  close: number; volume: number; }
type Tool = 'none' | 'hline' | 'trend' | 'fib';

const IND_DEFS = [
  ['vwap', 'VWAP'], ['sma20', 'SMA 20'], ['sma50', 'SMA 50'],
  ['gauss', 'Gaussian Ch.'], ['vol', 'Volume'], ['rsi', 'RSI'],
] as const;

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

export default function ChartPane({ paneId, defaultSymbol }: {
  paneId: string; defaultSymbol: string;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [symInput, setSymInput] = useState(defaultSymbol);
  const [tf, setTf] = useState<'5min' | '1hour' | 'daily'>('5min');
  const [inds, setInds] = useState<Record<string, boolean>>({
    vwap: true, sma20: true, sma50: false, gauss: false, vol: true, rsi: false });
  const [tool, setTool] = useState<Tool>('none');
  const [bars, setBars] = useState<Bar[]>([]);
  const [quality, setQuality] = useState('');
  const [replayIdx, setReplayIdx] = useState<number | null>(null);
  const [analyze, setAnalyze] = useState(true);
  const [det, setDet] = useState<any>(null);
  const [playing, setPlaying] = useState(false);
  const pendingPt = useRef<{ time: number; price: number } | null>(null);
  const drawKey = `ph_draw_${symbol}_${tf}`;

  useEffect(() => {
    let alive = true;
    apiGet<{ bars: Bar[]; quality: string }>(`/api/chart/bars?symbol=${symbol}&tf=${tf}`)
      .then((r) => { if (alive) { setBars(r.bars); setQuality(r.quality); setReplayIdx(null); } })
      .catch(() => setQuality('UNAVAILABLE'));
    if (analyze) {
      apiGet<any>(`/api/chart/analyze?symbol=${symbol}&tf=${tf}`)
        .then((r) => { if (alive) setDet(r); })
        .catch(() => setDet(null));
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
    const chart = createChart(holder.current, {
      height: inds.rsi ? 430 : 360,
      layout: { background: { type: ColorType.Solid, color: 'transparent' },
                textColor: '#8b98b4', fontSize: 11 },
      grid: { vertLines: { color: '#182135' }, horzLines: { color: '#182135' } },
      rightPriceScale: { borderColor: '#1e2942' },
      timeScale: { borderColor: '#1e2942', timeVisible: tf !== 'daily' },
      crosshair: { horzLine: { color: '#38bdf8' }, vertLine: { color: '#38bdf8' } },
    });
    chartRef.current = chart;
    const candles = chart.addCandlestickSeries({
      upColor: '#34d399', downColor: '#f87171', borderVisible: false,
      wickUpColor: '#34d399', wickDownColor: '#f87171' });
    const data = view.map((b) => ({ ...b, time: (typeof b.time === 'string'
      ? b.time : b.time as UTCTimestamp) as any }));
    candles.setData(data);
    const closes = view.map((b) => b.close);
    const times = data.map((d) => d.time);
    const lineFrom = (vals: (number | null)[], color: string, width = 1) => {
      const s = chart.addLineSeries({ color, lineWidth: width as any,
        priceLineVisible: false, lastValueVisible: false });
      s.setData(vals.map((v, i) => v === null ? null :
        ({ time: times[i], value: v })).filter(Boolean) as any);
      return s;
    };
    if (inds.sma20) lineFrom(sma(closes, 20), '#38bdf8');
    if (inds.sma50) lineFrom(sma(closes, 50), '#a78bfa');
    if (inds.vwap && tf === '5min') {
      let num = 0, den = 0, day = '';
      const vw: (number | null)[] = [];
      view.forEach((b) => {
        const d = typeof b.time === 'number'
          ? new Date(b.time * 1000).toISOString().slice(0, 10) : String(b.time);
        if (d !== day) { day = d; num = 0; den = 0; }
        if (b.volume > 0) { num += ((b.high + b.low + b.close) / 3) * b.volume; den += b.volume; }
        vw.push(den ? num / den : null);
      });
      lineFrom(vw, '#fbbf24', 2);
    }
    if (inds.gauss && closes.length > 25) {
      const g = gaussF(closes);
      const tr = view.map((b, i) => i === 0 ? b.high - b.low :
        Math.max(b.high - b.low, Math.abs(b.high - view[i - 1].close),
                 Math.abs(b.low - view[i - 1].close)));
      const ftr = gaussF(tr);
      lineFrom(g, '#818cf8', 2);
      lineFrom(g.map((v, i) => v + 1.4 * ftr[i]), 'rgba(129,140,248,0.5)');
      lineFrom(g.map((v, i) => v - 1.4 * ftr[i]), 'rgba(129,140,248,0.5)');
    }
    if (inds.vol) {
      const vs = chart.addHistogramSeries({ priceScaleId: 'v' });
      chart.priceScale('v').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
      vs.setData(view.map((b, i) => ({ time: times[i], value: b.volume,
        color: b.close >= b.open ? 'rgba(52,211,153,0.35)' : 'rgba(248,113,113,0.35)' })));
    }
    if (inds.rsi && closes.length > 20) {
      const rs = chart.addLineSeries({ priceScaleId: 'rsi', color: '#f472b6',
        lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      chart.priceScale('rsi').applyOptions({ scaleMargins: { top: 0.75, bottom: 0.02 } });
      rs.setData(rsiSeries(closes).map((v, i) => v === null ? null :
        ({ time: times[i], value: v })).filter(Boolean) as any);
    }
    // ── auto-detected technicals (deterministic, non-repainting) ──
    if (det && replayIdx === null) {
      for (const z of det.zones ?? []) {
        candles.createPriceLine({ price: z.level,
          color: z.kind === 'resistance' ? 'rgba(248,113,113,0.65)' : 'rgba(52,211,153,0.65)',
          lineWidth: z.touches >= 3 ? 2 : 1, lineStyle: 0, axisLabelVisible: true,
          title: `${z.kind === 'resistance' ? 'R' : 'S'}×${z.touches}` });
      }
      for (const t of det.trendlines ?? []) {
        if (t.t1 == null || t.t2 == null) continue;
        const s = chart.addLineSeries({ color: t.kind.startsWith('up') ? '#34d399' : '#f87171',
          lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
        s.setData([{ time: t.t1, value: t.p1 }, { time: t.t2, value: t.p2 }] as any);
      }
      const sigMarks = (det.signals ?? []).filter((s: any) => s.time != null)
        .map((s: any) => ({ time: s.time,
          position: s.kind.startsWith('buy') ? 'belowBar' : 'aboveBar',
          color: s.kind.startsWith('buy') ? '#34d399' : '#f87171',
          shape: s.kind.startsWith('buy') ? 'arrowUp' : 'arrowDown',
          text: s.kind.replace(/_/g, ' ') }));
      if (sigMarks.length) candles.setMarkers(sigMarks.sort((a: any, b: any) =>
        (a.time as number) > (b.time as number) ? 1 : -1));
    }
    // saved drawings
    try {
      const saved = JSON.parse(localStorage.getItem(drawKey) || '[]');
      for (const d of saved) {
        if (d.type === 'hline') {
          candles.createPriceLine({ price: d.price, color: '#67e8f9', lineWidth: 1,
            lineStyle: 2, axisLabelVisible: true, title: 'line' });
        } else if (d.type === 'trend') {
          const s = chart.addLineSeries({ color: '#67e8f9', lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false });
          s.setData([{ time: d.t1, value: d.p1 }, { time: d.t2, value: d.p2 }] as any);
        } else if (d.type === 'fib') {
          const hi = Math.max(d.p1, d.p2), lo = Math.min(d.p1, d.p2);
          for (const f of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) {
            candles.createPriceLine({ price: lo + (hi - lo) * f,
              color: 'rgba(251,191,36,0.55)', lineWidth: 1, lineStyle: 3,
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
          saveDraw({ type: tool, t1: pendingPt.current.time, p1: pendingPt.current.price,
                     t2: p.time, p2: price });
          pendingPt.current = null;
        }
      }
    });
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() =>
      chart.applyOptions({ width: holder.current?.clientWidth ?? 500 }));
    ro.observe(holder.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [view, inds, tf, tool, drawKey, det, replayIdx]);

  useEffect(() => { const c = draw(); return c; }, [draw]);

  return (
    <div className="tbl-wrap" style={{ padding: '10px 12px' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 6 }}>
        <form onSubmit={(e) => { e.preventDefault(); setSymbol(symInput.toUpperCase()); }}>
          <input aria-label="Symbol" value={symInput}
            onChange={(e) => setSymInput(e.target.value)}
            style={{ width: 90, background: 'var(--bg-panel)', border: '1px solid var(--line)',
                     color: 'var(--text)', borderRadius: 7, padding: '5px 9px',
                     fontFamily: 'var(--mono)', fontWeight: 700 }} />
        </form>
        {(['5min', '1hour', 'daily'] as const).map((t) => (
          <button key={t} className={`ptab ${tf === t ? 'active' : ''}`}
            style={{ padding: '4px 10px', fontSize: 11 }} onClick={() => setTf(t)}>{t}</button>
        ))}
        <span className={`fresh ${quality === 'LIVE' ? 'ok' : 'stale'}`}>{quality}</span>
        <label style={{ fontSize: 10.5, color: analyze ? 'var(--buy)' : 'var(--text-faint)', cursor: 'pointer' }}
          title="Automatically detect and draw support/resistance, trendlines, double tops/bottoms, compressions, and volume-confirmed breakout/breakdown signals (deterministic, non-repainting)">
          <input type="checkbox" checked={analyze} style={{ marginRight: 3 }}
            onChange={(e) => setAnalyze(e.target.checked)} />🔍 Auto-detect
        </label>
        <span className="spacer" style={{ flex: 1 }} />
        {IND_DEFS.map(([k, label]) => (
          <label key={k} style={{ fontSize: 10.5, color: inds[k] ? 'var(--accent)' : 'var(--text-faint)',
                                   cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" checked={inds[k]} style={{ marginRight: 3 }}
              onChange={(e) => setInds((s) => ({ ...s, [k]: e.target.checked }))} />
            {label}
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 6, alignItems: 'center' }}>
        {(['none', 'hline', 'trend', 'fib'] as Tool[]).map((t) => (
          <button key={t} className={`ptab ${tool === t ? 'active' : ''}`}
            style={{ padding: '3px 9px', fontSize: 10.5 }}
            title={t === 'trend' || t === 'fib' ? 'click two points' : ''}
            onClick={() => { setTool(t); pendingPt.current = null; }}>
            {t === 'none' ? '↖ cursor' : t === 'hline' ? '― h-line' : t === 'trend' ? '╱ trend' : '𝑓 fib'}
          </button>
        ))}
        <button className="ptab" style={{ padding: '3px 9px', fontSize: 10.5 }}
          onClick={() => { try { localStorage.removeItem(drawKey); } catch {} draw(); }}>✕ clear</button>
        <span className="spacer" style={{ flex: 1 }} />
        <button className="ptab" style={{ padding: '3px 9px', fontSize: 10.5 }}
          title="Bar replay: hide future candles and step through history"
          onClick={() => { setReplayIdx(replayIdx === null ? Math.max(10, bars.length - 60) : null); setPlaying(false); }}>
          {replayIdx === null ? '◂ replay' : 'exit replay'}
        </button>
        {replayIdx !== null && (<>
          <button className="ptab" style={{ padding: '3px 8px' }} onClick={() => setReplayIdx((i) => Math.max(10, (i ?? 0) - 1))}>‹</button>
          <button className="ptab" style={{ padding: '3px 8px' }} onClick={() => setPlaying((p) => !p)}>{playing ? '⏸' : '▶'}</button>
          <button className="ptab" style={{ padding: '3px 8px' }} onClick={() => setReplayIdx((i) => Math.min(bars.length, (i ?? 0) + 1))}>›</button>
          <span className="faint" style={{ fontSize: 10 }}>{replayIdx}/{bars.length}</span>
        </>)}
      </div>
      {det && (det.patterns?.length || det.signals?.length) ? (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          {(det.patterns ?? []).map((p: any, i: number) => (
            <span key={`p${i}`} className="badge warn" title={p.note}>{p.type.replace(/_/g, ' ')}</span>
          ))}
          {(det.signals ?? []).slice(-4).map((s: any, i: number) => (
            <span key={`s${i}`} className={`badge ${s.kind.startsWith('buy') ? 'buy' : 'risk'}`}
              title={s.reason}>{s.kind.replace(/_/g, ' ')} @{s.price}</span>
          ))}
        </div>
      ) : null}
      <div ref={holder} style={{ width: '100%' }} />
    </div>
  );
}
