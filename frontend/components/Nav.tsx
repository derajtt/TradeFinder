'use client';
import Link from 'next/link';
import { useCallback, useState } from 'react';
import { usePathname } from 'next/navigation';
import { apiGet } from '../lib/api';
import { useMode } from '../lib/mode';
import type { Tone } from '../lib/vocab';
import { StatusPill } from './ui/StatusPill';

export interface NavItem {
  href: string;
  label: string;
  /** Hidden in Simple mode. */
  advanced?: boolean;
  /** Always-on pill next to the label (e.g. a strategy that failed its test). */
  pill?: { label: string; tone: Tone };
  /** Icon for the ≤900px icon-only rail. */
  ico?: string;
}
export interface NavGroup { group: string; items: NavItem[] }

/** Spec §1.1 — 4 groups / 12 links in Simple, 5 groups / 18 links in Advanced.
 *  Route paths are unchanged; only labels and grouping differ from the old flat list. */
export const NAV_GROUPS: NavGroup[] = [
  { group: 'Today', items: [
    { href: '/', label: 'Today', ico: '⌂' },
    { href: '/signals', label: 'Picks', ico: '≣' },
    { href: '/feed', label: 'News & filings', ico: '📰' },
    { href: '/calendar', label: 'Calendar', ico: '📅' },
  ] },
  { group: 'Stocks', items: [
    { href: '/chart', label: 'Charts', ico: '📈' },
    { href: '/watchlists', label: 'My watchlist & alerts', ico: '★' },
    { href: '/journal', label: 'My journal', ico: '✎' },
  ] },
  { group: 'Results', items: [
    { href: '/performance', label: 'Scorecard', ico: '∿' },
    { href: '/competition', label: 'Strategies (paper accounts)', ico: '🏁' },
    { href: '/accuracy', label: 'Accuracy board', advanced: true, ico: '◎' },
  ] },
  { group: 'Research', items: [
    { href: '/backtest', label: 'Backtests', advanced: true, ico: '↺' },
    { href: '/lab', label: 'Exit lab', advanced: true, ico: '⚗' },
    { href: '/quant', label: 'Quant lab', advanced: true, ico: '⚛' },
    { href: '/reversion', label: 'Extreme Reversion', advanced: true, ico: '⇄',
      pill: { label: 'Failed test', tone: 'risk' } },
  ] },
  { group: 'Setup', items: [
    { href: '/risk', label: 'Risk & position size', ico: '⚖' },
    { href: '/settings', label: 'Settings', ico: '⚙' },
    { href: '/health', label: 'System health', advanced: true, ico: '✚' },
  ] },
];

interface ModelMeta { id: string; name: string; color: string; experimental?: boolean; enabled: boolean }

const EYEBROW: React.CSSProperties = {
  fontSize: 11, textTransform: 'uppercase', letterSpacing: 1.2,
  color: 'var(--text-faint)', padding: '14px 12px 4px', fontWeight: 600,
};

export default function Nav() {
  const path = usePathname();
  const { advanced } = useMode();
  const [listOpen, setListOpen] = useState(false);
  const [models, setModels] = useState<ModelMeta[] | null>(null);
  const [modelsErr, setModelsErr] = useState(false);

  // /api/models is fetched only when the sub-list is opened (spec §1.1).
  const toggleList = useCallback(() => {
    setListOpen((o) => {
      const next = !o;
      if (next && models === null) {
        apiGet<{ models: ModelMeta[] }>('/api/models')
          .then((r) => setModels(r.models ?? []))
          .catch(() => { setModelsErr(true); setModels([]); });
      }
      return next;
    });
  }, [models]);

  const isActive = (href: string) =>
    path === href || (href === '/competition' && path.startsWith('/models/'));

  return (
    <nav className="side" aria-label="Main navigation">
      <div className="brand">
        <span className="brand-dot" aria-hidden />
        <span><b>TRADEFINDER</b></span>
      </div>

      {NAV_GROUPS.map((g) => {
        const items = g.items.filter((it) => advanced || !it.advanced);
        if (!items.length) return null;
        return (
          <div key={g.group} className="nav-grp">
            <div className="eyebrow nav-txt" style={EYEBROW}>{g.group}</div>
            {items.map((l) => (
              <div key={l.href}>
                <Link href={l.href}
                  className={`nav-link ${isActive(l.href) ? 'active' : ''}`}
                  aria-current={isActive(l.href) ? 'page' : undefined}>
                  <span className="nav-ico" aria-hidden>{l.ico}</span>
                  <span className="nav-txt" style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.label}</span>
                    {l.pill ? <StatusPill label={l.pill.label} tone={l.pill.tone} size="sm" /> : null}
                  </span>
                </Link>

                {l.href === '/competition' ? (
                  <>
                    <button type="button" className="nav-link nav-group sub" onClick={toggleList}
                      aria-expanded={listOpen} aria-controls="nav-all-strategies">
                      <span className="nav-ico" aria-hidden>{listOpen ? '▾' : '▸'}</span>
                      <span className="nav-txt" style={{ fontSize: 12 }}>
                        All strategies{models ? ` (${models.length})` : ''}
                      </span>
                    </button>
                    {listOpen ? (
                      <div id="nav-all-strategies">
                        {models === null ? (
                          <div className="nav-link sub nav-txt" style={{ fontSize: 12, color: 'var(--text-faint)' }}>Loading…</div>
                        ) : modelsErr ? (
                          <div className="nav-link sub nav-txt" style={{ fontSize: 12, color: 'var(--text-faint)' }}>Could not load the strategy list.</div>
                        ) : models.map((m) => {
                          const href = `/models/${m.id}`;
                          const name = m.name.replace('EXP · ', '');
                          return (
                            <Link key={m.id} href={href}
                              className={`nav-link sub ${path === href ? 'active' : ''}`}
                              aria-current={path === href ? 'page' : undefined}
                              aria-label={m.enabled ? name : `${name} (disabled)`}>
                              <span className="nav-ico" aria-hidden>
                                <span className="dot" style={{ background: m.color, opacity: m.enabled ? 1 : 0.3 }} />
                              </span>
                              <span className="nav-txt" style={{ fontSize: 12, opacity: m.enabled ? 1 : 0.5, display: 'flex', gap: 6, alignItems: 'baseline', minWidth: 0 }}>
                                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
                                {m.experimental ? <span style={{ color: 'var(--text-faint)' }}>experimental</span> : null}
                              </span>
                            </Link>
                          );
                        })}
                      </div>
                    ) : null}
                  </>
                ) : null}
              </div>
            ))}
          </div>
        );
      })}

      <div className="side-foot" style={{ fontSize: 12 }}>
        Research signals &amp; paper trading only — no orders are placed. Green never means guaranteed profit.
      </div>
    </nav>
  );
}
