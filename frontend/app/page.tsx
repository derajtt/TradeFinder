'use client';
import { useCallback, useMemo, useRef, useState } from 'react';
import AttentionChips from '../components/AttentionChips';
import BuyPicks from '../components/BuyPicks';
import CandidateTable from '../components/CandidateTable';
import DetailDrawer from '../components/DetailDrawer';
import FunnelStrip from '../components/FunnelStrip';
import NoonCard from '../components/NoonCard';
import OpsPanel from '../components/OpsPanel';
import PositionsTable, { type LiveQuote } from '../components/PositionsTable';
import RadarTable from '../components/RadarTable';
import RejectedTable from '../components/RejectedTable';
import SessionStrip from '../components/SessionStrip';
import SignalTable from '../components/SignalTable';
import StatusLine from '../components/StatusLine';
import TrustTiles from '../components/TrustTiles';
import s from '../components/today.module.css';
import { Advanced, SectionHeader, StrategyScope, useScopeLabel } from '../components/ui';
import { usePollingState, useSharedPoll, useStream } from '../lib/api';
import { fmtEtShort } from '../lib/format';
import { useMode } from '../lib/mode';
import { useProfile } from '../lib/profile';
import { phaseKeyOf, useAppSettings, useOps, useStatus } from '../lib/status';
import type {
  AlertRow, Brief, CandidateRow, Canonical, Digest, Noon, Position, RadarRow, Rejected, SignalRow,
} from '../lib/types';

type LiveSignal = Partial<SignalRow> & { signal_uid: string };

/** Fold the stream's partial signal updates into the polled rows (initiation
 *  fields never change; only live price fields are overwritten). */
function mergeLive(rows: SignalRow[], live: Record<string, LiveSignal>): SignalRow[] {
  return rows.map((r) => {
    const u = live[r.signal_uid];
    if (!u) return r;
    return {
      ...r,
      current: u.current ?? r.current, current_ts: u.current_ts ?? r.current_ts,
      day_high: u.day_high ?? r.day_high, day_low: u.day_low ?? r.day_low,
      since_high: u.since_high ?? r.since_high, since_low: u.since_low ?? r.since_low,
      change_pct: u.change_pct ?? r.change_pct, change_abs: u.change_abs ?? r.change_abs,
      max_gain_pct: u.max_gain_pct ?? r.max_gain_pct, max_drawdown_pct: u.max_drawdown_pct ?? r.max_drawdown_pct,
    };
  });
}

/** Today — "Is there anything the system says to buy right now — and if not,
 *  why not and when next?" One data layer (spec §2 table); every section gets
 *  `loaded` and shows skeletons until its first response. */
