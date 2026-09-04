'use client';
import { useMemo, useState } from 'react';
import { GLOSSARY, PLAIN, TERMS } from '../../lib/terms';
import { humanKey } from '../../lib/vocab';
import { Drawer } from './Drawer';

/** Readable titles for PLAIN keys whose snake_case does not humanise well. */
const PLAIN_TITLE: Record<string, string> = {
  early_pop: 'Early pop', noon_check: 'Noon check', conservative_floor: 'Conservative floor',
  r_multiple: 'R (risk multiple)', paper: 'Paper', tracked: 'Tracked', backtest: 'Backtest',
  drawdown_account: 'Worst dip (of account)', drawdown_sum: 'Worst dip (sum of trade %)',
  gap: 'Gap', score_plain: 'Score', pipeline: 'Pipeline', regime: 'Market type',
  ceiling_floor: 'Ceiling / floor', whats_missing: "What's missing", ambiguous: 'Unclear fills',
  legacy_bucket: 'Legacy picks', pop_rate_incl_flat: 'Early pop rate (incl. flat)', signals_today: 'Signals today',
};

interface Entry { key: string; title: string; text: string }
interface Group { title: string; entries: Entry[] }

function buildGroups(): Group[] {
  return [
    { title: 'Plain English', entries: Object.entries(PLAIN).map(([k, v]) => ({ key: `p:${k}`, title: PLAIN_TITLE[k] ?? humanKey(k), text: v })) },
    { title: 'Table columns', entries: Object.entries(TERMS).map(([k, v]) => ({ key: `t:${k}`, title: humanKey(k), text: v })) },
    { title: 'Every acronym', entries: Object.entries(GLOSSARY).map(([k, v]) => ({ key: `g:${k}`, title: k, text: v })) },
  ];
}

/** The glossary, opened from the TopBar "?" button (the floating FAB is gone). */
export function GlossaryPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState('');
  const groups = useMemo(buildGroups, []);
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return groups;
    return groups
      .map((g) => ({ ...g, entries: g.entries.filter((e) => e.title.toLowerCase().includes(s) || e.text.toLowerCase().includes(s)) }))
      .filter((g) => g.entries.length);
  }, [groups, q]);
  return (
    <Drawer open={open} onClose={onClose} width={480} title="Glossary"
      subtitle="What the words on this screen mean. Nothing here is advice.">
      <input className="gloss-search" type="search" autoFocus placeholder="Search any term…" aria-label="Search the glossary"
        value={q} onChange={(e) => setQ(e.target.value)} />
      {shown.map((g) => (
        <section key={g.title}>
          <div className="subhead">{g.title}</div>
          {g.entries.map((e) => (
            <div className="gloss-entry" key={e.key}>
              <b>{e.title}</b>
              <p>{e.text}</p>
            </div>
          ))}
        </section>
      ))}
      {!shown.length ? <p className="note">No match for “{q}”.</p> : null}
    </Drawer>
  );
}
