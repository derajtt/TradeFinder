'use client';
import { fmtEtShort } from '../lib/format';
import { useOps } from '../lib/status';
import { LANE_STATE } from '../lib/vocab';
import s from './today.module.css';
import { eventLabel, plainProse } from './todayShared';
import { Term } from './ui/Popover';
import { SectionHeader } from './ui/SectionHeader';
import { StatusPill, pillFor } from './ui/StatusPill';

/** Advanced-only system detail from `/api/ops` (shared 30s poll): market type,
 *  every lane's state, what runs next, and what is deliberately off. */
export default function OpsPanel() {
  const { ops, loaded } = useOps();
  return (
    <section aria-labelledby="sysdetail-title">
      <SectionHeader id="system" title={<span id="sysdetail-title">System detail</span>}
        question="What is each part of the system doing right now, and what is deliberately off?"
        caption="All models · reported by the scheduler" />
      {!loaded || !ops ? (
        <div className={s.panel} aria-busy="true"><span className={`skel ${s.sk}`} style={{ width: 320 }} /></div>
      ) : (
        <div className={s.panel}>
          <div><Term k="regime">Market type</Term>: {ops.regime_text ? plainProse(ops.regime_text) : <span className="dim">not reported</span>}</div>

          <div className={s.lanes}>
            {ops.lanes.map((l) => (
              <div key={l.lane} className={s.lane}>
                <span>
                  {plainProse(l.lane)}
                  {l.detail ? <div className={s.laneDetail}>{plainProse(l.detail)}</div> : null}
                </span>
                <StatusPill size="sm" {...pillFor(LANE_STATE, l.state)} />
              </div>
            ))}
          </div>

          <div>
            <span className="eyebrow">Next up</span>
            <div className={s.list}>
              {ops.upcoming.map((u, i) => (
                <span key={i}><b>{fmtEtShort(u.at_et)}</b> {eventLabel(u.event)}</span>
              ))}
            </div>
          </div>

          <div>
            <span className="eyebrow">Intentionally off</span>
            <div className={s.list}>
              {ops.not_running.map((x, i) => (
                <span key={i}>{x.what} <small>— {plainProse(x.why)}</small></span>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
