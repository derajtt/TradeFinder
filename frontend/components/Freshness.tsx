'use client';
import { useEffect, useState } from 'react';
import { ageSeconds } from '../lib/format';

export default function Freshness({ ts, fresh }: { ts: string | null; fresh?: boolean | null }) {
  const [, force] = useState(0);
  useEffect(() => { const id = setInterval(() => force((x) => x + 1), 5000); return () => clearInterval(id); }, []);
  const age = ageSeconds(ts);
  if (age === null) return <span className="fresh stale" title="No provider timestamp">∅</span>;
  const ok = fresh !== false && age < 180;
  const label = age < 60 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`;
  return (
    <span className={`fresh ${ok ? 'ok' : 'stale'}`} title={`Quote age ${label}${ok ? '' : ' — stale'}`}>
      ● {label}
    </span>
  );
}