export default function Today() {
  const [profile] = useProfile();
  const scopeLabel = useScopeLabel();
  const { advanced } = useMode();

  /* ── data layer (spec §2) ─────────────────────────────────────────────── */
  const { status, loaded: statusLoaded } = useStatus();                 // 15s + stream (provider)
  const { ops, loaded: opsLoaded } = useOps();                          // 30s shared
  const { settings, loaded: settingsLoaded } = useAppSettings();        // 120s shared
  const sig = usePollingState<{ rows: SignalRow[] }>(`/api/signals?active_only=true&profile=${profile}`, 30000);
  const cand = usePollingState<{ rows: CandidateRow[]; radar?: RadarRow[] }>('/api/candidates', 30000);
  const pos = usePollingState<{ rows: Position[] }>(`/api/positions?profile=${profile}`, 20000);
  const alerts = usePollingState<{ rows: AlertRow[] }>('/api/alerts', 60000);
  const canon = usePollingState<Canonical>(`/api/report/canonical?profile=${profile}`, 30000);
  const noon = usePollingState<Noon>('/api/outcomes/noon', 60000);
  // Advanced-only fetches stay idle (empty path) in Simple.
  const digest = usePollingState<Digest>(advanced ? '/api/digest' : '', 60000);
  const brief = usePollingState<Brief>(advanced ? '/api/brief' : '', 120000);
  const canonAll = useSharedPoll<Canonical>(advanced ? '/api/report/canonical' : '', 60000);
  const rejected = usePollingState<{ rows: Rejected[] }>(advanced ? `/api/rejected?profile=${profile}` : '', 60000);

  /* ── stream overlays ──────────────────────────────────────────────────── */
  const [liveRows, setLiveRows] = useState<CandidateRow[] | null>(null);
  const [liveRadar, setLiveRadar] = useState<RadarRow[] | null>(null);
  const [liveSigs, setLiveSigs] = useState<Record<string, LiveSignal>>({});
  const [updated, setUpdated] = useState<Set<string>>(new Set());
  const clearRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reloadSigs = sig.reload;

  useStream({
    candidates: (d: { rows?: CandidateRow[]; radar?: RadarRow[] }) => {
      const rows = d.rows ?? [];
      setLiveRows(rows);
      if (d.radar) setLiveRadar(d.radar);
      setUpdated(new Set(rows.map((r) => r.symbol)));
      if (clearRef.current) clearTimeout(clearRef.current);
      clearRef.current = setTimeout(() => setUpdated(new Set()), 1600);
    },
    signals: (d: { rows?: LiveSignal[] }) => {
      setLiveSigs((prev) => {
        const next = { ...prev };
        for (const u of d.rows ?? []) if (u.signal_uid) next[u.signal_uid] = u;
        return next;
      });
    },
    buy_signal: () => reloadSigs(),
  });

  /* ── derived ──────────────────────────────────────────────────────────── */
  const candRows = liveRows ?? cand.data?.rows ?? [];
  const radar = liveRadar ?? cand.data?.radar ?? [];
  const candLoaded = cand.loaded || liveRows !== null;

  const sigRows = useMemo(() => mergeLive(sig.data?.rows ?? [], liveSigs), [sig.data, liveSigs]);
  /* A Buy pick is a row that passed every check, which is what the canonical
     lifecycle records.  signal_type + status alone also admitted rows with no
     lifecycle — and those are exactly the rows carrying no stop or targets, so
     the card rendered a "Buy" with an empty plan. */
  const buys = useMemo(() => sigRows.filter(
    (r) => r.signal_type === 'buy' && r.status === 'active'
      && r.lifecycle === 'ACTIONABLE_BUY'), [sigRows]);
  const allWatches = useMemo(() => sigRows
    .filter((r) => r.signal_type === 'watch' && r.status === 'active')
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1)), [sigRows]);
  // Simple: a stock that is already a Buy pick has become a buy — it is not repeated under
  // "could become buys" (its Buy card is right above). Advanced keeps every tracked row.
  const buySyms = useMemo(() => new Set(buys.map((r) => r.symbol)), [buys]);
  const watches = useMemo(
    () => (advanced ? allWatches : allWatches.filter((r) => !buySyms.has(r.symbol))),
    [advanced, allWatches, buySyms]);
  const watchesNotRepeated = advanced ? 0 : new Set(allWatches.filter((r) => buySyms.has(r.symbol)).map((r) => r.symbol)).size;
  const liveBySymbol = useMemo(() => {
    const m: Record<string, LiveQuote> = {};
    for (const r of sigRows) {
      const prev = m[r.symbol];
      if (!prev || (r.current_ts ?? '') > (prev.current_ts ?? '')) m[r.symbol] = { current: r.current, current_ts: r.current_ts };
    }
    return m;
  }, [sigRows]);
  const latestWatchTs = useMemo(() => watches.reduce<string | null>(
    (a, r) => (r.current_ts && (!a || r.current_ts > a) ? r.current_ts : a), null), [watches]);

  const positions = pos.data?.rows ?? [];
  const phaseKey = phaseKeyOf(status?.phase);
  const marketClosed = !(phaseKey === 'premarket' || phaseKey === 'open');
  const confirmAt = settings?.buy_confirm_after_et ?? null;
  const minBuy = settings?.min_score_for_buy;
  const freshSec = settings?.quote_freshness_sec;
  const chipsLoaded = statusLoaded && sig.loaded && pos.loaded && alerts.loaded && noon.loaded;

  const [selected, setSelected] = useState<string | null>(null);
  const onSelectSym = useCallback((sym: string) => setSelected(sym), []);
  const onSelectRow = useCallback((r: SignalRow) => setSelected(r.symbol), []);

  const watchTitle = !sig.loaded ? 'Watching'
    : watches.length ? `Watching — ${watches.length} stock${watches.length === 1 ? '' : 's'}` : 'Watching — none';
  // ScorePill bands: ≥ min Strong · ≥ 55 OK · else Weak — the OK band only exists when min > 55.
  const scoreNote = minBuy == null ? undefined
    : minBuy > 55
      ? `Score out of 100 · ≥ ${minBuy} needed to buy · Strong ≥ ${minBuy} · OK 55–${minBuy - 1} · Weak < 55`
      : `Score out of 100 · Strong ≥ ${minBuy} (needed to buy) · Weak < ${minBuy}`;
  // "What's missing" reads the live candidate list; when the last cycle found no candidates every
  // cell would say "Not in today's scan", so the column is left out and the note says why.
  const wmAvailable = !candLoaded || candRows.length > 0;
  const wmNote = wmAvailable || advanced ? undefined
    : `What's missing appears while the scanner has candidates — the last cycle found none`;
  const watchNote = [scoreNote, wmNote].filter(Boolean).join(' · ') || undefined;
  const watchCaption = `Tracked · ${scopeLabel} · qualified by the scanner but not yet buys · prices as of ${latestWatchTs ? fmtEtShort(latestWatchTs) : '—'}`
    + (watchesNotRepeated > 0 ? ` · ${watchesNotRepeated} already ${watchesNotRepeated === 1 ? 'a Buy pick' : 'Buy picks'} above, not repeated` : '');
  const scannerCaption = statusLoaded && status?.scanner
    ? `All models · ${status.scanner.candidates} candidates in the last cycle`
    : 'All models';

  return (
    <>
      {/* 2.0 + 2.1 — scope, status line, attention chips */}
      <div className={s.head}>
        <StatusLine ops={ops} opsLoaded={opsLoaded}
          digest={digest.data} brief={brief.data} canonicalAll={canonAll.data} />
        <StrategyScope />
      </div>
      <AttentionChips buys={buys} watches={watches} positions={positions}
        alerts={alerts.data?.rows ?? []} noon={noon.data}
        quoteFreshnessSec={freshSec} loaded={chipsLoaded} />

      {/* 2.2 — premarket clock */}
      <SessionStrip confirmAt={confirmAt} loaded={settingsLoaded} />

      {/* 2.3 — buy picks */}
      <BuyPicks rows={buys} loaded={sig.loaded && opsLoaded} scopeLabel={scopeLabel}
        quietReason={ops?.quiet_reason} confirmAt={confirmAt} nextScanStart={status?.next_scan_start}
        onSelect={onSelectRow} minScoreForBuy={minBuy} quoteFreshnessSec={freshSec} />

      {/* 2.4 — watching */}
      <SectionHeader id="watching" title={watchTitle}
        question="Which stocks is the model watching, what is missing, and how are they doing since it spotted them?"
        caption={watchCaption}
        evidence="TRACKED"
        note={watchNote} />
      <SignalTable rows={watches} variant="watch" scope={scopeLabel} loaded={sig.loaded} cap={8}
        marketClosed={marketClosed} minScoreForBuy={minBuy} onSelect={onSelectRow}
        showWhatsMissing={wmAvailable ? undefined : false} evidenceChip={false} />

      {/* 2.5 — scanner */}
      <SectionHeader id="scanner" title="Scanner — what it is looking at"
        question="What stocks are being checked right now, and what is blocking them?"
        caption={scannerCaption} />
      <CandidateTable rows={candRows} updatedSyms={updated} onSelect={onSelectSym} loaded={candLoaded}
        minScoreForBuy={minBuy} phaseKey={phaseKey} quietReason={ops?.quiet_reason}
        lastCycleAt={status?.scanner?.last_cycle_at} />

      {/* 2.6 — trust */}
      <TrustTiles canonical={canon.data} status={status} noon={noon.data} scopeLabel={scopeLabel}
        earlyWindowMin={settings?.early_window_min} loaded={canon.loaded && statusLoaded && noon.loaded} />
      <Advanced><NoonCard noon={noon.data} loaded={noon.loaded} /></Advanced>

      {/* 2.7 — open paper trades */}
      <PositionsTable rows={positions} loaded={pos.loaded} scopeLabel={scopeLabel}
        onSelect={onSelectSym} liveBySymbol={liveBySymbol} />

      {/* 2.8–2.11 — Advanced only */}
      <Advanced>
        <FunnelStrip canonical={canon.data} loaded={canon.loaded} scopeLabel={scopeLabel} />
        <OpsPanel />
        <RejectedTable rows={rejected.data?.rows ?? []} loaded={rejected.loaded} onSelect={onSelectSym} />
        <RadarTable rows={radar} onSelect={onSelectSym} loaded={candLoaded} />
      </Advanced>

      {/* 2.12 — disclaimer (unchanged text) */}
      <p className="disclaimer">
        BUY is a rules-based research signal produced by the documented scoring engine
        (momentum, catalyst, filings, liquidity, price confirmation, company quality, minus risk
        penalties). It is not investment advice, not a recommendation, and not connected to order
        execution. Data: Financial Modeling Prep &amp; SEC EDGAR; delays and gaps are labeled, never hidden.
      </p>

      {selected ? <DetailDrawer symbol={selected} onClose={() => setSelected(null)} /> : null}
    </>
  );
}
