'use client';
import { useState } from 'react';

/** Local "Show details" disclosure. Children mount only while open, so an
 *  Advanced section inside it costs nothing (no fetch, no DOM) until revealed. */
export function Details({ summary, children, defaultOpen = false }: {
  summary: string; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details className="details" open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{summary}</summary>
      {open ? <div className="details-body">{children}</div> : null}
    </details>
  );
}
