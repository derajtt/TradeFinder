'use client';
import { useEffect, useState } from 'react';
import { apiGet, apiPut } from '../lib/api';
import type { ProfileCfg, ProfilesPayload } from '../lib/types';
import { EmptyState } from './ui/EmptyState';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill } from './ui/StatusPill';

/** Per-strategy overrides accepted by PUT /api/profiles (backend/app/routes/api.py). */
const OVERRIDE_FIELDS: [string, string][] = [
  ['price_min', 'Price min ($)'], ['price_max', 'Price max ($)'],
  ['watch_max_gap_pct', 'Max gap (%)'], ['max_ext_above_vwap_pct', 'Max stretch above average price (%)'],
  ['rotation_hard_cap', 'Max float traded (× float)'],
  ['min_pm_dollar_volume', 'Min premarket $ volume'], ['watch_score_min', 'Min score to watch'],
  ['min_score_for_buy', 'Min score to buy'], ['min_pm_volume', 'Min premarket volume (shares)'],
];

/** Which strategy variants run, and with what overrides. Lives under
 *  Settings → "Show all scanner rules". */
export default function ProfileEditor() {
  const [profiles, setProfiles] = useState<Record<string, ProfileCfg>>({});
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState('accuracy');
  const [saved, setSaved] = useState('');
  const load = () => apiGet<ProfilesPayload>('/api/profiles')
    .then((r) => { setProfiles(r.profiles); setErr(null); })
    .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
    .finally(() => setLoaded(true));
  useEffect(() => { load(); }, []);

  const ids = Object.keys(profiles);
  const current = ids.includes(sel) ? sel : ids[0];
  const p = current ? profiles[current] : undefined;
  const save = async () => {
    try {
      await apiPut('/api/profiles', { profiles });
      setSaved('Saved — every enabled strategy evaluates in parallel from the next scan.');
      load();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  };
  const upd = (patch: Partial<ProfileCfg>) =>
    setProfiles((ps) => ({ ...ps, [current]: { ...ps[current], ...patch } }));

  return (
    <>
      <SectionHeader title="Strategy variants" question="Which variants run, and which rules do they override?"
        caption="Each variant runs concurrently with its own settings; the strategy selector on Today switches which one you are viewing." />
      {err ? <div className="err-box">{err}</div> : null}
      {!loaded ? (
        <EmptyState loaded={false} headline="Loading variants" reason={null} />
      ) : !ids.length ? (
        <EmptyState headline="No strategy variants" reason="The server returned no profiles." />
      ) : (
        <>
          <div className="ptabs" role="tablist" aria-label="Strategy variant">
            {ids.map((id) => {
              const cfg = profiles[id];
              return (
                <button key={id} type="button" role="tab" aria-selected={current === id}
                  className={`ptab ${current === id ? 'active' : ''} ${cfg.enabled ? '' : 'off'}`}
                  style={current === id ? { borderColor: cfg.color, color: cfg.color } : undefined}
                  onClick={() => setSel(id)}>
                  <span className="dot" style={{ background: cfg.color }} aria-hidden />{cfg.name}
                  {!cfg.enabled ? <StatusPill size="sm" label="Off" tone="neutral" /> : null}
                </button>
              );
            })}
          </div>
          {p ? (
            <>
              <div className="check">
                <input type="checkbox" id="prof-en" checked={p.enabled} onChange={(e) => upd({ enabled: e.target.checked })} />
                <label htmlFor="prof-en"><b>Enabled</b> — <span className="dim">{p.description}</span></label>
              </div>
              {current === 'primary' ? (
                <p className="note">The primary strategy uses the global settings above — it has no overrides by design.</p>
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
                <button type="button" className="btn primary" onClick={save}>Save variants</button>
                {saved ? <span className="save-note">{saved}</span> : null}
              </div>
            </>
          ) : null}
        </>
      )}
    </>
  );
}
