'use client';
import Link from 'next/link';
import { usePolling } from '../../lib/api';
import { fmtNum, fmtPrice } from '../../lib/format';

interface Card { model_id: string; name: string; color: string; experimental: boolean;
  season: number; equity: number; cash: number; return_pct: number; realized_pnl: number;
  max_drawdown_pct: number; trades: number; wins: number; win_rate: number | null; }
interface Comp { cards: Card[]; leaderboards: Record<string, Card[]>; note: string; }
interface ResearchOnly { id: string; name: string; why_not: string; }

export default function CompetitionPage() {
  const [comp] = usePolling<Comp>('/api/competition', 30000);
  const [models] = usePolling<{ research_only: ResearchOnly[]; regime: any }>('/api/models', 60000);
  if (!comp) return <div className="skel" style={{ height: 400, marginTop: 20 }} />;
  const cards = comp.cards ?? [];
  return (
    <>
      <div className="sect">
        <h2>The $10,000 Strategy Competition</h2>
        <span className="meta">cohort: live forward paper · identical costs, conservative fills, isolated ledgers</span>
        {models?.regime && (
          <><span className="spacer" />
          <span className={`regime-chip regime-${models.regime.state}`}
            title={models.regime.why}>regime: {models.regime.state}</span></>
        )}
      </div>
      {cards.length === 0 ? (
        <div className="tbl-wrap"><div className="empty"><b>Accounts initialize on each model's first signal</b>
          Every model starts at exactly $10,000 when it first acts. Until then it appears here.</div></div>
      ) : null}
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))' }}>
        {cards.map((c) => (
          <Link key={c.model_id} href={`/models/${c.model_id}`} style={{ textDecoration: 'none' }}>
            <div className="card acct-card" style={{ borderTopColor: c.color }}>
              <h3 style={{ color: c.color }}>{c.name}{c.experimental && <span className="badge est" style={{ marginLeft: 6 }}>EXP</span>}</h3>
              <div className={`ret-big ${c.return_pct >= 0 ? 'pos' : 'neg'}`}>
                {c.return_pct >= 0 ? '+' : ''}{c.return_pct.toFixed(2)}%
              </div>
              <div className="sub">
                equity <b className="m">{fmtPrice(c.equity)}</b> · dd <span className="neg">{c.max_drawdown_pct}%</span><br />
                {c.trades} trades{c.win_rate != null && <> · WR {(c.win_rate * 100).toFixed(0)}%</>} · season {c.season}
              </div>
            </div>
          </Link>
        ))}
      </div>

      <div className="sect"><h2 style={{ fontSize: 13 }}>Leaderboards</h2>
        <span className="meta">no single winner until you pick an objective — and none of these matter until sample sizes exist</span></div>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))' }}>
        {Object.entries(comp.leaderboards).map(([board, rows]) => (
          <div className="card" key={board}>
            <h3>{board.replace(/_/g, ' ')}</h3>
            {rows.length === 0 ? <div className="sub faint">no qualified entries yet</div> :
              rows.map((r, i) => (
                <div key={r.model_id} style={{ display: 'flex', gap: 8, fontSize: 12.5, padding: '3px 0' }}>
                  <span className="faint m">{i + 1}.</span>
                  <span style={{ color: r.color }}>{r.name}</span>
                  <span className="spacer" style={{ flex: 1 }} />
                  <span className="m">{board === 'win_rate' && r.win_rate != null
                    ? (r.win_rate * 100).toFixed(0) + '%'
                    : board === 'drawdown' ? r.max_drawdown_pct + '%'
                    : (r.return_pct >= 0 ? '+' : '') + r.return_pct.toFixed(2) + '%'}</span>
                </div>
              ))}
          </div>
        ))}
      </div>

      {models?.research_only?.length ? (<>
        <div className="sect"><h2 style={{ fontSize: 13 }}>Research-only methods</h2>
          <span className="meta">visible, honest about why they don't compete with retail data</span></div>
        <div className="tbl-wrap"><table className="tbl" style={{ minWidth: 640 }}>
          <tbody>{models.research_only.map((r) => (
            <tr key={r.id} style={{ cursor: 'default' }}>
              <td className="l"><b>{r.name}</b></td>
              <td className="l dim" style={{ whiteSpace: 'normal' }}>{r.why_not}</td>
            </tr>))}</tbody>
        </table></div>
      </>) : null}
      <p className="disclaimer">{comp.note}</p>
    </>
  );
}
