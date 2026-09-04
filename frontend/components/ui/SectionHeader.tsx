'use client';
import type { BacktestSplit, Evidence } from '../../lib/evidence';
import { EvidenceTag } from './EvidenceTag';

export interface SectionHeaderProps {
  title: React.ReactNode;              // the heading text (h2 by default)
  question: string;                    // plain-English question, 13px --text-dim under the heading (hidden in Advanced via CSS)
  count?: number | null;               // appended "— {count}" ("— none" when 0)
  caption?: React.ReactNode;           // 12px source line ("Tracked · Primary model · …")
  evidence?: Evidence; split?: BacktestSplit;
  note?: React.ReactNode;              // 12px header note (score thresholds, suppression note)
  right?: React.ReactNode;             // actions slot
  id?: string; level?: 1 | 2;
}

/** Every section: a heading that is a plain sentence, the question it answers,
 *  and the source caption. Never carries `title=`. */
export function SectionHeader({ title, question, count, caption, evidence, split, note, right, id, level = 2 }: SectionHeaderProps) {
  const countText = count === undefined || count === null ? null : count === 0 ? ' — none' : ` — ${count}`;
  const heading = (
    <>
      {title}{countText}
      {evidence ? <> <EvidenceTag evidence={evidence} split={split} /></> : null}
    </>
  );
  return (
    <div className={`sect sect--hdr${level === 1 ? ' sect--h1' : ''}`} id={id}>
      <div className="sect-main">
        {level === 1 ? <h1 className="sect-title">{heading}</h1> : <h2 className="sect-title">{heading}</h2>}
        {question ? <div className="sect-q">{question}</div> : null}
        {caption ? <div className="sect-cap">{caption}</div> : null}
        {note ? <div className="sect-note">{note}</div> : null}
      </div>
      {right ? <div className="sect-right">{right}</div> : null}
    </div>
  );
}
