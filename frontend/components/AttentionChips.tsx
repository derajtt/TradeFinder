'use client';
import { ageSeconds, fmtPrice } from '../lib/format';
import { useMarketPhase, useScannerState } from '../lib/status';
import type { AlertRow, Noon, Position, SignalRow } from '../lib/types';
import type { Tone } from '../lib/vocab';
import s from './today.module.css';
import { NOON_MIN, etMinutes, useEtNow } from './todayShared';
import { StatusPill } from './ui/StatusPill';

export interface AttentionChipsProps {
  /** signal rows already filtered to Buy picks / watches (active) */
  buys: SignalRow[]; watches: SignalRow[];
  positions: Position[]; alerts: AlertRow[]; noon: Noon | null;
  /** settings.quote_freshness_sec (fallback 180) */
  quoteFreshnessSec: number | null | undefined;
  loaded: boolean;
}

interface Chip { key: string; label: string; tone: Tone; href: string; glow?: boolean }

const MAX_CHIPS = 4;
const DAY_MS = 24 * 3600 * 1000;
const STALE_SHARE = 0.3;

/** The only element on the page allowed to glow. Priority order from spec §2.1;
 *  at most four chips, the rest collapse into "+N". */
export default function AttentionChips(props: AttentionChipsProps) {
  const { buys, watches, positions, alerts, noon, quoteFreshnessSec, loaded } = props;
  const phase = useMarketPhase();
  const scanner = useScannerState();
  const now = useEtNow(30000);

  if (!loaded || !now) {
    return (
      <div className={s.chips} aria-busy="true">
        <span className={`pill pill--sm pill--neutral skel`} style={{ width: 120, color: 'transparent' }}>…</span>
      </div>
    );
  }

  const chips: Chip[] = [];

  if (scanner.key === 'problem' || scanner.key === 'unreachable') {
    chips.push({ key: 'scanner', label: scanner.label, tone: 'risk', href: '/health' });
  }
  if (scanner.key === 'paused') {
    chips.push({ key: 'paused', label: 'Scanner paused', tone: 'warn', href: '/settings' });
  }

  const scanning = phase.isPremarket || phase.isOpen;
  if (scanning && watches.length) {
    const limit = quoteFreshnessSec ?? 180;
    const stale = watches.filter((r) => { const a = ageSeconds(r.current_ts); return a !== null && a > limit; }).length;
    if (stale / watches.length >= STALE_SHARE) {
      chips.push({ key: 'stale', label: `Quotes stale · ${stale} of ${watches.length} watched`, tone: 'warn', href: '#scanner' });
    }
  }

  if (buys.length > 0) {
    chips.push({ key: 'buys', label: `${buys.length} buy pick${buys.length === 1 ? '' : 's'} live`, tone: 'buy', href: '#buys', glow: true });
  }

  const open = positions.filter((p) => p.status === 'open');
  if (open.length > 0) {
    const stopsSet = open.every((p) => p.stop != null);
    chips.push({
      key: 'positions',
      label: `${open.length} open paper trade${open.length === 1 ? '' : 's'}${stopsSet ? ' · stops set' : ''}`,
      tone: 'accent', href: '#positions',
    });
  }

  const fired = alerts
    .filter((a) => a.fired_at && Date.now() - Date.parse(a.fired_at) < DAY_MS)
    .sort((a, b) => Date.parse(b.fired_at as string) - Date.parse(a.fired_at as string))[0];
  if (fired) {
    chips.push({ key: 'alert', label: `Alert: ${fired.symbol} ${fired.condition} ${fmtPrice(fired.price)}`, tone: 'accent', href: '/watchlists' });
  }

  if (etMinutes(now) >= NOON_MIN && noon && noon.denominator > 0 && noon.call_win_rate != null) {
    chips.push({ key: 'noon', label: `Noon check locked · ${Math.round(noon.call_win_rate * 100)}% green`, tone: 'neutral', href: '#trust' });
  }

  if (!chips.length) {
    return <div className={s.chips}><span className={s.chipsQuiet}>Nothing needs your attention right now</span></div>;
  }
  const shown = chips.slice(0, MAX_CHIPS);
  const overflow = chips.length - shown.length;
  return (
    <div className={s.chips} role="list" aria-label="Needs attention">
      {shown.map((c) => (
        <span role="listitem" key={c.key}>
          <StatusPill size="sm" label={c.label} tone={c.tone} href={c.href} glow={c.glow} />
        </span>
      ))}
      {overflow > 0 ? <span role="listitem"><StatusPill size="sm" label={`+${overflow}`} tone="neutral" /></span> : null}
    </div>
  );
}
