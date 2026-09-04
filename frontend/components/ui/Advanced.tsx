'use client';
import { useMode } from '../../lib/mode';

/** Renders children only in Advanced mode (null in Simple → no fetch, no DOM). */
export function Advanced({ children, fallback }: { children: React.ReactNode; fallback?: React.ReactNode }) {
  const { advanced } = useMode();
  if (!advanced) return fallback === undefined ? null : <>{fallback}</>;
  return <>{children}</>;
}

/** Renders children only in Simple mode. */
export function SimpleOnly({ children }: { children: React.ReactNode }) {
  const { advanced } = useMode();
  if (advanced) return null;
  return <>{children}</>;
}
