'use client';
import { useEffect, useState } from 'react';
import { ageSeconds, fmtEtClock } from '../lib/format';

/** Quote freshness as text — "as of 4:02 PM" — plus an optional amber dot.
 *  The dot (and the word "stale") appear only while the market is open and the
 *  quote is older than `thresholdSec` (settings.quote_freshness_sec, default 180),
 *  or when the backend flagged `fresh === false`. Never a `title`. */
export default function Freshness({ ts, fresh, marketOpen, thresholdSec = 180, dot = true }: {
  ts: string | null | undefined; fresh?: boolean | null;
  marketOpen?: boolean; thresholdSec?: number; dot?: boolean;
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!marketOpen) return;
    const id = setInterval(() => tick((x) => x + 1), 15000);
    return () => clearInterval(id);
  }, [marketOpen]);
  if (!ts) return <span className="fresh">no timestamp</span>;
  const age = marketOpen ? ageSeconds(ts) : null;
  const stale = fresh === false || (marketOpen === true && age !== null && age > thresholdSec);
  return (
    <span className={`fresh${stale ? ' stale' : ''}`}>
      {dot && stale ? <span className="fresh-dot" aria-hidden /> : null}
      as of {fmtEtClock(ts)}{stale ? ' · stale' : ''}
    </span>
  );
}
