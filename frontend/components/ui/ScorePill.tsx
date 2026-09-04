'use client';
import { useMode } from '../../lib/mode';
import { scoreBand } from '../../lib/vocab';

/** "72 / 100 · Strong" — words default on in Simple, off in Advanced.
 *  Bands: ≥ minBuy (default 75) Strong · ≥ 55 OK · else Weak. */
export function ScorePill({ value, minBuy = 75, words }: {
  value: number | null | undefined; minBuy?: number; words?: boolean;
}) {
  const { advanced } = useMode();
  if (value === null || value === undefined || Number.isNaN(value)) return <span className="dim">—</span>;
  const showWords = words ?? !advanced;
  const band = scoreBand(value, minBuy);
  const cls = band === 'Strong' ? 'score-hi' : band === 'OK' ? 'score-mid' : 'score-lo';
  // 54.6 must not read "55 · Weak": keep a decimal when rounding would cross a band edge.
  const rounded = Math.round(value);
  const shown = scoreBand(rounded, minBuy) === band ? rounded : value.toFixed(1);
  return (
    <span className={`score-pill ${cls}${showWords ? ' score-pill--words' : ''}`}>
      {shown}
      {showWords ? <span className="score-words"> / 100 · {band}</span> : null}
    </span>
  );
}
