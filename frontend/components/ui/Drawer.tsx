'use client';
import { useEffect, useId, useRef } from 'react';

export interface DrawerProps {
  open: boolean; onClose: () => void;
  title: React.ReactNode; subtitle?: React.ReactNode;
  width?: number;                      // default 780
  footer?: React.ReactNode; children: React.ReactNode;
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Right-side dialog: Esc closes, veil click closes, focus trap, body scroll lock. */
export function Drawer({ open, onClose, title, subtitle, width = 780, footer, children }: DrawerProps) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const prevActive = document.activeElement as HTMLElement | null;
    const focusables = () => Array.from(ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    const t = setTimeout(() => (focusables()[0] ?? ref.current)?.focus(), 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
      if (e.key !== 'Tab') return;
      const f = focusables();
      if (!f.length) { e.preventDefault(); ref.current?.focus(); return; }
      const first = f[0], last = f[f.length - 1];
      const inside = ref.current?.contains(document.activeElement);
      if (!inside) { e.preventDefault(); first.focus(); return; }
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(t);
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKey);
      prevActive?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <>
      <div className="drawer-veil" onClick={onClose} aria-hidden />
      <aside ref={ref} className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId}
        tabIndex={-1} style={{ width: `min(${width}px, 96vw)` }}>
        <div className="drawer-head">
          <div className="drawer-head-main">
            <h2 id={titleId} className="drawer-title">{title}</h2>
            {subtitle ? <div className="drawer-sub">{subtitle}</div> : null}
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">✕ Close</button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer ? <div className="drawer-foot">{footer}</div> : null}
      </aside>
    </>
  );
}
