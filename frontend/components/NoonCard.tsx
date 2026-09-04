'use client';
import type { Noon } from '../lib/types';
import { NOON_CLASS } from '../lib/vocab';
import s from './today.module.css';
import { StatusPill, pillFor } from './ui/StatusPill';

const MAX_SYMBOLS = 12;

/** Advanced-only noon detail under TrustTiles: how the locked picks split by
 *  class, which symbols they were, and the backend's own note. `policy` is never shown. */
export default function NoonCard({ noon, loaded }: { noon: Noon | null; loaded: boolean }) {
  if (!loaded) {
    return <div className={s.panel} aria-busy="true"><span className={`skel ${s.sk}`} style={{ width: 240 }} /></div>;
  }
  if (!noon || !noon.rows?.length) {
    return <div className={s.panel}><span className={s.noonNote}>Noon check detail · All models — no picks have been locked at noon yet.</span></div>;
  }
  const classes = Object.entries(noon.counts ?? {}).filter(([, v]) => v > 0);
  const symbols = noon.rows.slice(0, MAX_SYMBOLS);
  return (
    <div className={s.panel} aria-label="Noon check detail">
      <span className="eyebrow">Noon check detail · All models</span>
      <div className={s.noonRow}>
        {classes.map(([k, v]) => {
          const p = pillFor(NOON_CLASS, k);
          return <StatusPill key={k} size="sm" tone={p.tone} raw={p.raw} label={`${p.label} · ${v}`} />;
        })}
      </div>
      <div className={s.noonNote}>Most recent {symbols.length} of the {noon.rows.length} latest locked picks:</div>
      <div className="chips">
        {symbols.map((r, i) => {
          const p = pillFor(NOON_CLASS, r.class);
          return <span key={`${r.symbol}-${i}`} className="chip"><span className="sym">{r.symbol}</span> {p.label}</span>;
        })}
        {noon.rows.length > MAX_SYMBOLS ? <span className="chip">+{noon.rows.length - MAX_SYMBOLS} more</span> : null}
      </div>
      {noon.note ? <p className={s.noonNote}>{noon.note}</p> : null}
    </div>
  );
}
