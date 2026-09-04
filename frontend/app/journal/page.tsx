'use client';
import { useState } from 'react';
import DetailDrawer from '../../components/DetailDrawer';
import { EmptyState, SectionHeader, StatusPill, Tip } from '../../components/ui';
import { apiPostBody, usePollingState } from '../../lib/api';
import { fmtEtShort } from '../../lib/format';
import type { JournalEntry } from '../../lib/types';

export default function JournalPage() {
  const { data, loaded, reload } = usePollingState<{ rows: JournalEntry[] }>('/api/journal', 60000);
  const [note, setNote] = useState('');
  const [symbol, setSymbol] = useState('');
  const [tags, setTags] = useState('');
  const [rules, setRules] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  const rows = data?.rows ?? [];
  const save = async () => {
    if (!note.trim() || busy) return;
    setBusy(true); setErr(null);
    try {
      await apiPostBody('/api/journal', {
        note, symbol: symbol.trim().toUpperCase(), rules_followed: rules,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
      });
      setNote(''); setSymbol(''); setTags(''); setRules(true);
      reload();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  return (
    <>
      <SectionHeader level={1} title="My journal"
        question="What did I do, and did I follow my plan?"
        caption="Your notes, tied to stocks and picks — compare what you did with what the rules said." />

      <form className="tbl-wrap" style={{ padding: '14px 16px', marginBottom: 14 }}
        onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="jr-symbol">Stock (optional)</label>
            <input id="jr-symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)}
              placeholder="e.g. ABUS" autoComplete="off" />
          </div>
          <div className="field">
            <label htmlFor="jr-tags">Tags</label>
            <input id="jr-tags" value={tags} onChange={(e) => setTags(e.target.value)}
              placeholder="comma-separated" autoComplete="off" />
            <span className="hint">Short words you can filter by later, separated by commas.</span>
          </div>
          <div className="field">
            <span className="row" style={{ gap: 4 }}>
              <label htmlFor="jr-rules">Rules followed</label>
              <Tip label="rules followed" text="Tick this if you took the trade (or skipped it) the way your plan said. Untick it to record a deviation." />
            </span>
            <label className="switch" style={{ padding: '9px 0' }}>
              <input id="jr-rules" type="checkbox" checked={rules} onChange={(e) => setRules(e.target.checked)} />
              <span>{rules ? 'Yes — I followed my plan' : 'No — I deviated from my plan'}</span>
            </label>
            <span className="hint">Did you follow your plan?</span>
          </div>
        </div>
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="jr-note">Note</label>
          <textarea id="jr-note" className="input sans" rows={3}
            placeholder="What happened, what you saw, what you'd do differently…"
            value={note} onChange={(e) => setNote(e.target.value)} style={{ width: '100%', resize: 'vertical' }} />
        </div>
        <div className="btn-row" style={{ marginTop: 12 }}>
          <button type="submit" className="btn primary" disabled={busy || !note.trim()}>{busy ? 'Saving…' : 'Save entry'}</button>
          {err ? <span className="err-box" style={{ margin: 0, padding: '6px 10px' }}>{err}</span> : null}
        </div>
      </form>

      <SectionHeader title="Entries" count={loaded ? rows.length : null} question="What have I written so far?" />
      {!loaded ? (
        <EmptyState loaded={false} headline="Loading entries" reason={null} />
      ) : rows.length === 0 ? (
        <EmptyState headline="No entries yet" reason="Nothing has been written in this journal yet."
          next="Your first note starts the journal." />
      ) : (
        <div className="timeline">
          {rows.map((e) => (
            <div className="tl-item" key={e.id}>
              <span className="tl-time">{fmtEtShort(e.created_at)}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="chips" style={{ alignItems: 'center' }}>
                  {e.symbol ? <span className="sym">{e.symbol}</span> : null}
                  {e.signal_uid && e.symbol ? (
                    <button type="button" className="chip chip--early" onClick={() => setSel(e.symbol)}>about {e.symbol} pick</button>
                  ) : null}
                  {!e.rules_followed ? <StatusPill size="sm" label="Plan not followed" tone="risk" /> : null}
                  {(e.tags ?? []).map((t) => <span key={t} className="chip">{t}</span>)}
                </div>
                <div style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{e.note}</div>
                {e.review ? <div className="note" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>Review: {e.review}</div> : null}
              </div>
            </div>
          ))}
        </div>
      )}
      {sel ? <DetailDrawer symbol={sel} onClose={() => setSel(null)} /> : null}
    </>
  );
}
