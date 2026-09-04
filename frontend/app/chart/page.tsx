'use client';
import { useState } from 'react';
import ChartPane from '../../components/ChartPane';
import { EmptyState, SectionHeader } from '../../components/ui';
import { usePollingState } from '../../lib/api';
import { useMode } from '../../lib/mode';
import type { WatchlistRow } from '../../lib/types';

const FALLBACK = ['SPY', 'QQQ', 'NVDA', 'BTCUSD'];

/** Simple = one chart. Advanced = 1/2/4-up. Default symbols come from the
 *  first watchlist when it has any, else SPY. */
export default function ChartWorkstation() {
  const { advanced } = useMode();
  const [layout, setLayout] = useState<1 | 2 | 4>(1);
  const wl = usePollingState<{ rows: WatchlistRow[] }>('/api/watchlists', 300000);
  const panes = advanced ? layout : 1;
  const wlSyms = wl.data?.rows?.[0]?.symbols ?? [];
  const defaults = wlSyms.length ? wlSyms : FALLBACK;

  return (
    <>
      <SectionHeader level={1} title="Charts"
        question="What does this stock's price look like, and where are the important levels?"
        caption="Provider bars with indicators computed locally — nothing repaints. Drawings are saved per symbol in this browser."
        right={advanced ? (
          <div className="ptabs" role="group" aria-label="Layout">
            {([1, 2, 4] as const).map((n) => (
              <button key={n} type="button" className={`ptab ${layout === n ? 'active' : ''}`}
                style={{ padding: '4px 12px' }} aria-pressed={layout === n} onClick={() => setLayout(n)}>{n}-up</button>
            ))}
          </div>
        ) : undefined} />
      {!wl.loaded ? (
        <EmptyState loaded={false} headline="Loading" reason={null} />
      ) : (
        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: panes === 1 ? '1fr' : 'repeat(2, minmax(0, 1fr))' }}>
          {Array.from({ length: panes }, (_, i) => (
            <ChartPane key={`p${i}`} paneId={`p${i}`} defaultSymbol={defaults[i] ?? FALLBACK[i] ?? 'SPY'} />
          ))}
        </div>
      )}
      <p className="disclaimer">Charts render provider bars with locally computed indicators (causal — nothing repaints).
        Crypto symbols use aggregate quotes, which are not an executable venue order book.</p>
    </>
  );
}
