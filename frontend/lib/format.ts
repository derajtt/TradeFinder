import { drawdownLabel, type DrawdownBasis } from './evidence';

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const abs = Math.abs(v);
  const digits = abs < 1 ? 4 : 2;
  return (v < 0 ? '-$' : '$') + abs.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const s = signed && v > 0 ? '+' : '';
  return s + v.toFixed(2) + '%';
}

export function fmtCompact(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e12) return (v / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (abs >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}

/** Loose money formatter (accepts strings/unknown). Shared by the roadmap cards. */
export function money(v: unknown, dp = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const abs = Math.abs(n);
  return (n < 0 ? '-$' : '$') + abs.toLocaleString('en-US',
    { minimumFractionDigits: dp, maximumFractionDigits: abs < 1 ? 6 : dp });
}
export const fmtMoney = money;

const ET = 'America/New_York';

/** "07:12:05 ET" (24h, with seconds). Kept for Advanced-only detail rows. */
export function fmtEt(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      timeZone: ET, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }) + ' ET';
  } catch { return '—'; }
}

/** "Sep 04, 07:12 ET" */
export function fmtEtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('en-US', {
      timeZone: ET, month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }) + ' ET';
  } catch { return '—'; }
}

/** "4:02 PM" — a clock reading in ET; seconds optional. */
export function fmtEtClock(iso: string | null | undefined, opts: { seconds?: boolean } = {}): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('en-US', {
      timeZone: ET, hour: 'numeric', minute: '2-digit',
      ...(opts.seconds ? { second: '2-digit' } : {}), hour12: true,
    });
  } catch { return '—'; }
}

/** "Tue 7:12 AM ET" */
export function fmtEtShort(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    const day = d.toLocaleDateString('en-US', { timeZone: ET, weekday: 'short' });
    const time = d.toLocaleTimeString('en-US', { timeZone: ET, hour: 'numeric', minute: '2-digit', hour12: true });
    return `${day} ${time} ET`;
  } catch { return '—'; }
}

/** 'YYYY-MM-DD' → "Thu Sep 4" (no timezone shift). */
export function fmtDateLabel(ymd: string | null | undefined): string {
  if (!ymd) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd);
  if (!m) return ymd;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12));
  return d.toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' });
}

/** "+0.4R" / "-1.0R" */
export function fmtR(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (v > 0 ? '+' : '') + v.toFixed(digits) + 'R';
}

/** "3.2×" */
export function fmtMult(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toFixed(digits) + '×';
}

/** "12s ago" · "3 min ago" · "2 h ago" · "3 d ago" */
export function fmtAgo(iso: string | null | undefined): string {
  const age = ageSeconds(iso);
  if (age === null) return '—';
  if (age < 60) return `${Math.round(age)}s ago`;
  if (age < 3600) return `${Math.round(age / 60)} min ago`;
  if (age < 86400) return `${Math.round(age / 3600)} h ago`;
  return `${Math.round(age / 86400)} d ago`;
}

/** "12.3% of $10,000 account" · "8.0% sum of trade %". Never a bare drawdown. */
export function fmtDrawdown(v: number | null | undefined, basis: DrawdownBasis, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${Math.abs(v).toFixed(digits)}% ${drawdownLabel(basis)}`;
}

export function ageSeconds(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, (Date.now() - t) / 1000);
}
