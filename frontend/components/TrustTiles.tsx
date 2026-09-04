'use client';
import { wilsonLower } from '../lib/evidence';
import { useMode } from '../lib/mode';
import type { Canonical, Noon, StatusPayload } from '../lib/types';
import { plainProse } from './todayShared';
import { Term } from './ui/Popover';
import { SectionHeader } from './ui/SectionHeader';
import { StatTile } from './ui/StatTile';

export interface TrustTilesProps {
  canonical: Canonical | null;   // /api/report/canonical?profile= (model-scoped)
  status: StatusPayload | null;  // status.outcomes (all models)
  noon: Noon | null;             // /api/outcomes/noon (all models)
  scopeLabel: string;
  earlyWindowMin: number | null | undefined;   // settings.early_window_min
  loaded: boolean;
}

const pct = (v: number | null | undefined) => (v == null ? null : `${Math.round(v * 100)}%`);

/** Should I trust this model's picks? Exactly three StatTiles, three different
 *  cohorts, each naming its scope and population. Never mixed. */
export default function TrustTiles({ canonical, status, noon, scopeLabel, earlyWindowMin, loaded }: TrustTilesProps) {
  const { advanced } = useMode();

  // 1 · Paper trades — model-scoped, Buy picks only
  const perf = canonical?.actionable_buy_performance ?? null;
  const closed = perf?.closed_trades ?? null;
  const paperFloor = perf && perf.closed_trades > 0 ? wilsonLower(perf.wins, perf.closed_trades) : null;
  const calibrated = perf?.calibration === 'calibrated';
  const paperSubParts: React.ReactNode[] = [];
  if (perf && !calibrated) paperSubParts.push(perf.note ? plainProse(perf.note) : 'Not enough trades yet to trust this rate');
  if (advanced && paperFloor != null) {
    paperSubParts.push(<><Term k="conservative_floor">Conservative floor</Term> {pct(paperFloor)}</>);
  }

  // 2 · Early pops — all models, watches and buys
  const oc = status?.outcomes ?? null;
  const decided = oc ? oc.win + oc.loss : null;

  // 3 · Noon check — all models
  const noonWins = noon ? (noon.counts?.WIN_10_TOUCH ?? 0) + (noon.counts?.WIN_NOON_GREEN ?? 0) : 0;
  const noonFloor = noon && noon.denominator > 0 ? (wilsonLower(noonWins, noon.denominator) ?? noon.win_rate_lb) : null;
  const incomplete = noon?.counts?.INCOMPLETE ?? 0;

  return (
    <section aria-labelledby="trust-title">
      <SectionHeader id="trust" title={<span id="trust-title">How is this strategy doing?</span>}
        question="Should I trust this model's picks?" />
      <div className="stat-grid">
        <StatTile label="Paper trades" loaded={loaded}
          value={pct(perf?.win_rate) ? `${pct(perf?.win_rate)} won` : null}
          n={closed} unit="trades"
          nLabel={closed ? `${closed} ${closed === 1 ? 'trade' : 'trades'}` : undefined}
          source={`Paper account · ${scopeLabel} · Buy picks only`} evidence="PAPER"
          sub={paperSubParts.length ? paperSubParts.map((p, i) => <span key={i}>{i ? ' · ' : ''}{p}</span>) : undefined} />

        <StatTile label="Early pops" term="early_pop" loaded={loaded}
          value={pct(oc?.win_rate) ? `${pct(oc?.win_rate)} popped` : null}
          n={decided} unit="picks"
          nLabel={oc ? `of ${decided} decided picks · ${oc.neutral} flat not counted · ${oc.pending} pending` : undefined}
          source={`All models · watches and buys · judged in the first ${earlyWindowMin ?? '—'} min`} evidence="TRACKED" />

        <StatTile label="Noon check" term="noon_check" loaded={loaded}
          value={pct(noon?.call_win_rate) ? `${pct(noon?.call_win_rate)} green at noon` : null}
          n={noon?.denominator ?? null} unit="picks"
          nLabel={noon ? `of ${noon.denominator} picks${incomplete ? ` · ${incomplete} incomplete not counted` : ''}` : undefined}
          source="All models · was the pick above its pick price at 12:00 ET" evidence="TRACKED"
          sub={advanced && noonFloor != null ? <><Term k="conservative_floor">Conservative floor</Term> {pct(noonFloor)}</> : undefined} />
      </div>
    </section>
  );
}
