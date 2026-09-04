'use client';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

export type Mode = 'simple' | 'advanced';

const KEY = 'tf_mode';
const EVT = 'tf-mode';

interface ModeCtx { mode: Mode; setMode: (m: Mode) => void; advanced: boolean; mounted: boolean }

const Ctx = createContext<ModeCtx>({ mode: 'simple', setMode: () => {}, advanced: false, mounted: false });

function isMode(v: unknown): v is Mode { return v === 'simple' || v === 'advanced'; }

function readStored(): Mode | null {
  try { const v = localStorage.getItem(KEY); return isMode(v) ? v : null; } catch { return null; }
}
function writeStored(m: Mode) {
  try { localStorage.setItem(KEY, m); } catch { /* private mode */ }
}
function applyDom(m: Mode) {
  if (typeof document !== 'undefined') document.documentElement.dataset.mode = m;
}

/** Simple is the SSR default; on mount localStorage.tf_mode is read, then a
 *  `?mode=` URL param overrides and persists. Cross-tab sync via `storage`
 *  and a window CustomEvent `tf-mode`. Sets <html data-mode>. */
export function ModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<Mode>('simple');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    let m: Mode = readStored() ?? 'simple';
    try {
      const q = new URLSearchParams(window.location.search).get('mode');
      if (isMode(q)) { m = q; writeStored(q); }
    } catch { /* ignore */ }
    setModeState(m); applyDom(m); setMounted(true);

    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY && isMode(e.newValue)) { setModeState(e.newValue); applyDom(e.newValue); }
    };
    const onEvt = (e: Event) => {
      const v = (e as CustomEvent).detail;
      if (isMode(v)) { setModeState(v); applyDom(v); }
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener(EVT, onEvt);
    return () => { window.removeEventListener('storage', onStorage); window.removeEventListener(EVT, onEvt); };
  }, []);

  const setMode = useCallback((m: Mode) => {
    if (!isMode(m)) return;
    writeStored(m); applyDom(m); setModeState(m);
    try { window.dispatchEvent(new CustomEvent(EVT, { detail: m })); } catch { /* ignore */ }
  }, []);

  const value = useMemo<ModeCtx>(() => ({ mode, setMode, advanced: mode === 'advanced', mounted }),
    [mode, setMode, mounted]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useMode(): ModeCtx {
  return useContext(Ctx);
}
