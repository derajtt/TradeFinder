'use client';
import { useState } from 'react';

const ROWS: [string, string, string][] = [
  ['Momentum & volume', '30', 'RVOL vs its 10-session baseline, 5-min volume acceleration, gap size, higher-highs/higher-lows'],
  ['Catalyst quality', '25', 'AI-classified materiality × novelty × confidence of news/filings, with an original source link'],
  ['SEC filing', '15', 'Positive 8-K items, clean recent filing context, insider Form 4 activity'],
  ['Liquidity / execution', '10', 'Premarket dollar volume, tight spread, fresh quotes'],
  ['Price confirmation', '10', 'Holding above VWAP and near the premarket high'],
  ['Company quality', '10', 'Market cap floor, known (low) float, revenue-producing sector'],
];
const PENALTIES = 'Penalties subtract: dilution −10..30 · going concern −15 · no catalyst −15 · reverse split −10 · recycled news −10 · extreme extension −10. Hard blocks (stale quote, wide spread, zero observed PM volume, halt, severe dilution) make BUY impossible regardless of score.';

export default function ScoringLegend() {
  const [open, setOpen] = useState(false);
  return (
    <span style={{ position: 'relative' }}>
      <button aria-label="How scoring works" onClick={() => setOpen((o) => !o)}
        style={{ background: 'var(--bg-hover)', color: 'var(--accent)', border: '1px solid var(--line)',
                 borderRadius: '50%', width: 20, height: 20, fontSize: 12, lineHeight: 1, marginLeft: 6, cursor: 'pointer' }}>?</button>
      {open && (
        <span role="tooltip" style={{ position: 'absolute', left: 0, top: 26, zIndex: 80, width: 340,
          background: 'var(--bg-panel)', border: '1px solid var(--line)', borderRadius: 10,
          padding: '12px 14px', display: 'block', fontSize: 12, boxShadow: '0 12px 30px rgba(0,0,0,.5)',
          textTransform: 'none', letterSpacing: 0, fontWeight: 400, color: 'var(--text)' }}>
          <b style={{ display: 'block', marginBottom: 8 }}>How the 100-point score works</b>
          {ROWS.map(([k, max, d]) => (
            <span key={k} style={{ display: 'block', marginBottom: 6, lineHeight: 1.45 }}>
              <b className="dim">{k}</b> <span className="faint">(max {max})</span><br />{d}
            </span>
          ))}
          <span className="faint" style={{ display: 'block', marginTop: 6, lineHeight: 1.45 }}>{PENALTIES}</span>
          <span className="faint" style={{ display: 'block', marginTop: 6 }}>BUY needs score ≥ 75 AND every gate in &quot;Path to BUY&quot;.</span>
        </span>
      )}
    </span>
  );
}
