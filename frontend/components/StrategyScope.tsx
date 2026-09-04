'use client';
import { useSharedPoll } from '../lib/api';
import { useMode } from '../lib/mode';
import { useProfile } from '../lib/profile';
import type { ProfileCfg, ProfilesPayload } from '../lib/types';
import { titleCase } from '../lib/vocab';

/** `/api/profiles`, shared by every subscriber (one request per minute app-wide). */
export function useProfiles(): { profiles: Record<string, ProfileCfg>; loaded: boolean } {
  const { data, loaded } = useSharedPoll<ProfilesPayload>('/api/profiles', 60000);
  return { profiles: data?.profiles ?? {}, loaded };
}

/** "{name} model" — the scope every model-scoped source caption carries. */
export function scopeLabelFor(profiles: Record<string, ProfileCfg>, id: string): string {
  const name = profiles[id]?.name ?? titleCase(id);
  return `${name} model`;
}

export function useScopeLabel(): string {
  const [profile] = useProfile();
  const { profiles } = useProfiles();
  return scopeLabelFor(profiles, profile);
}

/** Which strategy am I looking at? Simple: one labelled select pill.
 *  Advanced: the pill row plus the "this only changes the view" caption. */
export function StrategyScope() {
  const { profiles, loaded } = useProfiles();
  const [active, setActive] = useProfile();
  const { advanced } = useMode();
  const ids = Object.keys(profiles);

  if (!loaded) {
    return (
      <div className="scope" aria-busy="true">
        <span className="scope-pill skel" style={{ width: 180, color: 'transparent' }}>Strategy</span>
      </div>
    );
  }
  if (!ids.length) return null;
  const current = ids.includes(active) ? active : ids[0];
  const cur = profiles[current];

  if (!advanced) {
    return (
      <div className="scope">
        <label className="scope-pill">
          <span className="dot" style={{ background: cur?.color || 'var(--text-faint)' }} aria-hidden />
          <span>Strategy:</span>
          <select value={current} onChange={(e) => setActive(e.target.value)} aria-label="Strategy">
            {ids.map((id) => (
              <option key={id} value={id}>{profiles[id].name}{profiles[id].enabled ? '' : ' (off)'}</option>
            ))}
          </select>
        </label>
      </div>
    );
  }
  return (
    <div className="scope">
      <div className="ptabs" role="tablist" aria-label="Strategy models">
        {ids.map((id) => {
          const p = profiles[id];
          const on = current === id;
          return (
            <button key={id} type="button" role="tab" aria-selected={on}
              className={`ptab${on ? ' active' : ''}${p.enabled ? '' : ' off'}`}
              style={on ? { borderColor: p.color, color: p.color } : undefined}
              onClick={() => setActive(id)}>
              <span className="dot" style={{ background: p.color, opacity: p.enabled ? 1 : 0.3 }} aria-hidden />
              {p.name}{p.enabled ? '' : <span className="faint"> · off</span>}
            </button>
          );
        })}
      </div>
      <span className="scope-cap">all enabled strategies evaluate every stock; this only changes the view</span>
    </div>
  );
}
export default StrategyScope;
