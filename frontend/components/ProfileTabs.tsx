'use client';
import { usePolling } from '../lib/api';
import { useProfile } from '../lib/profile';

interface ProfileCfg { name: string; enabled: boolean; color: string; description: string; }

export default function ProfileTabs() {
  const [resp] = usePolling<{ profiles: Record<string, ProfileCfg> }>('/api/profiles', 60000);
  const [active, setActive] = useProfile();
  const profiles = resp?.profiles ?? {};
  const ids = Object.keys(profiles);
  if (!ids.length) return null;
  return (
    <div className="ptabs" role="tablist" aria-label="Strategy models">
      {ids.map((id) => {
        const p = profiles[id];
        const on = active === id;
        return (
          <button key={id} role="tab" aria-selected={on}
            className={`ptab ${on ? 'active' : ''} ${p.enabled ? '' : 'off'}`}
            style={on ? { borderColor: p.color, color: p.color } : undefined}
            title={`${p.description}${p.enabled ? '' : ' (disabled — not evaluating)'}`}
            onClick={() => setActive(id)}>
            <span className="dot" style={{ background: p.color, boxShadow: p.enabled ? `0 0 8px ${p.color}` : 'none', opacity: p.enabled ? 1 : 0.3 }} />
            {p.name}
          </button>
        );
      })}
      <span className="faint" style={{ fontSize: 10.5, marginLeft: 6 }}>
        all enabled models evaluate every candidate in parallel — tabs switch the view
      </span>
    </div>
  );
}
