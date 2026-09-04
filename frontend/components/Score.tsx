'use client';
import { ScorePill } from './ui/ScorePill';

export { ScorePill };

/** @deprecated use `ScorePill` from `components/ui`. Default export kept so
 *  existing tables compile; renders the same pill (words in Simple). */
export default function Score({ v, minBuy }: { v: number | null | undefined; minBuy?: number }) {
  return <ScorePill value={v} minBuy={minBuy} />;
}
