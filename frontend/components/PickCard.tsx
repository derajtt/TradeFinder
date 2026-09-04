'use client';
import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api';
import { fmtEtShort, fmtPct, fmtPrice } from '../lib/format';
import type { SignalRow } from '../lib/types';
import { catalystLabel } from '../lib/vocab';
import Freshness from './Freshness';
import s from './today.module.css';
import { Advanced } from './ui/Advanced';
import { ScorePill } from './ui/ScorePill';
import { StatusPill } from './ui/StatusPill';

export interface PickCardProps {
  row: SignalRow;
  onSelect: (r: SignalRow) => void;
  marketOpen: boolean;
  minScoreForBuy?: number;
  quoteFreshnessSec?: number;
}

/* company name: `/api/candidates/{symbol}` → company.name, fetched once per symbol */
const nameCache = new Map<string, string | null>();
function useCompanyName(symbol: string, known: string | undefined): string | null {
  const [name, setName] = useState<string | null>(() => known ?? nameCache.get(symbol) ?? null);
  useEffect(() => {
    if (known) { setName(known); return; }
    const hit = nameCache.get(symbol);
    if (hit !== undefined) { setName(hit); return; }
    let alive = true;
    apiGet<{ company?: { name?: string } | null }>(`/api/candidates/${encodeURIComponent(symbol)}`)
      .then((d) => { const n = d?.company?.name ?? null; nameCache.set(symbol, n); if (alive) setName(n); })
      .catch(() => { nameCache.set(symbol, null); });
    return () => { alive = false; };
  }, [symbol, known]);
  return name;
}

function signCls(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return 'dim';
  return v >= 0 ? 'pos' : 'neg';
}

/** One Buy pick: the plan (buy / stop / targets), where it is now, and when it
 *  was picked. Every value carries its label; the only action opens the drawer. */
export default function PickCard({ row, onSelect, marketOpen, minScoreForBuy, quoteFreshnessSec }: PickCardProps) {
  const name = useCompanyName(row.symbol, row.name);
  return (
    <article className={s.pick} aria-label={`Buy pick ${row.symbol}`}>
      <div className={s.pickHead}>
        <span className={s.pickSym}>{row.symbol}</span>
        <StatusPill size="sm" label="Buy" tone="buy" />
        {name ? <span className={s.pickName}>{name}</span> : null}
        <Advanced><ScorePill value={row.score} minBuy={minScoreForBuy} /></Advanced>
      </div>

      <div className={s.kv}>
        <div>
          <div className={s.k}>Buy price</div>
          <div className={s.v}>{fmtPrice(row.buy_price)}</div>
        </div>
        <div>
          <div className={s.k}>Now</div>
          <div className={s.v}>{fmtPrice(row.current)}</div>
          <div className={s.vSub}>
            <Freshness ts={row.current_ts} marketOpen={marketOpen} thresholdSec={quoteFreshnessSec} />
          </div>
        </div>
        <div>
          <div className={s.k}>Since pick</div>
          <div className={`${s.v} ${signCls(row.change_pct)}`}>{fmtPct(row.change_pct)}</div>
        </div>
      </div>

      <div className={s.kv}>
        <div>
          <div className={s.k}>Stop</div>
          <div className={`${s.v} neg`}>{fmtPrice(row.stop)}</div>
        </div>
        <div>
          <div className={s.k}>Target 1</div>
          <div className={`${s.v} pos`}>{fmtPrice(row.target1)}</div>
        </div>
        <div>
          <div className={s.k}>Target 2</div>
          <div className={`${s.v} pos`}>{fmtPrice(row.target2)}</div>
        </div>
      </div>

      <div className={s.pickFoot}>
        <span>
          {catalystLabel(row.catalyst_type)} · Picked {fmtEtShort(row.initiated_at)}
          <Advanced>{row.price_source ? <> · price source <code className="pill-raw">{row.price_source}</code></> : null}</Advanced>
        </span>
        <button type="button" className="btn sm" onClick={() => onSelect(row)}>See the plan</button>
      </div>
    </article>
  );
}
