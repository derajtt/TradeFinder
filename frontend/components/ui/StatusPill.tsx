'use client';
import Link from 'next/link';
import { useMode } from '../../lib/mode';
import type { Tone } from '../../lib/vocab';

export interface StatusPillProps {
  label: string; tone: Tone;
  /** Advanced-only <code> suffix; also shown in Simple when the mapping was unknown. */
  raw?: string;
  /** only AttentionChips may set true */
  glow?: boolean;
  size?: 'sm' | 'md';
  href?: string; onClick?: () => void;
  icon?: React.ReactNode;
}

/** Replaces .badge.*, .st.*, .phase-chip, .regime-chip. Plain words, never ALL-CAPS enums. */
export function StatusPill({ label, tone, raw, glow, size = 'md', href, onClick, icon }: StatusPillProps) {
  const { advanced } = useMode();
  const showRaw = !!raw && (advanced || label === 'Unknown');
  const cls = `pill pill--${tone}${size === 'sm' ? ' pill--sm' : ''}${glow ? ' pill--glow' : ''}`;
  const body = (
    <>
      {icon ? <span className="pill-ico" aria-hidden>{icon}</span> : null}
      <span>{label}</span>
      {showRaw ? <code className="pill-raw">{raw}</code> : null}
    </>
  );
  if (href) return <Link href={href} className={cls} onClick={onClick}>{body}</Link>;
  if (onClick) return <button type="button" className={cls} onClick={onClick}>{body}</button>;
  return <span className={cls}>{body}</span>;
}

/** Look a raw enum up in a vocab table. Unknown → { label: 'Unknown', tone: 'neutral', raw }. */
export function pillFor(map: Record<string, { label: string; tone: Tone }>,
                        raw: string | null | undefined): StatusPillProps {
  const key = raw == null ? '' : String(raw);
  const hit = map[key] ?? map[key.toUpperCase()] ?? map[key.toLowerCase()];
  if (!hit) return { label: 'Unknown', tone: 'neutral', raw: key || undefined };
  return { label: hit.label, tone: hit.tone, raw: key };
}
