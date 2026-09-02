'use client';
import { useEffect, useState } from 'react';

/** Five screens, shown once. Explains what a signal card is actually telling
 *  you before you look at one. Dismissable and remembered locally. */
const STEPS = [
  { h: 'BUY means the strategy favours a long entry',
    p: 'A BUY card is a research signal saying the strategy\'s conditions are met right now. It is not advice, and it is not a prediction — it is the strategy telling you what it sees.' },
  { h: 'The entry zone is where the trade is still worth taking',
    p: 'Every card gives a price range, not a single number. Inside the range the setup is intact. Above the "do not chase" price the move is already extended and the reward no longer covers the risk.' },
  { h: 'The stop loss is where the plan admits it was wrong',
    p: 'It is chosen from the chart — a swing low, a band edge, a volatility distance — before any position size is calculated. If price reaches it, the idea has failed and the plan exits.' },
  { h: 'Position size comes from the stop, not the other way round',
    p: 'You choose what percentage of the account a losing trade may cost. The system divides that dollar amount by the distance to your stop. A wider stop therefore means a smaller position for exactly the same planned loss.' },
  { h: 'Targets tell you where profit gets taken',
    p: 'Each target shows its reward as a multiple of what you risked — 2R means aiming to make twice the risk. The plan takes partial profit along the way and moves the stop to breakeven after the first target.' },
];

export default function Onboarding() {
  const [i, setI] = useState(0);
  const [show, setShow] = useState(false);
  useEffect(() => {
    try { if (!localStorage.getItem('tf_onboarded')) setShow(true); } catch { /* private mode */ }
  }, []);
  function done() {
    try { localStorage.setItem('tf_onboarded', '1'); } catch { /* ignore */ }
    setShow(false);
  }
  if (!show) return null;
  const s = STEPS[i];
  return (
    <div className="onb-back" role="dialog" aria-modal="true" aria-labelledby="onb-h">
      <div className="onb">
        <div className="onb-dots">{STEPS.map((_, k) => <i key={k} className={k <= i ? 'on' : ''} />)}</div>
        <h2 id="onb-h">{s.h}</h2>
        <p>{s.p}</p>
        <p style={{ fontSize: 12, color: 'var(--text-faint)', marginTop: 14 }}>
          Signals are research output, not guaranteed outcomes. Risk management limits
          planned exposure but cannot remove the risk of loss, slippage or gaps.
        </p>
        <div className="onb-row">
          <button className="tab" onClick={done}>Skip</button>
          {i > 0 && <button className="tab" onClick={() => setI(i - 1)}>Back</button>}
          <button className="btn" onClick={() => (i === STEPS.length - 1 ? done() : setI(i + 1))}>
            {i === STEPS.length - 1 ? 'Got it' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
