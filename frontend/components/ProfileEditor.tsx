'use client';
import { useEffect, useState } from 'react';
import { apiGet, apiPut } from '../lib/api';

interface P { name: string; enabled: boolean; color: string; description: string;
  overrides: Record<string, number | string>; }

const OVERRIDE_FIELDS: [string, string][] = [
  ['price_min', 'Price min ($)'], ['price_max', 'Price max ($)'],
  ['watch_max_gap_pct', 'Max gap (%)'], ['max_ext_above_vwap_pct', 'Max ext above VWAP (%)'],
  ['rotation_hard_cap', 'Rotation hard cap (×float)'],
  ['min_pm_dollar_volume', 'Min PM $ volume'], ['watch_score_min', 'Watch score min'],
  ['min_score_for_buy', 'BUY score min'], ['min_pm_volume', 'Min PM volume (sh)'],
];

export default function ProfileEditor() {
  const [profiles, setProfiles] = useState<Record<string, P>>({});
  const [sel, setSel] = useState('accuracy');
  const [saved, setSaved] = useState('');
  const load = () => apiGet<{ profiles: Record<string, P> }>('/api/profiles')
    .then((r) => setProfiles(r.profiles));
  useEffect(() => { load(); }, []);
  const p = profiles[sel];
  const save = async () => {
    await apiPut('/api/profiles', { profiles });
    setSaved('Profiles saved — every enabled model evaluates in parallel next cycle.');
    load();
  };
  const upd = (patch: Partial<P>) =>
    setProfiles((ps) => ({ ...ps, [sel]: { ...ps[sel], ...patch } }));
  return (
    <>
      <div className="sect"><h2 style={{ fontSize: 13 }}>Strategy models</h2>
        <span className="meta">each model runs concurrently with its own settings; the tabs on the dashboard switch which model you're viewing</span></div>
      <div className="ptabs">
        {Object.entries(profiles).map(([id, cfg]) => (
          <button key={id} className={`ptab ${sel === id ? 'active' : ''} ${cfg.enabled ? '' : 'off'}`}
            style={sel === id ? { borderColor: cfg.color, color: cfg.color } : undefined}
            onClick={() => setSel(id)}>
            <span className="dot" style={{ background: cfg.color }} />{cfg.name}
          </button>
        ))}
      </div>
      {p && (
        <>
          <div className="check">
            <input type="checkbox" id="prof-en" checked={p.enabled}
              onChange={(e) => upd({ enabled: e.target.checked })} />
            <label htmlFor="prof-en"><b>Enabled</b> — <span className="dim">{p.description}</span></label>
          </div>
          {sel === 'primary' ? (
            <p className="dim" style={{ fontSize: 12.5 }}>
              The primary model uses the global settings above — it has no overrides by design.
            </p>
          ) : (
            <div className="form-grid">
              {OVERRIDE_FIELDS.map(([k, label]) => (
                <div className="field" key={k}>
                  <label htmlFor={`ov-${k}`}>{label}</label>
                  <input id={`ov-${k}`} inputMode="decimal" placeholder="inherit global"
                    value={p.overrides?.[k] ?? ''}
                    onChange={(e) => {
                      const v = e.target.value.trim().replace(/,/g, '');
                      const overrides = { ...(p.overrides || {}) };
                      if (v === '') delete overrides[k];
                      else if (!Number.isNaN(Number(v))) overrides[k] = Number(v);
                      upd({ overrides });
                    }} />
                  <span className="hint">blank = inherit the global setting</span>
                </div>
              ))}
            </div>
          )}
          <div className="btn-row">
            <button className="btn primary" onClick={save}>Save models</button>
            {saved && <span className="save-note">{saved}</span>}
          </div>
        </>
      )}
    </>
  );
}
