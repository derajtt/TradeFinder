'use client';
import { useEffect, useState } from 'react';
import { useMode } from '../lib/mode';

/** Five screens, shown once, describing what the user lands on (spec §1.3).
 *  Step 5 picks the mode: Simple (default) or Advanced. Remembered locally. */
const STEPS: { h: string; p: string }[] = [
  { h: 'Today', p: 'Today shows whether there is anything to buy right now — and if not, why not and when the next scan runs.' },
  { h: 'Buy picks and Watches', p: "A Buy pick passed every check. A Watch is a stock the scanner is tracking that might become one. 'What's missing' tells you the gap." },
  { h: 'Every pick has a plan', p: "Every pick has a plan: buy price, stop (where we admit we're wrong), and two targets." },
  { h: 'Paper money only', p: "Everything here is paper — simulated money. Results with few trades are marked 'too few to judge'. Nothing here is advice." },
  { h: 'Simple or Advanced', p: 'Simple mode hides the machinery. Flip to Advanced any time from the top bar.' },
];

export default function Onboarding() {
  const [i, setI] = useState(0);
  const [show, setShow] = useState(false);
  const { setMode } = useMode();
  useEffect(() => {
    try { if (!localStorage.getItem('tf_onboarded')) setShow(true); } catch { /* private mode */ }
  }, []);
  function done(mode?: 'simple' | 'advanced') {
    if (mode) setMode(mode);
    try { localStorage.setItem('tf_onboarded', '1'); } catch { /* ignore */ }
    setShow(false);
  }
  if (!show) return null;
  const s = STEPS[i];
  const last = i === STEPS.length - 1;
  return (
    <div className="onb-back" role="dialog" aria-modal="true" aria-labelledby="onb-h">
      <div className="onb">
        <div className="onb-dots" aria-hidden>{STEPS.map((_, k) => <i key={k} className={k <= i ? 'on' : ''} />)}</div>
        <div className="eyebrow">Step {i + 1} of {STEPS.length}</div>
        <h2 id="onb-h">{s.h}</h2>
        <p>{s.p}</p>
        <p className="onb-fine">
          Picks are research output, not guaranteed outcomes. Paper results are simulated and
          cannot remove the risk of loss, slippage or gaps in real trading.
        </p>
        <div className="onb-row">
          <button type="button" className="tab" onClick={() => done()}>Skip</button>
          {i > 0 && <button type="button" className="tab" onClick={() => setI(i - 1)}>Back</button>}
          {last ? (
            <>
              <button type="button" className="btn" onClick={() => done('advanced')}>I know this stuff — Advanced</button>
              <button type="button" className="btn primary" onClick={() => done('simple')}>Start in Simple</button>
            </>
          ) : (
            <button type="button" className="btn primary" onClick={() => setI(i + 1)}>Next</button>
          )}
        </div>
      </div>
    </div>
  );
}
