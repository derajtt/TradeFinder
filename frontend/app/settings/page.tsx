'use client';
import { useEffect, useState } from 'react';
import { apiGet, apiPost, apiPut } from '../../lib/api';
import type { AppSettings } from '../../lib/types';

interface SettingsResp { settings: AppSettings; defaults: AppSettings;
  env_status: Record<string, boolean>; strategy_version: string; }

/** Fields where blank = no limit (nullable). */
const NULLABLE = new Set(['market_cap_min', 'market_cap_max', 'float_min', 'float_max',
  'shares_outstanding_min', 'shares_outstanding_max', 'price_max']);
const STRING_FIELDS = new Set(['buy_confirm_after_et']);

const GROUPS: { title: string; hint?: string; fields: [keyof AppSettings, string, string?][] }[] = [
  {
    title: 'Universe — price & size',
    hint: 'Leave a field blank for no limit. Target micro caps by capping Market cap max (e.g. 300,000,000).',
    fields: [
      ['price_min', 'Price min ($)'],
      ['price_max', 'Price max ($)', 'blank = no cap'],
      ['market_cap_min', 'Market cap min ($)', 'blank = no floor'],
      ['market_cap_max', 'Market cap max ($)', 'blank = no cap'],
      ['float_min', 'Float min (shares)', 'blank = ignore'],
      ['float_max', 'Float max (shares)', 'blank = ignore'],
      ['shares_outstanding_min', 'Shares outstanding min', 'blank = ignore'],
      ['shares_outstanding_max', 'Shares outstanding max', 'blank = ignore'],
    ],
  },
  {
    title: 'Liquidity gates',
    fields: [
      ['min_pm_volume', 'Min premarket volume (shares)'],
      ['min_pm_dollar_volume', 'Min premarket $ volume'],
      ['max_spread_pct', 'Max spread %'],
      ['preferred_spread_pct', 'Preferred spread %'],
      ['quote_freshness_sec', 'Quote freshness (sec)'],
    ],
  },
  {
    title: 'BUY gates',
    fields: [
      ['buy_confirm_after_et' as any, 'Confirm BUY after (ET)', 'e.g. 07:00 — Schwab & many brokers open premarket at 7:00. Blank = confirm immediately. Earlier qualifiers show as EARLY WATCH.'],
      ['min_score_for_buy', 'Min score (0–100)'],
      ['min_rvol_for_buy', 'Min premarket RVOL (x)'],
      ['min_catalyst_confidence', 'Min catalyst confidence (0–1)'],
      ['max_extension_from_pm_high_pct', 'Max extension from PM high %'],
      ['est_rvol_buy_multiplier', 'Estimated-RVOL multiplier'],
      ['reentry_cooldown_min', 'Re-entry cooldown (min)'],
      ['early_window_min', 'Outcome window (min)', 'A pick is judged only on its first N minutes of tradability (10–30 typical).'],
      ['early_win_gain_pct', 'Win threshold (%)', 'Up this much inside the window = WIN, locked forever. 0 = any uptick counts.'],
    ],
  },
  {
    title: 'Scanner & budget',
    fields: [
      ['scan_interval_sec', 'Scan interval (sec)'],
      ['enrich_top_n', 'Enrich top N candidates'],
      ['universe_sweep_per_cycle', 'Universe sweep per cycle', 'Full NASDAQ/NYSE/AMEX rotation — symbols quoted per cycle beyond the movers lists. 0 = off. 50 ≈ full rotation every ~35 min.'],
      ['openai_monthly_budget_usd', 'OpenAI monthly budget ($)'],
    ],
  },
];

const TOGGLES: [keyof AppSettings, string, string][] = [
  ['allow_estimated_rvol', 'Allow estimated RVOL', 'Let the labeled low-confidence RVOL estimate satisfy the gate (at the stricter multiplier) while per-symbol baselines accumulate.'],
  ['momentum_only_mode', 'Momentum-only mode', 'Permit BUY without an identified catalyst (separately-tested strategy; off by default).'],
  ['include_otc', 'Include OTC securities', 'Off by default per spec.'],
];

