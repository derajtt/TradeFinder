'use client';
import { useEffect, useRef, useState } from 'react';

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

export async function apiPost<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { method: 'POST' });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

/** Subscribe to backend SSE. handlers: {event: (data) => void} */
export function useEventStream(handlers: Record<string, (data: any) => void>) {
  const ref = useRef(handlers);
  ref.current = handlers;
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/api/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    const names = Object.keys(ref.current);
    const listeners: Record<string, (e: MessageEvent) => void> = {};
    for (const name of names) {
      listeners[name] = (e: MessageEvent) => {
        try { ref.current[name]?.(JSON.parse(e.data)); } catch { /* ignore */ }
      };
      es.addEventListener(name, listeners[name]);
    }
    return () => { es.close(); };
  }, []);
  return connected;
}

/** Poll an endpoint on an interval (fallback + initial load). */
export function usePolling<T>(path: string, ms: number): [T | null, Error | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => apiGet<T>(path)
      .then((d) => { if (alive) { setData(d); setErr(null); } })
      .catch((e) => { if (alive) setErr(e); });
    load();
    const id = setInterval(load, ms);
    return () => { alive = false; clearInterval(id); };
  }, [path, ms, tick]);
  return [data, err, () => setTick((t) => t + 1)];
}
