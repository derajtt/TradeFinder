'use client';
import { usePolling } from '../lib/api';

export default function DigestCard() {
  const [d] = usePolling<{ line: string; regime_text: string }>('/api/digest', 60000);
  const [b] = usePolling<any>('/api/brief', 120000);
  if (!d) return null;
  return (
    <div className="tbl-wrap" style={{ padding: '11px 16px', marginBottom: 14 }}>
      <div style={{ fontSize: 13, lineHeight: 1.55 }}>
        <b style={{ fontSize: 10, letterSpacing: 1.4, color: 'var(--text-faint)', marginRight: 10 }}>TODAY</b>
        {d.line}
      </div>
      {b?.available && b.content && (
        <div className="faint" style={{ fontSize: 11.5, marginTop: 5 }}>
          <b className="dim">Morning brief ({b.session_date}):</b> {b.content.headline}{' '}
          {b.content.top_rejection_reasons?.length > 0 &&
            <>Top gate: {b.content.top_rejection_reasons[0][0].replace(/_/g, ' ')} ×{b.content.top_rejection_reasons[0][1]}.</>}
        </div>
      )}
    </div>
  );
}
