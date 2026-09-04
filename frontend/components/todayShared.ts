'use client';
/** Small helpers shared by the Today page components (Group B): the ET clock,
 *  HH:MM parsing, plain-English rewrites of backend prose and scheduler event names. */
import { useEffect, useState } from 'react';

const ET = 'America/New_York';

export const PREMARKET_START_MIN = 4 * 60;      // 4:00 AM ET
export const MARKET_OPEN_MIN = 9 * 60 + 30;     // 9:30 AM ET
export const NOON_MIN = 12 * 60;                // 12:00 ET

/** Minutes since midnight in New York for `d` (default: now). */
export function etMinutes(d: Date = new Date()): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: ET, hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
  }).formatToParts(d);
  const h = Number(parts.find((p) => p.type === 'hour')?.value ?? 0);
  const m = Number(parts.find((p) => p.type === 'minute')?.value ?? 0);
  return h * 60 + m;
}

/** "07:00" → 420 · null when the string is not HH:MM. */
export function parseHm(s: string | null | undefined): number | null {
  if (!s) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(s.trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/** A local clock that is null until mounted (SSR-safe) and ticks every `intervalMs`. */
export function useEtNow(intervalMs = 30000): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Backend prose still carries a few enum tokens; rewrite them into plain words. */
export function plainProse(s: string | null | undefined): string {
  if (!s) return '';
  return String(s)
    .replace(/\bEARLY WATCH\b/g, 'Early watch')
    .replace(/\bACTIONABLE BUY\b/g, 'Buy pick')
    .replace(/\bEXPIRED\b/g, 'Expired')
    .replace(/\bBUY\b/g, 'Buy')
    .replace(/\bWATCH\b/g, 'Watch');
}

/** `/api/ops` `upcoming[].event` strings → the short plain label used after "Next:". */
const EVENT_LABEL: Record<string, string> = {
  'Premarket discovery begins': 'premarket scan',
  'Broker window opens — EARLY WATCH can convert to BUY': 'buys allowed (broker window opens)',
  'Last new premarket entry': 'last new premarket pick',
  'Regular session — intraday models activate': 'market open',
  'Noon outcomes lock': 'noon check',
  'Intraday paper positions time-exit': 'intraday paper trades close',
  'Nightly research replay': 'nightly research',
};
export function eventLabel(event: string | null | undefined): string {
  if (!event) return 'next scan';
  return EVENT_LABEL[event] ?? plainProse(event);
}
