'use client';
import { useEffect, useRef, useState } from 'react';
import { apiGet } from '../../lib/api';
import type { CandidateRow, ExplainItem } from '../../lib/types';
import { GATE, gateLabel } from '../../lib/vocab';

export interface WhatsMissingProps {
  explain?: CandidateRow['explain']; hardBlocks?: string[];
  symbol?: string; lazy?: boolean;     // lazy: fetch /api/candidates/{symbol} when visible and read live.explain
  full?: boolean;                      // render every item as a pass/fail list (drawer); default: first failing + "+N more"
}

interface LiveBits { explain: ExplainItem[] | null; hardBlocks: string[]; found: boolean }
const TTL_MS = 60_000;
const cache = new Map<string, { t: number; v: LiveBits }>();
const inflight = new Map<string, Promise<LiveBits>>();

function fetchLive(symbol: string): Promise<LiveBits> {
  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.t < TTL_MS) return Promise.resolve(hit.v);
  const running = inflight.get(symbol);
  if (running) return running;
  const p = apiGet<{ live: Partial<CandidateRow> | null }>(`/api/candidates/${encodeURIComponent(symbol)}`)
    .then((d) => {
      const live = d?.live ?? null;
      const v: LiveBits = live
        ? { explain: live.explain ?? null, hardBlocks: live.hard_blocks ?? [], found: true }
        : { explain: null, hardBlocks: [], found: false };
      cache.set(symbol, { t: Date.now(), v });
      return v;
    })
    .catch(() => ({ explain: null, hardBlocks: [], found: false } as LiveBits))
    .finally(() => inflight.delete(symbol));
  inflight.set(symbol, p);
  return p;
}

function fmtActual(a: ExplainItem['actual']): string {
  if (a === null || a === undefined || a === '') return '—';
  if (typeof a === 'number') return a.toLocaleString('en-US', { maximumFractionDigits: 2 });
  return String(a);
}
function itemLabel(e: ExplainItem): string {
  return GATE[e.key.replace(/_gate$/, '')] ?? e.label ?? gateLabel(e.key);
}
function itemText(e: ExplainItem): string {
  return `${itemLabel(e)}: ${fmtActual(e.actual)} (need ${e.required})`;
}

/** "{label}: {actual} (need {required})" · hard block → red "Blocked: {gate}" ·
 *  nothing failing → green "Passes every check" · no data → "Not in today's scan". */
export function WhatsMissing({ explain, hardBlocks, symbol, lazy, full }: WhatsMissingProps) {
  const [live, setLive] = useState<LiveBits | null>(null);
  const [wanted, setWanted] = useState(!lazy);
  const ref = useRef<HTMLSpanElement>(null);

  // lazy: wait until the cell scrolls into view (or hover) before fetching
  useEffect(() => {
    if (!lazy || wanted || !ref.current) return;
    if (typeof IntersectionObserver === 'undefined') { setWanted(true); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setWanted(true); io.disconnect(); }
    }, { rootMargin: '120px' });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [lazy, wanted]);

  useEffect(() => {
    if (!lazy || !wanted || !symbol) return;
    let alive = true;
    setLive(null);
    fetchLive(symbol).then((v) => { if (alive) setLive(v); });
    return () => { alive = false; };
  }, [lazy, wanted, symbol]);

  const items: ExplainItem[] | null | undefined = lazy ? live?.explain : explain;
  const blocks: string[] = lazy ? (live?.hardBlocks ?? []) : (hardBlocks ?? []);

  if (lazy && (!wanted || live === null)) {
    return <span ref={ref} className="wm wm-loading" onMouseEnter={() => setWanted(true)} aria-busy="true">…</span>;
  }
  if (lazy && live && !live.found) return <span ref={ref} className="wm faint">Not in today's scan</span>;

  if (full) {
    return (
      <ul ref={ref as unknown as React.RefObject<HTMLUListElement>} className="wm wm-list">
        {blocks.map((b) => <li key={`hb-${b}`} className="wm-block">Blocked: {gateLabel(b)}</li>)}
        {(items ?? []).map((e) => (
          <li key={e.key} className={e.pass ? 'wm-ok' : 'wm-no'}>
            <span className="wm-mark" aria-hidden>{e.pass ? '✓' : '✕'}</span>
            <span className="wm-sr">{e.pass ? 'Passes' : 'Fails'}: </span>{itemText(e)}
          </li>
        ))}
        {!blocks.length && !(items ?? []).length ? <li className="faint">Not in today's scan</li> : null}
      </ul>
    );
  }

  if (blocks.length) {
    return (
      <span ref={ref} className="wm wm-block">
        Blocked: {gateLabel(blocks[0])}
        {blocks.length > 1 ? <span className="faint"> +{blocks.length - 1} more</span> : null}
      </span>
    );
  }
  if (!items || !items.length) return <span ref={ref} className="wm faint">Not in today's scan</span>;
  const failing = items.filter((e) => !e.pass);
  if (!failing.length) return <span ref={ref} className="wm wm-pass">Passes every check</span>;
  return (
    <span ref={ref} className="wm">
      {itemText(failing[0])}
      {failing.length > 1 ? <span className="faint"> +{failing.length - 1} more</span> : null}
    </span>
  );
}
