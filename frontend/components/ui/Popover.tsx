'use client';
import { useEffect, useId, useRef, useState } from 'react';
import { GLOSSARY, PLAIN, TERMS } from '../../lib/terms';

/** Click/focus-activated popover shared by Tip and Term. Text is not in the DOM until open. */
function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);
  const onBlur = (e: React.FocusEvent) => {
    if (ref.current && e.relatedTarget && ref.current.contains(e.relatedTarget as Node)) return;
    setOpen(false);
  };
  return { open, setOpen, ref, onBlur };
}

export type TipProps =
  /** spec shape: an ⓘ button next to a visible label */
  | { text: string; label?: string; term?: undefined; children?: undefined }
  /** @deprecated legacy shape `{term, children}` — kept so pages not yet migrated compile;
   *  the children become the click/focus trigger (no hover, no extra glyph). */
  | { term: string; children: React.ReactNode; text?: undefined; label?: undefined };

/** ⓘ button with a one-sentence popover. Supplementary only — never the sole
 *  carrier of a value, unit, denominator or warning. */
export function Tip(props: TipProps) {
  const { open, setOpen, ref, onBlur } = usePopover();
  const id = useId();
  const text = props.text ?? props.term;
  const legacy = props.text === undefined;
  return (
    <span className="tipwrap" ref={ref}>
      <button type="button" className={legacy ? 'tip-legacy' : 'tip-btn'}
        aria-label={legacy ? undefined : props.label ? `About ${props.label}` : 'What does this mean?'}
        aria-expanded={open} aria-describedby={open ? id : undefined}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        onFocus={() => setOpen(true)} onBlur={onBlur}>
        {legacy ? props.children : 'ⓘ'}
      </button>
      {open && <span role="tooltip" id={id} className="popover">{text}</span>}
    </span>
  );
}

export function termText(k: string): string | undefined {
  return PLAIN[k] ?? TERMS[k] ?? GLOSSARY[k];
}

/** Dotted glossary word; popover text = PLAIN[k] ?? TERMS[k] ?? GLOSSARY[k].
 *  Children default to the key itself. Without a definition it renders plain text. */
export function Term({ k, children }: { k: string; children?: React.ReactNode }) {
  const { open, setOpen, ref, onBlur } = usePopover();
  const id = useId();
  const text = termText(k);
  const word = children ?? k;
  if (!text) return <span>{word}</span>;
  return (
    <span className="term" ref={ref}>
      <button type="button" className="term-btn" aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        onFocus={() => setOpen(true)} onBlur={onBlur}>{word}</button>
      {open && <span role="tooltip" id={id} className="popover">{text}</span>}
    </span>
  );
}
