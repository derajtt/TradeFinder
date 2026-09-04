'use client';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || 'http://localhost:8000';
export const API_KEY = process.env.NEXT_PUBLIC_API_KEY || '';

function authHeaders(): Record<string, string> {
  return API_KEY ? { 'X-API-Key': API_KEY } : {};
}

/** Append api_key for URL-only consumers (EventSource, CSV links). */
export function withKey(url: string): string {
  if (!API_KEY) return url;
  return url + (url.includes('?') ? '&' : '?') + 'api_key=' + encodeURIComponent(API_KEY);
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { cache: 'no-store', headers: authHeaders() });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

export async function apiPostBody<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((j as any)?.detail || `${path} -> HTTP ${r.status}`);
  return j as T;
}

export async function apiPost<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { method: 'POST', headers: authHeaders() });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json();
}

export async function apiDelete<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { method: 'DELETE', headers: authHeaders() });
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  return r.json().catch(() => ({} as T));
}

/* ── one EventSource for the whole app ────────────────────────────────────── */

type StreamHandler = (data: any) => void;
const streamHandlers = new Map<string, Set<StreamHandler>>();
const nativeBound = new Set<string>();
const connListeners = new Set<() => void>();
let es: EventSource | null = null;
let streamConnected = false;

function bindNative(name: string) {
  if (!es || nativeBound.has(name)) return;
  nativeBound.add(name);
  es.addEventListener(name, (e: MessageEvent) => {
    let data: any;
    try { data = JSON.parse(e.data); } catch { return; }
    streamHandlers.get(name)?.forEach((fn) => { try { fn(data); } catch { /* handler error */ } });
  });
}

function setConnected(v: boolean) {
  if (streamConnected === v) return;
  streamConnected = v;
  connListeners.forEach((fn) => fn());
}

function ensureStream() {
  if (es || typeof window === 'undefined') return;
  es = new EventSource(withKey(`${API_BASE}/api/stream`));
  es.onopen = () => setConnected(true);
  es.onerror = () => setConnected(false);
  nativeBound.clear();
  streamHandlers.forEach((_, name) => bindNative(name));
}

function teardownStream() {
  if (!es) return;
  es.close(); es = null; nativeBound.clear();
  setConnected(false);
}

/** Subscribe to one named SSE event on the shared connection. Returns an unsubscribe. */
export function subscribeStream(name: string, fn: StreamHandler): () => void {
  let set = streamHandlers.get(name);
  if (!set) { set = new Set(); streamHandlers.set(name, set); }
  set.add(fn);
  ensureStream();
  bindNative(name);
  return () => {
    set!.delete(fn);
    if (set!.size === 0) streamHandlers.delete(name);
    if (streamHandlers.size === 0) teardownStream();
  };
}

const subscribeConn = (fn: () => void) => { connListeners.add(fn); return () => { connListeners.delete(fn); }; };
const getConn = () => streamConnected;
const getConnServer = () => false;

/** One shared EventSource, multiplexed by event name. handlers: {event: (data) => void}.
 *  Returns whether the stream is currently connected. */
export function useStream(handlers: Record<string, StreamHandler>): boolean {
  const ref = useRef(handlers);
  ref.current = handlers;
  const names = Object.keys(handlers).sort().join(',');
  useEffect(() => {
    if (!names) return;
    const offs = names.split(',').map((name) =>
      subscribeStream(name, (d) => ref.current[name]?.(d)));
    return () => offs.forEach((off) => off());
  }, [names]);
  return useSyncExternalStore(subscribeConn, getConn, getConnServer);
}

/** @deprecated alias of `useStream` — kept so existing pages compile. */
export const useEventStream = useStream;

/* ── polling ──────────────────────────────────────────────────────────────── */

/** Poll an endpoint on an interval (fallback + initial load). Tuple form, kept for existing pages. */
export function usePolling<T>(path: string, ms: number): [T | null, Error | null, () => void] {
  const { data, err, reload } = usePollingState<T>(path, ms);
  return [data, err, reload];
}

export interface PollState<T> { data: T | null; err: Error | null; loaded: boolean; reload: () => void }

/** Poll an endpoint on an interval; `loaded` flips true after the first response
 *  (success or error) so callers never render "0" before data exists.
 *  Pass an empty path to hold the hook idle (no fetch, loaded stays false). */
export function usePollingState<T>(path: string, ms: number): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () => apiGet<T>(path)
      .then((d) => { if (alive) { setData(d); setErr(null); setLoaded(true); } })
      .catch((e) => { if (alive) { setErr(e instanceof Error ? e : new Error(String(e))); setLoaded(true); } });
    load();
    const id = setInterval(load, ms);
    return () => { alive = false; clearInterval(id); };
  }, [path, ms, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, err, loaded, reload };
}

/* ── shared polling (one request per path, however many subscribers) ──────── */

interface SharedEntry {
  snap: { data: unknown; err: Error | null; loaded: boolean };
  subs: Set<() => void>;
  timer: ReturnType<typeof setInterval> | null;
  ms: number;
  path: string;
}
const SHARED = new Map<string, SharedEntry>();
const SERVER_SNAP = { data: null, err: null, loaded: false };

function sharedLoad(e: SharedEntry) {
  apiGet<unknown>(e.path)
    .then((d) => { e.snap = { data: d, err: null, loaded: true }; })
    .catch((x) => { e.snap = { data: e.snap.data, err: x instanceof Error ? x : new Error(String(x)), loaded: true }; })
    .finally(() => e.subs.forEach((fn) => fn()));
}

function sharedEntry(path: string, ms: number): SharedEntry {
  let e = SHARED.get(path);
  if (!e) {
    e = { snap: { data: null, err: null, loaded: false }, subs: new Set(), timer: null, ms, path };
    SHARED.set(path, e);
  }
  if (ms < e.ms) e.ms = ms;
  return e;
}

/** Force a refetch of a shared path (no-op when nobody is subscribed). */
export function reloadShared(path: string) {
  const e = SHARED.get(path);
  if (e && e.subs.size) sharedLoad(e);
}

/** Like `usePollingState` but every subscriber of the same path shares one
 *  request and one interval; polling stops when the last subscriber unmounts. */
export function useSharedPoll<T>(path: string, ms: number): PollState<T> {
  const subscribe = useCallback((fn: () => void) => {
    if (!path) return () => {};
    const e = sharedEntry(path, ms);
    e.subs.add(fn);
    if (!e.timer) {
      sharedLoad(e);
      e.timer = setInterval(() => sharedLoad(e), e.ms);
    }
    return () => {
      e.subs.delete(fn);
      if (e.subs.size === 0 && e.timer) { clearInterval(e.timer); e.timer = null; }
    };
  }, [path, ms]);
  const getSnap = useCallback(() => (path ? sharedEntry(path, ms).snap : SERVER_SNAP), [path, ms]);
  const snap = useSyncExternalStore(subscribe, getSnap, () => SERVER_SNAP);
  const reload = useCallback(() => reloadShared(path), [path]);
  return { data: snap.data as T | null, err: snap.err, loaded: snap.loaded, reload };
}
