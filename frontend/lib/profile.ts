'use client';
import { useEffect, useState } from 'react';

const KEY = 'ph_profile';

export function useProfile(): [string, (p: string) => void] {
  const [profile, setProfileState] = useState('primary');
  useEffect(() => {
    try {
      const v = localStorage.getItem(KEY);
      if (v) setProfileState(v);
    } catch { /* private mode */ }
    const onEvt = (e: Event) => setProfileState((e as CustomEvent).detail);
    window.addEventListener('ph-profile', onEvt);
    return () => window.removeEventListener('ph-profile', onEvt);
  }, []);
  const set = (p: string) => {
    try { localStorage.setItem(KEY, p); } catch { /* ignore */ }
    window.dispatchEvent(new CustomEvent('ph-profile', { detail: p }));
  };
  return [profile, set];
}
