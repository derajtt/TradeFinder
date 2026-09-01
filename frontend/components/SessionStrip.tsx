'use client';
import { useEffect, useState } from 'react';

/** Premarket timeline 4:00 → 9:30 ET with the 7:00 broker-window marker. */
export default function SessionStrip({ confirmAt }: { confirmAt?: string }) {
  const [now, setNow] = useState<Date>(new Date());
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 30000); return () => clearInterval(id); }, []);
  const et = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const mins = et.getHours() * 60 + et.getMinutes();
  const start = 4 * 60, end = 9 * 60 + 30;
  if (mins < start - 30 || mins > end + 30) return null;
  const pct = Math.max(0, Math.min(100, ((mins - start) / (end - start)) * 100));
  let confirmPct: number | null = null;
  if (confirmAt && /^\d{1,2}:\d{2}$/.test(confirmAt)) {
    const [h, m] = confirmAt.split(':').map(Number);
    const cm = h * 60 + m;
    if (cm > start && cm < end) confirmPct = ((cm - start) / (end - start)) * 100;
  }
  return (
    <div className="session-strip" aria-label="Premarket session progress">
      <span>4:00</span>
      <div className="session-track">
        <div className="session-fill" style={{ width: `${pct}%` }} />
        {confirmPct !== null && (
          <div className="session-marker" style={{ left: `${confirmPct}%` }}
               data-label={`BUY opens ${confirmAt}`} title={`Broker premarket window opens ${confirmAt} ET — EARLY WATCH converts to BUY after this`} />
        )}
        <div className="session-now" style={{ left: `${pct}%` }} title="Now" />
      </div>
      <span>9:30 open</span>
    </div>
  );
}
