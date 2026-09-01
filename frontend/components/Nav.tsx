'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { usePolling } from '../lib/api';

const CORE = [
  { href: '/', label: 'Command Center', ico: '⌂' },
  { href: '/competition', label: 'Competition', ico: '🏁' },
  { href: '/chart', label: 'Chart Workstation', ico: '📈' },
  { href: '/signals', label: 'Signal History', ico: '≣' },
  { href: '/performance', label: 'Performance', ico: '∿' },
  { href: '/backtest', label: 'Backtesting', ico: '↺' },
  { href: '/lab', label: 'Exit Lab', ico: '⚗' },
  { href: '/settings', label: 'Settings', ico: '⚙' },
  { href: '/health', label: 'System Health', ico: '✚' },
];

interface ModelMeta { id: string; name: string; color: string; experimental?: boolean; enabled: boolean; }

export default function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(true);
  const [resp] = usePolling<{ models: ModelMeta[] }>('/api/models', 120000);
  const models = resp?.models ?? [];
  return (
    <nav className="side" aria-label="Main navigation">
      <div className="brand">
        <span className="brand-dot" aria-hidden />
        <span><b>TRADEFINDER</b><small>MULTI-STRATEGY</small></span>
      </div>
      {CORE.slice(0, 3).map((l) => (
        <Link key={l.href} href={l.href}
          className={`nav-link ${path === l.href ? 'active' : ''}`}
          aria-current={path === l.href ? 'page' : undefined}>
          <span className="nav-ico" aria-hidden>{l.ico}</span>
          <span className="nav-txt">{l.label}</span>
        </Link>
      ))}
      <button className="nav-link nav-group" onClick={() => setOpen((o) => !o)}
        aria-expanded={open}>
        <span className="nav-ico" aria-hidden>◈</span>
        <span className="nav-txt">Finders</span>
        <span className="nav-txt" style={{ marginLeft: 'auto', fontSize: 10 }}>{open ? '▾' : '▸'}</span>
      </button>
      {open && models.map((m) => (
        <Link key={m.id} href={`/models/${m.id}`}
          className={`nav-link sub ${path === `/models/${m.id}` ? 'active' : ''}`}
          title={m.enabled ? m.name : `${m.name} (disabled)`}>
          <span className="nav-ico" aria-hidden>
            <span className="dot" style={{ background: m.color, opacity: m.enabled ? 1 : 0.3 }} />
          </span>
          <span className="nav-txt" style={{ fontSize: 12, opacity: m.enabled ? 1 : 0.5 }}>
            {m.name.replace('EXP · ', '')}
            {m.experimental && <span className="badge est" style={{ marginLeft: 5 }}>EXP</span>}
          </span>
        </Link>
      ))}
      {CORE.slice(3).map((l) => (
        <Link key={l.href} href={l.href}
          className={`nav-link ${path === l.href ? 'active' : ''}`}
          aria-current={path === l.href ? 'page' : undefined}>
          <span className="nav-ico" aria-hidden>{l.ico}</span>
          <span className="nav-txt">{l.label}</span>
        </Link>
      ))}
      <div className="side-foot">
        Research signals &amp; paper trading only — no orders are placed. Green never means guaranteed profit.
      </div>
    </nav>
  );
}
