'use client';
import Link from 'next/link';

export interface EmptyStateProps {
  headline: string;
  reason: string | null | undefined;   // null → "No reason reported by the scanner."
  next?: React.ReactNode;              // "Buys can appear from 07:00 ET …"
  action?: { label: string; href?: string; onClick?: () => void };
  tone?: 'neutral' | 'warn' | 'risk';
  loaded?: boolean;                    // false → skeleton instead
  compact?: boolean;
}

/** An honest empty: what is empty, why (from the backend), and what happens next. */
export function EmptyState({ headline, reason, next, action, tone = 'neutral', loaded = true, compact }: EmptyStateProps) {
  const cls = `empty-card empty-card--${tone}${compact ? ' empty-card--compact' : ''}`;
  if (!loaded) {
    return (
      <div className={`${cls} skel-tile`} aria-busy="true">
        <div className="empty-h skel">&nbsp;</div>
        <div className="empty-reason skel">&nbsp;</div>
      </div>
    );
  }
  return (
    <div className={cls} role="status">
      <div className="empty-h">{headline}</div>
      <div className="empty-reason">{reason ?? 'No reason reported by the scanner.'}</div>
      {next ? <div className="empty-next">{next}</div> : null}
      {action ? (
        action.href
          ? <Link className="btn empty-action" href={action.href} onClick={action.onClick}>{action.label}</Link>
          : <button type="button" className="btn empty-action" onClick={action.onClick}>{action.label}</button>
      ) : null}
    </div>
  );
}
