'use client';
import Link from 'next/link';
import { fmtEtShort, fmtPct, fmtPrice } from '../lib/format';
import { useMode } from '../lib/mode';
import { useMarketPhase } from '../lib/status';
import type { SignalRow } from '../lib/types';
import PickCard from './PickCard';
import s from './today.module.css';
import { etMinutes, parseHm, plainProse, useEtNow } from './todayShared';
import { EmptyState } from './ui/EmptyState';
import { SectionHeader } from './ui/SectionHeader';
import { StatTile } from './ui/StatTile';

export interface BuyPicksProps {
  /** signal rows already filtered to `signal_type === 'buy' && status === 'active'` */
  rows: SignalRow[];
  loaded: boolean;
  scopeLabel: string;
  quietReason: string | null | undefined;      // ops.quiet_reason
  confirmAt: string | null;                    // settings.buy_confirm_after_et
  nextScanStart: string | null | undefined;    // status.next_scan_start
  onSelect: (r: SignalRow) => void;
  minScoreForBuy?: number;
  quoteFreshnessSec?: number;
}

const MAX_CARDS = 3;

/** Is there a stock to buy right now? The count here is the only number on the
 *  page allowed to be called "Buy picks". */
export default function BuyPicks(props: BuyPicksProps) {
  const { rows, loaded, scopeLabel, quietReason, confirmAt, nextScanStart, onSelect, minScoreForBuy, quoteFreshnessSec } = props;
  const phase = useMarketPhase();
  const { advanced } = useMode();
  const now = useEtNow(60000);
  const marketOpen = phase.isOpen || phase.isPremarket;

  // settings.buy_confirm_after_et is read, never hard-coded; when it has not loaded the sentence says so
  const fromClause = confirmAt ? `from ${confirmAt} ET` : 'from the configured buy time (settings not loaded)';
  const confirmMin = parseHm(confirmAt);
  const mins = now ? etMinutes(now) : null;
  let next: string;
  if (phase.isPremarket && confirmMin !== null && mins !== null && mins < confirmMin) {
    next = `Buys are allowed ${fromClause} — early qualifiers show as Early watch below`;
  } else if (phase.isPremarket || phase.isOpen) {
    next = 'The scanner is running; a stock becomes a Buy pick the moment every check passes';
  } else {
    next = `Buys can appear ${fromClause} during the 4:00–9:30 premarket scan (next scan ${fmtEtShort(nextScanStart)})`;
  }

  const best = rows.reduce<SignalRow | null>((a, b) =>
    (b.change_pct ?? -Infinity) > (a?.change_pct ?? -Infinity) ? b : a, null);

  return (
    <section aria-labelledby="buys-title">
      <SectionHeader id="buys" title={<span id="buys-title">Buy picks right now</span>}
        count={loaded ? rows.length : undefined}
        question="Is there a stock to buy right now?"
        caption={`${scopeLabel} · passed every check · paper plan only`} evidence="TRACKED" />

      {!loaded ? (
        <EmptyState loaded={false} headline="" reason={null} />
      ) : rows.length === 0 ? (
        <EmptyState headline="Nothing to buy right now"
          reason={quietReason ? plainProse(quietReason) : null}
          next={next}
          action={{ label: 'See what’s being watched ↓', href: '#watching' }} />
      ) : (
        <>
          <div className={s.picks}>
            {rows.slice(0, MAX_CARDS).map((r) => (
              <PickCard key={r.signal_uid} row={r} onSelect={onSelect} marketOpen={marketOpen}
                minScoreForBuy={minScoreForBuy} quoteFreshnessSec={quoteFreshnessSec} />
            ))}
          </div>
          {rows.length > MAX_CARDS ? (
            <Link className={s.more} href="/signals?type=buy&status=active">+{rows.length - MAX_CARDS} more Buy picks →</Link>
          ) : null}
        </>
      )}

      {advanced && loaded ? (
        <div className={`stat-grid ${s.bestWrap}`}>
          <StatTile label="Best open Buy pick"
            value={best ? `${best.symbol} ${fmtPct(best.change_pct)}` : null}
            n={rows.length} unit="picks" nLabel={`${rows.length} ${rows.length === 1 ? 'pick' : 'picks'}`}
            source={`Tracked · ${scopeLabel} · open Buy picks only`} evidence="TRACKED"
            sub={best ? `Buy price ${fmtPrice(best.buy_price)} → now ${fmtPrice(best.current)}` : undefined} />
        </div>
      ) : null}
    </section>
  );
}
