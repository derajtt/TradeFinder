'use client';
import { fmtDateLabel, fmtEtShort } from '../lib/format';
import { useMarketPhase, useScannerState, useStatus } from '../lib/status';
import type { Brief, Canonical, Digest, Ops } from '../lib/types';
import { gateLabel } from '../lib/vocab';
import s from './today.module.css';
import { eventLabel, plainProse } from './todayShared';
import { Advanced } from './ui/Advanced';

export interface StatusLineProps {
  /** `/api/ops` from the page's `useOps()` — never polled here. */
  ops: Ops | null; opsLoaded: boolean;
  /** Advanced-only payloads the page fetches only in Advanced (null in Simple). */
  digest?: Digest | null; brief?: Brief | null; canonicalAll?: Canonical | null;
}

/** Advanced third line: morning brief + digest prose, guarded against the
 *  canonical (all-model) Buy-pick count so a mismatch is visible, never silent. */
function AdvancedLine({ digest, brief, canonicalAll }: Pick<StatusLineProps, 'digest' | 'brief' | 'canonicalAll'>) {
  const parts: React.ReactNode[] = [];
  if (brief?.available && brief.content) {
    const top = brief.content.top_rejection_reasons?.[0];
    parts.push(
      <span key="brief">
        <b>Morning brief ({fmtDateLabel(brief.session_date)}):</b> {plainProse(brief.content.headline) || '—'}
        {top ? <> · Most common block: {gateLabel(top[0])} — {top[1]} stocks</> : null}
      </span>,
    );
  }
  if (digest?.line) {
    const canonBuys = canonicalAll?.lifecycle_counts?.ACTIONABLE_BUY;
    const digestBuys = digest.today?.buys;
    const mismatch = canonBuys != null && digestBuys != null && digestBuys !== canonBuys;
    parts.push(
      <span key="digest">
        {plainProse(digest.line)}
        {mismatch ? <span className="faint"> (digest counts include all models and early/watching signals)</span> : null}
      </span>,
    );
  }
  if (!parts.length) return null;
  return <div className={s.line3}>{parts}</div>;
}

/** Is the machine on, what happens next, and is anything wrong? Built from
 *  structured fields only — the prose `digest.line` is never parsed. Line 2 is
 *  always reserved; the component never returns null. */
export default function StatusLine({ ops, opsLoaded, digest, brief, canonicalAll }: StatusLineProps) {
  const { status, loaded: statusLoaded } = useStatus();
  const phase = useMarketPhase();
  const scanner = useScannerState();
  const ready = statusLoaded && opsLoaded;

  const upcoming = ops?.upcoming?.[0];
  const nextLabel = upcoming ? eventLabel(upcoming.event) : 'next scan';
  const nextWhen = fmtEtShort(upcoming?.at_et ?? status?.next_scan_start);
  const quiet = opsLoaded ? plainProse(ops?.quiet_reason) : '';

  return (
    <div className={s.status} aria-live="polite">
      <div className={s.line1}>
        {ready ? (
          <>
            <span>{phase.label}</span>
            <span className={s.sep} aria-hidden>·</span>
            <span>{scanner.label}</span>
            <span className={s.sep} aria-hidden>·</span>
            <span>Next: {nextLabel} <span className="dim">{nextWhen}</span></span>
          </>
        ) : (
          <span className={`skel ${s.sk}`} style={{ width: 360 }} aria-busy="true" />
        )}
      </div>
      <div className={s.line2}>{quiet}</div>
      <Advanced>
        <AdvancedLine digest={digest} brief={brief} canonicalAll={canonicalAll} />
      </Advanced>
    </div>
  );
}
