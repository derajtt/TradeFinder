'use client';
import { useMode } from '../../lib/mode';

/** Simple | Advanced segmented control. Writes localStorage.tf_mode via useMode. */
export function ModeToggle() {
  const { mode, setMode } = useMode();
  return (
    <div className="mode-toggle" role="group" aria-label="Detail level">
      <button type="button" className={`mode-toggle__opt${mode === 'simple' ? ' is-on' : ''}`}
        aria-pressed={mode === 'simple'} onClick={() => setMode('simple')}>Simple</button>
      <button type="button" className={`mode-toggle__opt${mode === 'advanced' ? ' is-on' : ''}`}
        aria-pressed={mode === 'advanced'} onClick={() => setMode('advanced')}>Advanced</button>
    </div>
  );
}