export default function SettingsPage() {
  const [resp, setResp] = useState<SettingsResp | null>(null);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => apiGet<SettingsResp>('/api/settings').then((r) => {
    setResp(r);
    const f: Record<string, string | boolean> = {};
    for (const [k, v] of Object.entries(r.settings)) {
      f[k] = typeof v === 'boolean' ? v : v === null || v === undefined ? '' : String(v);
    }
    setForm(f);
  }).catch((e) => setErr(String(e)));

  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true); setErr(null); setSaved(null);
    try {
      const patch: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) {
        if (typeof v === 'boolean') { patch[k] = v; continue; }
        const t = v.trim().replace(/,/g, '');
        if (STRING_FIELDS.has(k)) { patch[k] = v.trim(); continue; }
        patch[k] = t === '' ? (NULLABLE.has(k) ? null : undefined) : Number(t);
        if (patch[k] === undefined) delete patch[k];
        if (Number.isNaN(patch[k])) delete patch[k];
      }
      await apiPut('/api/settings', patch);
      setSaved('Saved — applies prospectively; historical signals are never rewritten.');
      load();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  const preset = (p: 'micro' | 'nano' | 'clear') => {
    setForm((f) => ({
      ...f,
      market_cap_min: p === 'clear' ? '' : p === 'nano' ? '' : '20000000',
      market_cap_max: p === 'clear' ? '' : p === 'nano' ? '50000000' : '300000000',
    }));
  };

  if (!resp) return <div className="skel" style={{ height: 400, marginTop: 20 }} />;
  const paused = resp.settings.paused;

  return (
    <>
      <div className="sect">
        <h2>Settings</h2>
        <span className="meta">strategy {resp.strategy_version} — changes apply prospectively only</span>
        <span className="spacer" />
        <button className={`btn ${paused ? 'primary' : 'danger'}`} disabled={busy}
          onClick={async () => { await apiPost(paused ? '/api/scanner/resume' : '/api/scanner/pause'); load(); }}>
          {paused ? '▶ Resume scanner' : '⏸ Pause scanner'}
        </button>
      </div>

      {err && <div className="err-box">{err}</div>}

      <div className="sect" style={{ marginTop: 6 }}><h2 style={{ fontSize: 13 }}>Market-cap presets</h2></div>
      <div className="btn-row" style={{ marginTop: 0 }}>
        <button className="btn" onClick={() => preset('nano')}>Nano caps &lt;$50M</button>
        <button className="btn" onClick={() => preset('micro')}>Micro caps $20–300M</button>
        <button className="btn" onClick={() => preset('clear')}>No cap limits</button>
      </div>

      {GROUPS.map((g) => (
        <div key={g.title}>
          <div className="sect"><h2 style={{ fontSize: 13 }}>{g.title}</h2>
            {g.hint && <span className="meta">{g.hint}</span>}</div>
          <div className="form-grid">
            {g.fields.map(([key, label, hint]) => (
              <div className="field" key={key}>
                <label htmlFor={key}>{label}</label>
                <input id={key} inputMode="decimal"
                  placeholder={NULLABLE.has(key) ? 'no limit' : String((resp.defaults as any)[key] ?? '')}
                  value={String(form[key] ?? '')}
                  onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
                {hint && <span className="hint">{hint}</span>}
              </div>
            ))}
          </div>
        </div>
      ))}

      <div className="sect"><h2 style={{ fontSize: 13 }}>Modes</h2></div>
      {TOGGLES.map(([key, label, hint]) => (
        <div className="check" key={key}>
          <input type="checkbox" id={key} checked={Boolean(form[key])}
            onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.checked }))} />
          <label htmlFor={key}><b>{label}</b> — <span className="dim">{hint}</span></label>
        </div>
      ))}

      <div className="btn-row">
        <button className="btn primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save settings'}
        </button>
        {saved && <span className="save-note">{saved}</span>}
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Environment (read-only)</h2>
        <span className="meta">configured / not configured only — values are never shown</span></div>
      <div className="kv-grid">
        {Object.entries(resp.env_status).map(([k, ok]) => (
          <div className="kv" key={k}>
            <div className="k">{k}</div>
            <div className="v" style={{ color: ok ? 'var(--buy)' : 'var(--risk)' }}>
              {ok ? '● configured' : '○ not configured'}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
