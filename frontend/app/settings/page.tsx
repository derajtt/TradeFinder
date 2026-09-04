'use client';
import { useEffect, useState } from 'react';
import ProfileEditor from '../../components/ProfileEditor';
import { Details, EmptyState, SectionHeader, StatusPill } from '../../components/ui';
import { apiGet, apiPost, apiPut } from '../../lib/api';
import { useMode } from '../../lib/mode';
import { useScannerState, useStatus } from '../../lib/status';
import type { AppSettings, SettingsPayload } from '../../lib/types';
import { humanKey } from '../../lib/vocab';

/** Fields where blank = no limit (nullable). */
const NULLABLE = new Set(['market_cap_min', 'market_cap_max', 'float_min', 'float_max',
  'shares_outstanding_min', 'shares_outstanding_max', 'price_max']);
const STRING_FIELDS = new Set(['buy_confirm_after_et']);

type FieldDef = [keyof AppSettings, string, string?];

/** The few settings a beginner touches; everything else sits under "Show all scanner rules". */
const BASIC_FIELDS: FieldDef[] = [
  ['buy_confirm_after_et', 'Buys allowed from (ET)', 'e.g. 07:00 — many brokers open premarket orders at 7:00. Blank = allow immediately. Stocks that qualify earlier show as Early watch.'],
  ['min_score_for_buy', 'Min score to buy', '0–100. A stock below this score is Watching, not a Buy pick.'],
];
const BASIC_KEYS = new Set<string>(['buy_confirm_after_et', 'min_score_for_buy', 'include_otc']);

const GROUPS: { title: string; hint?: string; fields: FieldDef[] }[] = [
  {
    title: 'Which stocks can it look at?',
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
    title: 'How liquid must they be?',
    fields: [
      ['min_pm_volume', 'Min premarket volume (shares)'],
      ['min_pm_dollar_volume', 'Min premarket $ volume'],
      ['max_spread_pct', 'Max spread (%)'],
      ['preferred_spread_pct', 'Preferred spread (%)'],
      ['quote_freshness_sec', 'Price must be fresher than (seconds)'],
    ],
  },
  {
    title: 'When may it call a Buy?',
    fields: [
      ['buy_confirm_after_et', 'Buys allowed from (ET)'],
      ['min_score_for_buy', 'Min score to buy'],
      ['min_rvol_for_buy', 'Min volume vs normal (×)'],
      ['min_catalyst_confidence', 'Min news confidence (0–1)'],
      ['max_extension_from_pm_high_pct', 'Max stretch from premarket high (%)'],
      ['est_rvol_buy_multiplier', 'Estimated-volume multiplier'],
      ['reentry_cooldown_min', 'Re-entry cooldown (minutes)'],
      ['early_window_min', 'Outcome window (minutes)', 'A pick is judged only on its first N minutes of tradability (10–30 typical).'],
      ['early_win_gain_pct', 'Win threshold (%)', 'Up this much inside the window = popped, locked forever. 0 = any uptick counts.'],
    ],
  },
  {
    title: 'How often does it scan, and what may it spend?',
    fields: [
      ['scan_interval_sec', 'Scan interval (seconds)'],
      ['enrich_top_n', 'Enrich top N candidates'],
      ['universe_sweep_per_cycle', 'Universe sweep per cycle', 'Full NASDAQ/NYSE/AMEX rotation — symbols quoted per cycle beyond the movers lists. 0 = off. 50 ≈ full rotation every ~35 min.'],
      ['openai_monthly_budget_usd', 'AI monthly budget ($)'],
    ],
  },
];

const TOGGLES: [keyof AppSettings, string, string][] = [
  ['allow_estimated_rvol', 'Allow estimated volume-vs-normal', 'Let the labelled low-confidence estimate satisfy the gate (at the stricter multiplier) while per-symbol baselines accumulate.'],
  ['momentum_only_mode', 'Momentum-only mode', 'Permit a Buy without an identified news catalyst (separately tested; off by default).'],
];
const OTC_TOGGLE: [keyof AppSettings, string, string] = ['include_otc', 'Include OTC stocks', 'Off by default — OTC quotes are thinner and less reliable.'];

const ENV_LABEL: Record<string, string> = {
  FMP_API_KEY: 'Market data (FMP)', OPENAI_API_KEY: 'AI (OpenAI)', SEC_USER_AGENT: 'SEC EDGAR contact',
  DATABASE_URL: 'Database', APP_SECRET: 'App secret',
};

