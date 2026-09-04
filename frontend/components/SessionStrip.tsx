'use client';
import { useMarketPhase } from '../lib/status';
import s from './today.module.css';
import { MARKET_OPEN_MIN, PREMARKET_START_MIN, etMinutes, parseHm, useEtNow } from './todayShared';

const SPAN = MARKET_OPEN_MIN - PREMARKET_START_MIN;
const clamp = (v: number) => Math.max(0, Math.min(100, v));

/** How far into premarket are we, and when can a pick become a Buy?
 *  `confirmAt` is settings.buy_confirm_after_et — never hard-coded. The 28px row
 *  is always reserved; outside 3:30–10:00 ET it is a single 12px line. */
export default function SessionStrip({ confirmAt, loaded }: { confirmAt: string | null; loaded: boolean }) {
  const now = useEtNow(30000);
  const phase = useMarketPhase();

  if (!loaded || !now) {
    return <div className={s.reserved} aria-busy="true"><span className={`skel ${s.sk}`} style={{ width: 280 }} /></div>;
  }

  const buysFrom = confirmAt ? `buys from ${confirmAt} ET` : 'buy time unavailable (settings not loaded)';
  const mins = etMinutes(now);
  const inWindow = mins >= PREMARKET_START_MIN - 30 && mins <= MARKET_OPEN_MIN + 30
    && phase.key !== 'closed' && phase.key !== 'afterhours';
  if (!inWindow) {
    return <div className={s.reserved}>Premarket runs 4:00–9:30 AM ET · {buysFrom}</div>;
  }

  const pct = clamp(((mins - PREMARKET_START_MIN) / SPAN) * 100);
  const cm = parseHm(confirmAt);
  const confirmPct = cm !== null && cm > PREMARKET_START_MIN && cm < MARKET_OPEN_MIN
    ? ((cm - PREMARKET_START_MIN) / SPAN) * 100 : null;
  const toOpen = Math.max(0, MARKET_OPEN_MIN - mins);

  return (
    <div className={s.sessionWrap}>
      <div className="session-strip" role="img"
        aria-label={`${Math.round(pct)}% through premarket, ${toOpen} minutes to market open`}>
        <div className="session-track">
          <div className="session-fill" style={{ width: `${pct}%` }} />
          {confirmPct !== null ? (
            <div className="session-marker" style={{ left: `${confirmPct}%` }} data-label={`Buys allowed from ${confirmAt}`} />
          ) : null}
          <div className="session-now" style={{ left: `${pct}%` }} />
        </div>
      </div>
      <div className={s.sessionLabels}><span>4:00 AM premarket start</span><span>9:30 AM market open</span></div>
      <div className={s.sessionCap}>{Math.round(pct)}% through premarket · {toOpen} min to open · {buysFrom}</div>
    </div>
  );
}
