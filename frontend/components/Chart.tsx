'use client';
import { useEffect, useRef } from 'react';
import { createChart, ColorType, type IChartApi, type UTCTimestamp } from 'lightweight-charts';

export interface Bar { time: number; open: number; high: number; low: number; close: number; volume: number; }

export default function Chart({ bars, buyPrice, buyTime, vwap, pmHigh, pmLow, watchStart,
  stop, target1, target2 }: {
  bars: Bar[]; buyPrice?: number | null; buyTime?: number | null;
  vwap?: number | null; pmHigh?: number | null; pmLow?: number | null;
  watchStart?: number | null; stop?: number | null; target1?: number | null;
  target2?: number | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart = createChart(ref.current, {
      height: 320,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8b98b4', fontSize: 11 },
      grid: { vertLines: { color: '#182135' }, horzLines: { color: '#182135' } },
      rightPriceScale: { borderColor: '#1e2942' },
      timeScale: { borderColor: '#1e2942', timeVisible: true, secondsVisible: false },
      crosshair: { horzLine: { color: '#38bdf8' }, vertLine: { color: '#38bdf8' } },
    });
    chartRef.current = chart;
    const candles = chart.addCandlestickSeries({
      upColor: '#34d399', downColor: '#f87171', borderVisible: false,
      wickUpColor: '#34d399', wickDownColor: '#f87171',
    });
    candles.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
    const vol = chart.addHistogramSeries({ priceScaleId: 'vol', color: 'rgba(56,189,248,0.35)' });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.time as UTCTimestamp, value: b.volume,
      color: b.close >= b.open ? 'rgba(52,211,153,0.4)' : 'rgba(248,113,113,0.4)' })));

    const line = (price: number, color: string, title: string, style = 2) =>
      candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
    if (buyPrice) line(buyPrice, '#34d399', 'BUY', 0);
    if (stop) line(stop, '#f87171', 'STOP', 0);
    if (target1) line(target1, '#34d399', 'T1');
    if (target2) line(target2, '#34d399', 'T2');
    if (vwap) line(vwap, '#38bdf8', 'VWAP');
    if (pmHigh) line(pmHigh, '#fbbf24', 'PM H');
    if (pmLow) line(pmLow, '#8b98b4', 'PM L');
    const markers: Parameters<typeof candles.setMarkers>[0] = [];
    if (watchStart && bars.length) {
      const nearest = bars.reduce((a, b) =>
        Math.abs(b.time - watchStart) < Math.abs(a.time - watchStart) ? b : a);
      markers.push({ time: nearest.time as UTCTimestamp, position: 'aboveBar',
        color: '#38bdf8', shape: 'circle', text: 'WATCH' });
    }
    if (buyTime) {
      markers.push({ time: buyTime as UTCTimestamp, position: 'belowBar',
        color: '#34d399', shape: 'arrowUp', text: 'BUY' });
    }
    if (markers.length) candles.setMarkers(markers.sort((a, b) => (a.time as number) - (b.time as number)));
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.applyOptions({ width: ref.current?.clientWidth ?? 600 }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  }, [bars, buyPrice, buyTime, vwap, pmHigh, pmLow, watchStart, stop, target1, target2]);

  if (!bars.length) {
    return <div className="empty" style={{ border: '1px solid var(--line)', borderRadius: 12 }}>
      <b>No accumulated bars yet</b>
      Minute bars build from live observations while the scanner runs (the current FMP plan does not include historical 1-min bars).
    </div>;
  }
  return <div ref={ref} style={{ width: '100%' }} aria-label="Price chart" role="img" />;
}