export default function SettingsPage() {
  const [resp, setResp] = useState<SettingsPayload | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [form, setForm] = useState<Record<string, string | boolean>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const scanner = useScannerState();
  const { reload: reloadStatus } = useStatus();
  const { advanced } = useMode();

  const load = () => apiGet<SettingsPayload>('/api/settings').then((r) => {
    setResp(r);
    const f: Record<string, string | boolean> = {};
    for (const [k, v] of Object.entries(r.settings)) {
      f[k] = typeof v === 'boolean' ? v : v === null || v === undefined ? '' : String(v);
    }
    setForm(f);
  }).catch((e) => setErr(String(e))).finally(() => setLoaded(true));

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
      setSaved('Saved — applies from the next scan; past picks are never rewritten.');
      load(); reloadStatus();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  const togglePause = async () => {
    if (!resp) return;
    setBusy(true); setErr(null);
    try {
      await apiPost(resp.settings.paused ? '/api/scanner/resume' : '/api/scanner/pause');
      await load(); reloadStatus();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  const preset = (p: 'micro' | 'nano' | 'clear') => {
    setForm((f) => ({
      ...f,
      market_cap_min: p === 'clear' ? '' : p === 'nano' ? '' : '20000000',
      market_cap_max: p === 'clear' ? '' : p === 'nano' ? '50000000' : '300000000',
    }));
  };

  const header = (
    <SectionHeader level={1} title="Settings" question="What rules is the scanner using, and can I pause it?"
      caption={resp ? `engine v${String(resp.strategy_version).replace(/^v/i, '')} · changes apply from the next scan` : undefined} />
  );

  if (!loaded) return <>{header}<EmptyState loaded={false} headline="Loading settings" reason={null} /></>;
  if (!resp) return <>{header}<EmptyState tone="risk" headline="Settings unavailable" reason={err} /></>;

  const paused = resp.settings.paused;
  const field = ([key, label, hint]: FieldDef) => (
    <div className="field" key={key}>
      <label htmlFor={`set-${key}`}>{label}</label>
      <input id={`set-${key}`} inputMode={STRING_FIELDS.has(key) ? undefined : 'decimal'}
        placeholder={NULLABLE.has(key) ? 'no limit' : String(resp.defaults[key] ?? '')}
        value={String(form[key] ?? '')}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
  const toggle = ([key, label, hint]: [keyof AppSettings, string, string]) => (
    <div className="check" key={key}>
      <input type="checkbox" id={`set-${key}`} checked={Boolean(form[key])}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.checked }))} />
      <label htmlFor={`set-${key}`}><b>{label}</b> — <span className="dim">{hint}</span></label>
    </div>
  );
  const saveRow = (
    <div className="btn-row">
      <button type="button" className="btn primary" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save settings'}</button>
      {saved ? <span className="save-note">{saved}</span> : null}
    </div>
  );

  return (
    <>
      {header}
      {err ? <div className="err-box">{err}</div> : null}

      <section className="card" style={{ marginTop: 8 }}>
        <div className="row" style={{ gap: 12 }}>
          <StatusPill label={scanner.label} tone={scanner.tone} raw={advanced ? scanner.key : undefined} />
          {paused ? <StatusPill size="sm" label="Paused in settings" tone="warn" /> : null}
          <span style={{ flex: 1 }} />
          <button type="button" className={`btn ${paused ? 'primary' : 'danger'}`} disabled={busy} onClick={togglePause}>
            {paused ? '▶ Resume scanner' : '⏸ Pause scanner'}
          </button>
        </div>
        <p className="note" style={{ marginTop: 6 }}>Pausing stops new scans; nothing is sold or bought.</p>
      </section>

      <SectionHeader title="The basics" question="When may it buy, how strict is the score, and which sizes of company?" />
      <div className="form-grid">
        {BASIC_FIELDS.map(field)}
      </div>
      <div className="field" style={{ marginTop: 14 }}>
        <span className="field-label">Company size presets</span>
        <div className="btn-row" style={{ marginTop: 6 }}>
          <button type="button" className="btn sm" onClick={() => preset('nano')}>Nano caps &lt;$50M</button>
          <button type="button" className="btn sm" onClick={() => preset('micro')}>Micro caps $20–300M</button>
          <button type="button" className="btn sm" onClick={() => preset('clear')}>No cap limits</button>
        </div>
        <span className="hint">Sets Market cap min / max below. Current: {form.market_cap_min ? `$${Number(form.market_cap_min).toLocaleString('en-US')}` : 'no floor'} – {form.market_cap_max ? `$${Number(form.market_cap_max).toLocaleString('en-US')}` : 'no cap'}.</span>
      </div>
      {toggle(OTC_TOGGLE)}
      {saveRow}

      <Details summary="Show all scanner rules">
        {GROUPS.map((g) => {
          const fields = g.fields.filter(([k]) => !BASIC_KEYS.has(k));
          if (!fields.length) return null;
          return (
            <div key={g.title}>
              <SectionHeader title={g.title} question="" caption={g.hint} />
              <div className="form-grid">{fields.map(field)}</div>
            </div>
          );
        })}
        <SectionHeader title="Modes" question="" />
        {TOGGLES.map(toggle)}
        {saveRow}
        <ProfileEditor />
      </Details>

      <SectionHeader title="Environment (read-only)" question="Which external services are configured?"
        caption="Configured / not configured only — values are never shown." />
      <div className="kv-grid">
        {Object.entries(resp.env_status).map(([k, ok]) => (
          <div className="kv" key={k}>
            <div className="k">{ENV_LABEL[k] ?? humanKey(k.toLowerCase())}</div>
            <div className="v sans">
              <StatusPill size="sm" label={ok ? 'Configured' : 'Not configured'} tone={ok ? 'buy' : 'warn'} raw={advanced ? k : undefined} />
            </div>
          </div>
        ))}
      </div>

      <style href="tf-settings" precedence="default">{`
        .field-label { font-size: var(--fs-note); font-weight: 650; color: var(--text-dim); }
      `}</style>
    </>
  );
}
