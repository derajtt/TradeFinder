'use client';
import c from '../app/controls.module.css';

export interface SegOption<K extends string> {
  key: K; label: string;
  /** disabled with a visible 12px reason — never a hover-only explanation */
  disabled?: boolean; note?: string;
}

/** Labelled segmented control: "Show: Buys | Watches | Both". Uses the global
 *  `.tab` buttons with `aria-pressed`; the label is always visible. */
export function Seg<K extends string>({ label, value, options, onChange }: {
  label: string; value: K; options: SegOption<K>[]; onChange: (k: K) => void;
}) {
  const note = options.find((o) => o.disabled && o.note)?.note;
  return (
    <div className={c.group} role="group" aria-label={label}>
      <span className={c.label}>{label}:</span>
      <div className={c.seg}>
        {options.map((o) => (
          <button key={o.key} type="button" className={`tab${value === o.key ? ' on' : ''}`}
            aria-pressed={value === o.key} disabled={o.disabled}
            onClick={() => onChange(o.key)}>{o.label}</button>
        ))}
      </div>
      {note ? <span className={c.hint}>{note}</span> : null}
    </div>
  );
}

/** A real switch with its state written out ("On" / "Off"). */
export function Switch({ label, checked, onChange, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
      <span className={c.state}>{checked ? 'On' : 'Off'}</span>
      {hint ? <span className={c.hint}>{hint}</span> : null}
    </label>
  );
}

/** Labelled text input for filter bars. */
export function LabelledInput({ label, value, onChange, placeholder, mono, width }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; mono?: boolean; width?: number;
}) {
  return (
    <label className={c.group}>
      <span className={c.label}>{label}</span>
      <input className={`input${mono ? '' : ' sans'}`} value={value} placeholder={placeholder}
        style={width ? { width } : undefined}
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
