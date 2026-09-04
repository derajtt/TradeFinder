'use client';
import type { Canonical } from '../lib/types';
import { LIFECYCLE } from '../lib/vocab';
import s from './today.module.css';
import { Term } from './ui/Popover';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill } from './ui/StatusPill';

const CHAIN = ['DISCOVERED', 'EARLY_WATCH', 'QUALIFIED_WATCH', 'ACTIONABLE_BUY'] as const;
/** "v2.0.0" and "2.0.0" both render as "v2.0.0". */
const fmtVersion = (v: string | undefined) => (v ? `v${v.replace(/^v/i, '')}` : '—');
const TAIL = ['REJECTED', 'INVALIDATED', 'EXPIRED', 'CLOSED'] as const;

/** Advanced-only pipeline: where today's stocks are, whether the counts
 *  reconcile, and which engine/filter versions produced them. */
export default function FunnelStrip({ canonical, loaded, scopeLabel }: {
  canonical: Canonical | null; loaded: boolean; scopeLabel: string;
}) {
  const lc = canonical?.lifecycle_counts ?? {};
  const n = (k: string) => lc[k] ?? 0;
  const label = (k: string) => (k === 'ACTIONABLE_BUY' ? 'Buy picks' : LIFECYCLE[k]?.label ?? k);
  return (
    <section aria-labelledby="pipeline-title">
      <SectionHeader id="pipeline" title={<span id="pipeline-title"><Term k="pipeline">Pipeline</Term></span>}
        question="Where are today's stocks in the pipeline?" caption={scopeLabel} />
      {!loaded || !canonical ? (
        <div className={s.panel} aria-busy="true"><span className={`skel ${s.sk}`} style={{ width: 320 }} /></div>
      ) : (
        <div className={s.panel}>
          <div className={s.chain}>
            {CHAIN.map((k, i) => (
              <span key={k} className={s.chain}>
                {i ? <span className={s.arrow} aria-hidden>→</span> : null}
                <StatusPill size="sm" tone={LIFECYCLE[k].tone} label={`${label(k)} ${n(k)}`} />
              </span>
            ))}
          </div>
          <div className={s.gray}>
            {TAIL.map((k) => <span key={k}>{label(k)} {n(k)}</span>)}
            <span>· Blocked but still tracked {canonical.totals?.rejected_candidates ?? '—'} (today)</span>
          </div>
          <div className={s.gray}>
            {canonical.reconciliation?.equals_total
              ? <StatusPill size="sm" tone="buy" label="counts reconcile" />
              : <StatusPill size="sm" tone="risk" label="counts don't reconcile — see System health" href="/health" />}
            <span className="faint">
              engine {fmtVersion(canonical.versions?.strategy_version)} · filters {canonical.versions?.filter_version ?? '—'}
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
