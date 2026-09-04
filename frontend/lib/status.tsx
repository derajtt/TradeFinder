'use client';
import { createContext, useContext, useMemo } from 'react';
import { useSharedPoll, useStream } from './api';
import type { AppSettings, Ops, SettingsPayload, StatusPayload } from './types';
import { phaseLabel, type Tone } from './vocab';

export { useStream } from './api';

/* ── /api/status: one poll (15s) + the shared stream, app-wide ────────────── */

interface StatusCtx { status: StatusPayload | null; err: Error | null; loaded: boolean; reload: () => void }
const Ctx = createContext<StatusCtx | null>(null);

/** Mounted once in app/layout.tsx. Every consumer reads the same payload. */
export function StatusProvider({ children }: { children: React.ReactNode }) {
  const { data, err, loaded, reload } = useSharedPoll<StatusPayload>('/api/status', 15000);
  // buy_signal changes active_signals; scanner events change pause/phase state.
  useStream({ buy_signal: () => reload(), scanner: () => reload() });
  const value = useMemo<StatusCtx>(() => ({ status: data, err, loaded, reload }), [data, err, loaded, reload]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStatus(): StatusCtx {
  const ctx = useContext(Ctx);
  // Outside the provider (tests, isolated mounts) fall back to the shared poll —
  // still one request per path.
  const fallback = useSharedPoll<StatusPayload>(ctx ? '' : '/api/status', 15000);
  if (ctx) return ctx;
  return { status: fallback.data, err: fallback.err, loaded: fallback.loaded, reload: fallback.reload };
}

/* ── /api/ops (30s) and /api/settings (120s), shared by every subscriber ──── */

export function useOps(): { ops: Ops | null; loaded: boolean } {
  const { data, loaded } = useSharedPoll<Ops>('/api/ops', 30000);
  return { ops: data, loaded };
}

export function useAppSettings(): { settings: AppSettings | null; loaded: boolean; reload: () => void } {
  const { data, loaded, reload } = useSharedPoll<SettingsPayload>('/api/settings', 120000);
  return { settings: data?.settings ?? null, loaded, reload };
}

/* ── derived state ────────────────────────────────────────────────────────── */

export type PhaseKey = 'premarket' | 'open' | 'afterhours' | 'closed' | 'prep' | 'unknown';

export function phaseKeyOf(raw: string | null | undefined): PhaseKey {
  switch (raw) {
    case 'premarket': return 'premarket';
    case 'regular': case 'open': return 'open';
    case 'afterhours': return 'afterhours';
    case 'closed': return 'closed';
    case 'prep': return 'prep';
    default: return 'unknown';
  }
}

export function useMarketPhase(): { key: PhaseKey; label: string; tone: Tone; isOpen: boolean; isPremarket: boolean; raw: string | null } {
  const { status } = useStatus();
  const raw = status?.phase ?? null;
  const key = phaseKeyOf(raw);
  const l = phaseLabel(raw);
  return { key, label: key === 'unknown' ? 'Unknown' : l.label, tone: key === 'unknown' ? 'neutral' : l.tone,
    isOpen: key === 'open', isPremarket: key === 'premarket', raw };
}

export type ScannerKey = 'running' | 'sleeping' | 'paused' | 'problem' | 'starting' | 'unreachable';

const SCANNER: Record<ScannerKey, { label: string; tone: Tone }> = {
  unreachable: { label: 'Scanner unreachable', tone: 'risk' },
  paused: { label: 'Scanner paused', tone: 'warn' },
  problem: { label: 'Scanner problem', tone: 'risk' },
  starting: { label: 'Scanner starting', tone: 'neutral' },
  sleeping: { label: 'Scanner sleeping', tone: 'neutral' },
  running: { label: 'Scanner running', tone: 'buy' },
};

/** Spec §1.2 rule order: err → paused → last_cycle_ok false → null → closed/afterhours → running.
 *  The backend leaves `last_cycle_ok` null even after completed cycles (verified: null with
 *  `cycles: 67`), so a null flag only means "starting" while no cycle has finished yet
 *  (`last_cycle_at` empty); a non-empty `last_error` with a null flag reads as a problem. */
export function useScannerState(): { key: ScannerKey; label: string; tone: Tone } {
  const { status, err, loaded } = useStatus();
  const phase = phaseKeyOf(status?.phase);
  let key: ScannerKey;
  const sc = status?.scanner;
  if (err && !status) key = 'unreachable';
  else if (!loaded || !status) key = 'starting';
  else if (sc?.paused) key = 'paused';
  else if (sc?.last_cycle_ok === false) key = 'problem';
  else if (sc?.last_cycle_ok == null && !sc?.last_cycle_at) key = 'starting';
  else if (sc?.last_cycle_ok == null && sc?.last_error) key = 'problem';
  else if (phase === 'closed' || phase === 'afterhours') key = 'sleeping';
  else key = 'running';
  return { key, ...SCANNER[key] };
}
