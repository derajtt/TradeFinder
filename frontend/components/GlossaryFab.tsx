'use client';
import { useMemo, useState } from 'react';
import { GLOSSARY } from '../lib/terms';

export default function GlossaryFab() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const entries = useMemo(() => {
    const all = Object.entries(GLOSSARY);
    if (!q.trim()) return all;
    const s = q.toLowerCase();
    return all.filter(([k, v]) => k.toLowerCase().includes(s) || v.toLowerCase().includes(s));
  }, [q]);
  return (
    <>
      <button aria-label="Glossary — what does this term mean?" title="Glossary: every acronym explained"
        onClick={() => setOpen((o) => !o)}
        style={{ position: 'fixed', right: 18, bottom: 18, zIndex: 90, width: 40, height: 40,
                 borderRadius: '50%', border: '1px solid var(--line)', cursor: 'pointer',
                 background: 'var(--accent)', color: '#04121d', fontWeight: 800, fontSize: 18,
                 boxShadow: '0 6px 20px rgba(56,189,248,0.4)' }}>?</button>
      {open && (
        <div role="dialog" aria-label="Glossary"
          style={{ position: 'fixed', right: 18, bottom: 68, zIndex: 91, width: 'min(420px, 92vw)',
                   maxHeight: '70vh', overflowY: 'auto', background: 'var(--bg-raise)',
                   border: '1px solid var(--line)', borderRadius: 14, padding: '14px 16px',
                   boxShadow: '0 20px 50px rgba(0,0,0,0.6)' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
            <b style={{ fontSize: 12, letterSpacing: 1 }}>GLOSSARY</b>
            <input autoFocus placeholder="Search any term…" value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ flex: 1, background: 'var(--bg-panel)', border: '1px solid var(--line)',
                       color: 'var(--text)', borderRadius: 8, padding: '6px 10px', fontSize: 12.5 }} />
            <button className="drawer-close" style={{ padding: '4px 9px' }} onClick={() => setOpen(false)}>✕</button>
          </div>
          {entries.map(([k, v]) => (
            <div key={k} style={{ padding: '7px 0', borderBottom: '1px solid var(--line-soft)' }}>
              <b style={{ fontSize: 12.5, color: 'var(--accent)' }}>{k}</b>
              <div className="dim" style={{ fontSize: 12, lineHeight: 1.5, marginTop: 2 }}>{v}</div>
            </div>
          ))}
          {!entries.length && <div className="faint" style={{ fontSize: 12 }}>No match — tell Claude to add it.</div>}
        </div>
      )}
    </>
  );
}
