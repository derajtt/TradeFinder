'use client';
import { useState } from 'react';
import ChartPane from '../../components/ChartPane';

export default function ChartWorkstation() {
  const [layout, setLayout] = useState<1 | 2 | 4>(1);
  const defaults = ['SPY', 'QQQ', 'NVDA', 'BTCUSD'];
  return (
    <>
      <div className="sect">
        <h2>Chart Workstation</h2>
        <span className="meta">multi-layout charting · deterministic indicators · drawings persist per symbol · bar replay hides future candles</span>
        <span className="spacer" />
        {( [1, 2, 4] as const).map((n) => (
          <button key={n} className={`ptab ${layout === n ? 'active' : ''}`}
            style={{ padding: '4px 12px' }} onClick={() => setLayout(n)}>{n}-up</button>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 14,
        gridTemplateColumns: layout === 1 ? '1fr' : 'repeat(2, minmax(0, 1fr))' }}>
        {Array.from({ length: layout }, (_, i) => (
          <ChartPane key={i} paneId={`p${i}`} defaultSymbol={defaults[i]} />
        ))}
      </div>
      <p className="disclaimer">Charts render provider bars with locally computed indicators (causal — nothing repaints).
        Crypto symbols use FMP aggregate quotes, which are not an executable venue order book.</p>
    </>
  );
}
