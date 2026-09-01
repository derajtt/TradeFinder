'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const LINKS = [
  { href: '/', label: 'Dashboard', ico: '�launch' },
  { href: '/signals', label: 'Signal History', ico: '◷' },
  { href: '/performance', label: 'Performance', ico: '◔' },
  { href: '/settings', label: 'Settings', ico: '⚙' },
  { href: '/health', label: 'System Health', ico: '♥' },
] as const;

const ICONS: Record<string, string> = {
  '/': '⌂', '/signals': '≣', '/performance': '∿', '/settings': '⚙', '/health': '✚',
};

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="side" aria-label="Main navigation">
      <div className="brand">
        <span className="brand-dot" aria-hidden />
        <span><b>PREMARKET</b><small>HUNTER</small></span>
      </div>
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href}
          className={`nav-link ${path === l.href ? 'active' : ''}`}
          aria-current={path === l.href ? 'page' : undefined}>
          <span className="nav-ico" aria-hidden>{ICONS[l.href]}</span>
          <span className="nav-txt">{l.label}</span>
        </Link>
      ))}
      <div className="side-foot">
        Research signals only — not investment advice. No orders are placed.
      </div>
    </nav>
  );
}
