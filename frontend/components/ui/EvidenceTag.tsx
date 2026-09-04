'use client';
import { useEffect } from 'react';
import { evidenceLabel, type BacktestSplit, type Evidence } from '../../lib/evidence';

/** The cohort chip every stat/table carries. LIVE reads "Live (real money)" only
 *  when paperMode === false; otherwise it falls back to Paper (dev console.warn). */
export function EvidenceTag({ evidence, split, paperMode }: {
  evidence: Evidence; split?: BacktestSplit; paperMode?: boolean;
}) {
  const downgraded = evidence === 'LIVE' && paperMode !== false;
  useEffect(() => {
    if (downgraded && process.env.NODE_ENV !== 'production') {
      console.warn('[EvidenceTag] LIVE requested while status.paper_mode !== false — rendering "Paper".');
    }
  }, [downgraded]);
  const e: Evidence = downgraded ? 'PAPER' : evidence;
  return (
    <span className={`evtag evtag--${e.toLowerCase()}`}>
      {evidenceLabel(e, e === 'BACKTEST' ? split : undefined, paperMode)}
    </span>
  );
}
