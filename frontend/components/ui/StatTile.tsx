'use client';
import Link from 'next/link';
import { sampleClass, sampleNote, type BacktestSplit, type Evidence } from '../../lib/evidence';
import type { Tone } from '../../lib/vocab';
import { EvidenceTag } from './EvidenceTag';
import { Term } from './Popover';

export interface StatTileProps {
  label: string;                       // plain English, sentence case
  value: React.ReactNode | null;       // null → "—"
  n: number | null | undefined;        // population; drives the sample rule
  unit?: string;                       // 'trades' (default) | 'picks' | 'stocks'
  nLabel?: React.ReactNode;            // overrides "{n} {unit}"
  source: string;                      // "Paper account · Primary model · Buy picks only"
  evidence: Evidence;                  // required — renders EvidenceTag
  split?: BacktestSplit;
  tone?: Tone;
  sub?: React.ReactNode;               // one extra 12px line
  term?: string;                       // glossary key → label gets a Term popover
  loaded?: boolean;                    // default true; false → skeleton, never "0"
  size?: 'md' | 'lg';                  // 26px / 32px value
  href?: string;                       // whole tile is a link
  id?: string;
  paperMode?: boolean;                 // only consulted for evidence="LIVE"
}

/** The one place a number is rendered with its label, n, source and evidence.
 *  Sample rule: n 0/null → "—" + "No {unit} yet"; n < 10 → value dimmed and the
 *  warning becomes the headline; n < 30 → amber warning line under the value. */
export function StatTile(props: StatTileProps) {
  const { label, value, n, unit = 'trades', nLabel, source, evidence, split, tone, sub, term,
    loaded = true, size = 'md', href, id, paperMode } = props;

  if (!loaded) {
    return (
      <div className={`stat skel-tile${size === 'lg' ? ' stat--lg' : ''}`} id={id} aria-busy="true">
        <div className="stat-label">{label}</div>
        <div className="stat-value skel">&nbsp;</div>
        <div className="stat-n skel">&nbsp;</div>
        <div className="stat-src">{source}</div>
      </div>
    );
  }

  const cls = sampleClass(n);
  const note = sampleNote(n, unit);
  const shown = value === null || value === undefined || value === '' ? '—' : value;
  const labelNode = term ? <Term k={term}>{label}</Term> : label;
  const toneCls = tone ? ` stat--${tone}` : '';

  let headline: React.ReactNode;
  let nLine: React.ReactNode;
  let warnLine: React.ReactNode = null;
  if (cls === 'none') {
    headline = <span className="stat-dim">—</span>;
    nLine = nLabel ?? note;
  } else if (cls === 'tiny') {
    headline = <span className="stat-warn-h">{note}</span>;
    nLine = <><span className="stat-dim">{shown}</span> · {nLabel ?? `${n} ${unit}`}</>;
  } else {
    headline = shown;
    nLine = nLabel ?? `${n} ${unit}`;
    if (cls === 'small') warnLine = <div className="stat-warn">{note}</div>;
  }

  const body = (
    <>
      <EvidenceTag evidence={evidence} split={split} paperMode={paperMode} />
      <div className="stat-label">{labelNode}</div>
      <div className={`stat-value${cls === 'tiny' || cls === 'none' ? ' stat--dim' : ''}`}>{headline}</div>
      <div className="stat-n">{nLine}</div>
      {warnLine}
      <div className="stat-src">{source}</div>
      {sub ? <div className="stat-sub">{sub}</div> : null}
    </>
  );
  const className = `stat${size === 'lg' ? ' stat--lg' : ''}${toneCls}${href ? ' stat--link' : ''}`;
  if (href) return <Link href={href} className={className} id={id}>{body}</Link>;
  return <div className={className} id={id}>{body}</div>;
}
