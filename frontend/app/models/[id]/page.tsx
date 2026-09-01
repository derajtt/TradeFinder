'use client';
import { useParams } from 'next/navigation';
import { useMemo, useState } from 'react';
import DetailDrawer from '../../../components/DetailDrawer';
import SignalTable from '../../../components/SignalTable';
import { usePolling } from '../../../lib/api';
import { fmtPrice } from '../../../lib/format';
import type { SignalRow } from '../../../lib/types';

interface ModelInfo { id: string; name: string; color: string; edge: string;
  universe: string; horizon: string; cadence: string; asset_classes: string[];
  experimental?: boolean; data_notes?: string; hypothesis?: string; enabled: boolean;
  account: { cash: number; equity: number; realized_pnl: number; return_pct: number;
    max_drawdown_pct: number; trades_closed: number; wins: number };
  signals: Record<string, number>; }

export default function ModelPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [resp] = usePolling<{ models: ModelInfo[]; regime: any }>('/api/models', 30000);
  const [sigResp] = usePolling<{ rows: SignalRow[] }>(`/api/signals?profile=${id}&limit=200`, 30000);
  const [posResp] = usePolling<{ rows: any[] }>(`/api/positions?profile=${id}`, 30000);
  const [tab, setTab] = useState<'overview' | 'signals' | 'positions'>('overview');
  const [sel, setSel] = useState<string | null>(null);
  const m = useMemo(() => resp?.models.find((x) => x.id === id), [resp, id]);
  if (!m) return <div className="skel" style={{ height: 300, marginTop: 20 }} />;
  const a = m.account;
  return (
    <>
      <div className="model-hero" style={{ borderLeftColor: m.color, margin: '6px 0 18px' }}>
        <div className="sect" style={{ margin: 0 }}>
          <h2 style={{ color: m.color }}>{m.name}
            {m.experimental && <span className="badge est" style={{ marginLeft: 8 }}>EXPERIMENTAL</span>}
            {!m.enabled && <span className="badge neutral" style={{ marginLeft: 8 }}>DISABLED</span>}
          </h2>
        </div>
        <p className="dim" style={{ margin: '6px 0 2px', maxWidth: '75ch' }}>{m.edge}.</p>
        <p className="faint" style={{ fontSize: 12 }}>
          {m.universe} · {m.horizon} · cadence: {m.cadence} · {m.asset_classes.join(' + ')}
        </p>
        {m.data_notes && <p className="faint" style={{ fontSize: 12 }}>⚠ data: {m.data_notes}</p>}
        {m.hypothesis && <p className="faint" style={{ fontSize: 12, maxWidth: '80ch' }}>hypothesis: {m.hypothesis}</p>}
      </div>

      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
        <div className="card"><h3>Equity</h3><div className="big">{fmtPrice(a.equity)}</div>
          <div className="sub">of $10,000 start</div></div>
        <div className="card"><h3>Return</h3>
          <div className={`big ${a.return_pct >= 0 ? 'pos' : 'neg'}`}>{a.return_pct >= 0 ? '+' : ''}{a.return_pct}%</div>
          <div className="sub">live paper cohort</div></div>
        <div className="card"><h3>Trades</h3><div className="big">{a.trades_closed}</div>
          <div className="sub">{a.wins} wins · dd {a.max_drawdown_pct}%</div></div>
        <div className="card"><h3>Signals</h3>
          <div className="big">{Object.values(m.signals).reduce((x, y) => x + y, 0)}</div>
          <div className="sub">{Object.entries(m.signals).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(' · ') || 'none yet'}</div></div>
      </div>

      <div className="ptabs">
        {(['overview', 'signals', 'positions'] as const).map((t) => (
          <button key={t} className={`ptab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      {tab === 'signals' && <SignalTable rows={sigResp?.rows ?? []} onSelect={(s) => setSel(s.symbol)} />}
      {tab === 'positions' && (
        <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 700 }}>
          <thead><tr><th className="l">Symbol</th><th className="l">Status</th><th>Entry</th>
            <th>Stop</th><th>T1/T2</th><th>Size</th><th>Realized R</th><th className="l">Exit</th></tr></thead>
          <tbody>{(posResp?.rows ?? []).map((p, i) => (
            <tr key={i} onClick={() => setSel(p.symbol)}>
              <td className="l"><span className="sym">{p.symbol}</span></td>
              <td className="l"><span className={`badge ${p.status === 'open' ? 'buy' : 'neutral'}`}>{p.status}</span></td>
              <td>{fmtPrice(p.entry_fill)}</td><td className="dim">{fmtPrice(p.stop)}</td>
              <td className="dim">{fmtPrice(p.target1)}/{fmtPrice(p.target2)}</td>
              <td>{fmtPrice(p.size_usd)}</td>
              <td className={p.realized_r >= 0 ? 'pos' : 'neg'}>{p.realized_r?.toFixed(2)}R</td>
              <td className="l dim" style={{ fontSize: 11 }}>{p.exit_reason || '—'}</td>
            </tr>))}
            {!(posResp?.rows ?? []).length && <tr><td colSpan={8} className="l dim">No positions yet.</td></tr>}
          </tbody>
        </table></div>
      )}
      {tab === 'overview' && (
        <p className="disclaimer" style={{ marginTop: 8, borderTop: 'none' }}>
          This model runs continuously with its own settings and ledger. It shares only market data and the
          conservative execution simulator with other models — never scores or balances. Adjust its parameters in
          Settings → Strategy models. Every statistic is labeled by cohort; forward paper evidence decides the competition.
        </p>
      )}
      {sel && <DetailDrawer symbol={sel} onClose={() => setSel(null)} />}
    </>
  );
}
