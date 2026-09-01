"""REST + SSE API for the dashboard."""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..db import get_session
from ..models import (AiUsage, BtJob, BuySignal, Candidate,
                      CandidateFeatureSnapshot, Catalyst, HealthEvent, NewsItem,
                      PaperPosition, ProviderRequest, RejectedCandidate,
                      ScannerRun, SecFiling, ShadowExit, SignalEvent,
                      SignalPriceCheckpoint, Symbol, SymbolReferenceVersion)
from ..analytics import canonical_report, effective_lifecycle
from ..strategy.profiles import DEFAULT_PROFILES, get_profiles
from ..models import LockedOutcome, PaperAccount
from ..strategy.registry import (CRYPTO_UNIVERSE, ETF_UNIVERSE, MODELS,
                                 RESEARCH_ONLY, STARTING_CASH)
from ..scoring.engine import DEFAULT_SETTINGS, STRATEGY_VERSION
from ..settings_service import get_settings, update_settings
from ..signals.service import metrics_with_outcome, signal_metrics
from ..settings_service import get_settings as _get_settings
from ..sse import broadcaster
from ..util.timeutil import next_scan_start, now_et, session_phase

router = APIRouter(prefix="/api")


def app_state(request: Request) -> Dict[str, Any]:
    return request.app.state.shared


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_session)):
    shared = app_state(request)
    sched = shared.get("scheduler")
    t = now_et()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ai_rows = (await db.execute(select(AiUsage).where(AiUsage.month == month))).scalars().all()
    day_cut = datetime.now(timezone.utc) - timedelta(hours=24)
    api_24h = (await db.execute(select(func.count(ProviderRequest.id))
                                .where(ProviderRequest.ts_utc >= day_cut))).scalar() or 0
    five_cut = datetime.now(timezone.utc) - timedelta(minutes=5)
    api_5m = (await db.execute(select(func.count(ProviderRequest.id))
                               .where(ProviderRequest.ts_utc >= five_cut))).scalar() or 0
    throttles_1h = (await db.execute(select(func.count(ProviderRequest.id))
                                     .where(ProviderRequest.status_code == 429,
                                            ProviderRequest.ts_utc >= datetime.now(timezone.utc) - timedelta(hours=1))
                                     )).scalar() or 0
    active = (await db.execute(select(func.count(BuySignal.id))
                               .where(BuySignal.status == "active",
                                      BuySignal.signal_type == "buy",
                                      BuySignal.is_demo == False))).scalar() or 0  # noqa: E712
    real = (await db.execute(select(BuySignal).where(
        BuySignal.is_demo == False,  # noqa: E712
        BuySignal.status != "invalidated"))).scalars().all()
    _settings = await _get_settings(db)
    oc = {"win": 0, "neutral": 0, "loss": 0, "pending": 0}
    for s_ in real:
        oc[metrics_with_outcome(s_, _settings)["outcome"]] += 1
    dec = oc["win"] + oc["loss"]
    return {
        "et_time": t.isoformat(), "phase": session_phase(),
        "outcomes": {**oc, "win_rate": round(oc["win"] / dec, 3) if dec else None,
                     "tracked": len(real)},
        "scanner": (sched.state if sched else {"phase": "not_started"}),
        "next_scan_start": next_scan_start().isoformat(),
        "active_signals": active,
        "api_calls_24h": api_24h,
        "api_calls_per_min": round(api_5m / 5.0, 1),
        "api_throttles_1h": throttles_1h,
        "ai_usage_month": {"calls": len(ai_rows),
                           "est_cost_usd": round(sum(r.est_cost_usd for r in ai_rows), 4)},
        "strategy_version": STRATEGY_VERSION,
        "paper_mode": get_config().paper_mode,
    }


@router.get("/candidates")
async def candidates(request: Request):
    shared = app_state(request)
    ctx = shared.get("ctx")
    return {"rows": (ctx.candidates_live if ctx else []),
            "radar": (getattr(ctx, "radar_live", []) if ctx else []),
            "ts": (ctx.last_cycle.get("ts") if ctx else None)}


@router.get("/candidates/{symbol}")
async def candidate_detail(symbol: str, request: Request,
                           db: AsyncSession = Depends(get_session)):
    """Full drill-down for a clicked result: everything we know."""
    symbol = symbol.upper()
    shared = app_state(request)
    ctx = shared.get("ctx")
    live = next((r for r in (ctx.candidates_live if ctx else []) if r["symbol"] == symbol), None)

    snap = (await db.execute(
        select(CandidateFeatureSnapshot).join(Candidate,
                                              Candidate.id == CandidateFeatureSnapshot.candidate_id)
        .where(Candidate.symbol == symbol)
        .order_by(desc(CandidateFeatureSnapshot.id)).limit(1))).scalar_one_or_none()
    ref = (await db.execute(select(SymbolReferenceVersion)
                            .where(SymbolReferenceVersion.symbol == symbol)
                            .order_by(desc(SymbolReferenceVersion.id)).limit(1))
           ).scalar_one_or_none()
    news = (await db.execute(select(NewsItem).where(NewsItem.symbol == symbol)
                             .order_by(desc(NewsItem.published_at)).limit(25))).scalars().all()
    filings = (await db.execute(select(SecFiling).where(SecFiling.symbol == symbol)
                                .order_by(desc(SecFiling.accepted_at)).limit(25))).scalars().all()
    cat = (await db.execute(select(Catalyst).where(Catalyst.symbol == symbol)
                            .order_by(desc(Catalyst.id)).limit(1))).scalar_one_or_none()
    sym_row = (await db.execute(select(Symbol).where(Symbol.symbol == symbol))
               ).scalar_one_or_none()
    sigs = (await db.execute(select(BuySignal).where(BuySignal.symbol == symbol)
                             .order_by(desc(BuySignal.initiated_at)).limit(5))).scalars().all()

    bars = []
    if ctx:
        try:
            t = now_et()
            raw = await ctx.fmp.minute_bars(symbol, str((t - timedelta(days=5)).date()),
                                            str(t.date()))
            bars = [{"time": int(b["ts_utc"].timestamp()), "open": b["open"],
                     "high": b["high"], "low": b["low"], "close": b["close"],
                     "volume": b["volume"]} for b in raw[-960:]]
        except Exception:
            bars = []
    if not bars:
        from ..scanner.bars import all_day_bars
        bars = await all_day_bars(db, symbol, days=5)

    # watch history: every scan pass for this symbol today (score + price timeline)
    from ..util.timeutil import now_et
    today = str(now_et().date())
    watch_rows = (await db.execute(
        select(Candidate.created_at, Candidate.score, CandidateFeatureSnapshot.features)
        .join(CandidateFeatureSnapshot, CandidateFeatureSnapshot.candidate_id == Candidate.id)
        .where(Candidate.symbol == symbol, Candidate.session_date == today)
        .order_by(Candidate.created_at).limit(400))).all()
    watch = None
    if watch_rows:
        series = [{"t": r[0].isoformat(), "score": r[1],
                   "price": (r[2] or {}).get("price")} for r in watch_rows]
        first = series[0]
        last_price = next((x["price"] for x in reversed(series) if x["price"]), None)
        chg = None
        if first.get("price") and last_price:
            chg = round((last_price - first["price"]) / first["price"] * 100.0, 2)
        watch = {"started_at": first["t"], "start_price": first.get("price"),
                 "start_score": first.get("score"), "checks": len(series),
                 "change_since_watch_pct": chg, "series": series}

    return {
        "symbol": symbol,
        "live": live,
        "watch": watch,
        "company": {"name": (sym_row.name if sym_row else (live or {}).get("name", "")),
                    "exchange": (sym_row.exchange if sym_row else ""),
                    "cik": (sym_row.cik if sym_row else ""),
                    "sector": (ref.sector if ref else ""),
                    "industry": (ref.industry if ref else ""),
                    "country": (ref.country if ref else ""),
                    "market_cap": (ref.market_cap if ref else None),
                    "float_shares": (ref.float_shares if ref else None),
                    "shares_outstanding": (ref.shares_outstanding if ref else None),
                    "avg_volume": (ref.avg_volume if ref else None),
                    "description": (((ref.payload or {}).get("profile") or {}).get("description", "") if ref else ""),
                    "website": (((ref.payload or {}).get("profile") or {}).get("website", "") if ref else ""),
                    "free_float_pct": (((ref.payload or {}).get("float") or {}).get("free_float_pct") if ref else None)},
        "snapshot": ({"features": snap.features, "score_detail": snap.score_detail,
                      "at": snap.created_at.isoformat()} if snap else None),
        "catalyst": ({"direction": cat.direction, "materiality": cat.materiality,
                      "novelty": cat.novelty, "confidence": cat.confidence,
                      "type": cat.catalyst_type, "summary": cat.summary,
                      "dilution": cat.dilution_detected,
                      "going_concern": cat.going_concern_detected,
                      "facts": (cat.analysis or {}).get("facts", []),
                      "risks": (cat.analysis or {}).get("risks", []),
                      "source_url": (cat.analysis or {}).get("source_url", ""),
                      "ai": cat.status == "ok"} if cat else None),
        "news": [{"headline": n.headline, "source": n.source, "url": n.url,
                  "kind": n.kind, "published_at":
                  n.published_at.isoformat() if n.published_at else None} for n in news],
        "filings": [{"form": f.form_type, "items": f.items, "title": f.title,
                     "url": f.primary_doc_url, "accession": f.accession,
                     "accepted_at": f.accepted_at.isoformat() if f.accepted_at else None}
                    for f in filings],
        "signals": [{"signal_uid": s.signal_uid, "initiated_at": s.initiated_at.isoformat(),
                     "buy_price": s.buy_signal_price, "current": s.current_live_price,
                     "status": s.status, **signal_metrics(s)} for s in sigs],
        "bars": bars,
    }


@router.get("/signals")
async def signals(active_only: bool = False, include_demo: bool = False,
                  limit: int = 200, profile: str = "",
                  db: AsyncSession = Depends(get_session)):
    q = select(BuySignal).order_by(desc(BuySignal.initiated_at)).limit(min(limit, 1000))
    if profile:
        q = q.where((BuySignal.profile == profile) |
                    (BuySignal.profile == "") | (BuySignal.profile.is_(None))
                    if profile == "primary" else BuySignal.profile == profile)
    if active_only:
        q = q.where(BuySignal.status == "active")
    if not include_demo:
        q = q.where(BuySignal.is_demo == False)  # noqa: E712
    rows = (await db.execute(q)).scalars().all()
    _settings = await _get_settings(db)
    out = []
    for s in rows:
        cps = (await db.execute(select(SignalPriceCheckpoint)
                                .where(SignalPriceCheckpoint.signal_id == s.id))).scalars().all()
        out.append({
            "signal_uid": s.signal_uid, "symbol": s.symbol,
            "session_date": s.session_date, "strategy_version": s.strategy_version,
            "initiated_at": s.initiated_at.isoformat(),
            "buy_price": s.buy_signal_price, "price_source": s.price_source,
            "current": s.current_live_price,
            "current_ts": s.current_price_ts.isoformat() if s.current_price_ts else None,
            "day_high": s.day_high, "day_low": s.day_low,
            "since_high": s.since_signal_high, "since_low": s.since_signal_low,
            "status": s.status, "is_demo": s.is_demo,
            "signal_type": getattr(s, "signal_type", "buy"),
            "profile": getattr(s, "profile", "primary") or "primary",
            "lifecycle": getattr(s, "lifecycle", "") or "",
            "score": (s.score_snapshot or {}).get("score"),
            "catalyst_type": ((s.evidence_snapshot or {}).get("catalyst") or {}).get("catalyst_type", ""),
            "checkpoints": {c.label: {"price": c.price, "pct": c.pct_from_signal} for c in cps},
            **metrics_with_outcome(s, _settings),
        })
    return {"rows": out}


@router.get("/signals/export.csv")
async def signals_csv(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(BuySignal).where(BuySignal.is_demo == False)  # noqa: E712
                             .order_by(desc(BuySignal.initiated_at)))).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["signal_uid", "symbol", "session_date", "initiated_at_utc", "buy_price",
                "current", "day_high", "day_low", "since_high", "since_low",
                "change_pct", "max_gain_pct", "max_drawdown_pct", "score",
                "strategy_version", "status"])
    for s in rows:
        m = signal_metrics(s)
        w.writerow([s.signal_uid, s.symbol, s.session_date, s.initiated_at.isoformat(),
                    s.buy_signal_price, s.current_live_price, s.day_high, s.day_low,
                    s.since_signal_high, s.since_signal_low, m["change_pct"],
                    m["max_gain_pct"], m["max_drawdown_pct"],
                    (s.score_snapshot or {}).get("score"), s.strategy_version, s.status])
    buf.seek(0)
    return StreamingResponse(iter([buf.read()]), media_type="text/csv",
                             headers={"Content-Disposition":
                                      "attachment; filename=signals.csv"})


@router.get("/signals/{signal_uid}")
async def signal_detail(signal_uid: str, db: AsyncSession = Depends(get_session)):
    s = (await db.execute(select(BuySignal).where(BuySignal.signal_uid == signal_uid))
         ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "signal not found")
    events = (await db.execute(select(SignalEvent).where(SignalEvent.signal_id == s.id)
                               .order_by(SignalEvent.id))).scalars().all()
    cps = (await db.execute(select(SignalPriceCheckpoint)
                            .where(SignalPriceCheckpoint.signal_id == s.id))).scalars().all()
    return {
        "signal_uid": s.signal_uid, "symbol": s.symbol, "session_date": s.session_date,
        "strategy_version": s.strategy_version, "initiated_at": s.initiated_at.isoformat(),
        "buy_price": s.buy_signal_price, "price_source": s.price_source,
        "current": s.current_live_price, "day_high": s.day_high, "day_low": s.day_low,
        "since_high": s.since_signal_high, "since_low": s.since_signal_low,
        "status": s.status, "is_demo": s.is_demo,
        "signal_type": getattr(s, "signal_type", "buy"),
        "score_snapshot": s.score_snapshot, "evidence_snapshot": s.evidence_snapshot,
        "checkpoints": {c.label: {"price": c.price, "pct": c.pct_from_signal} for c in cps},
        "events": [{"ts": e.ts_utc.isoformat(), "type": e.event_type, "detail": e.detail}
                   for e in events],
        **metrics_with_outcome(s, await _get_settings(db)),
    }


@router.get("/performance")
async def performance(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(BuySignal).where(
        BuySignal.is_demo == False,  # noqa: E712
        BuySignal.signal_type == "buy"))).scalars().all()
    def band(score):
        if score is None: return "unknown"
        if score >= 90: return "90+"
        if score >= 80: return "80-89"
        return "75-79"
    def cap_band(mc):
        if mc is None: return "unknown"
        if mc < 50e6: return "nano <$50M"
        if mc < 300e6: return "micro $50-300M"
        if mc < 2e9: return "small $300M-2B"
        return "mid+ >$2B"
    aggs: Dict[str, Dict[str, Any]] = {}
    def add(group: str, key: str, chg: Optional[float], mg: Optional[float], md: Optional[float]):
        g = aggs.setdefault(group, {}).setdefault(key, {"n": 0, "wins": 0, "chg": [], "mg": [], "md": []})
        g["n"] += 1
        if chg is not None:
            g["chg"].append(chg)
            if chg > 0: g["wins"] += 1
        if mg is not None: g["mg"].append(mg)
        if md is not None: g["md"].append(md)
    for s in rows:
        m = signal_metrics(s)
        score = (s.score_snapshot or {}).get("score")
        cat = ((s.evidence_snapshot or {}).get("catalyst") or {}).get("catalyst_type") or "none"
        mc = (((s.score_snapshot or {}).get("inputs") or {}).get("market_cap"))
        hour = s.initiated_at.astimezone(timezone.utc).hour
        add("score_band", band(score), m["change_pct"], m["max_gain_pct"], m["max_drawdown_pct"])
        add("catalyst", cat[:32], m["change_pct"], m["max_gain_pct"], m["max_drawdown_pct"])
        add("market_cap", cap_band(mc), m["change_pct"], m["max_gain_pct"], m["max_drawdown_pct"])
        add("strategy_version", s.strategy_version, m["change_pct"], m["max_gain_pct"], m["max_drawdown_pct"])
    def finish(d):
        out = {}
        for k, g in d.items():
            n = g["n"]
            out[k] = {"n": n, "win_rate": round(g["wins"] / n, 3) if n else None,
                      "avg_change_pct": round(sum(g["chg"]) / len(g["chg"]), 3) if g["chg"] else None,
                      "avg_max_gain_pct": round(sum(g["mg"]) / len(g["mg"]), 3) if g["mg"] else None,
                      "avg_max_drawdown_pct": round(sum(g["md"]) / len(g["md"]), 3) if g["md"] else None}
        return out
    # win/loss scoreboard (user rule): +10% reached after the broker window = win,
    # finished below found price = loss, else neutral; pending until data exists
    all_real = (await db.execute(select(BuySignal).where(
        BuySignal.is_demo == False,  # noqa: E712
        BuySignal.status != "invalidated"))).scalars().all()
    outcomes = {"win": 0, "neutral": 0, "loss": 0, "pending": 0}
    by_type: Dict[str, Dict[str, int]] = {}
    _settings = await _get_settings(db)
    for s in all_real:
        o = metrics_with_outcome(s, _settings)["outcome"]
        outcomes[o] += 1
        t = getattr(s, "signal_type", "buy")
        by_type.setdefault(t, {"win": 0, "neutral": 0, "loss": 0, "pending": 0})[o] += 1
    decided = outcomes["win"] + outcomes["loss"]
    return {"total_signals": len(rows),
            "outcomes": {**outcomes,
                         "win_rate": round(outcomes["win"] / decided, 3) if decided else None,
                         "by_type": by_type},
            "groups": {k: finish(v) for k, v in aggs.items()}}


@router.get("/settings")
async def read_settings(db: AsyncSession = Depends(get_session)):
    s = await get_settings(db)
    return {"settings": s, "defaults": DEFAULT_SETTINGS,
            "env_status": get_config().provider_status(),
            "strategy_version": STRATEGY_VERSION}


@router.put("/settings")
async def write_settings(patch: Dict[str, Any], db: AsyncSession = Depends(get_session)):
    updated = await update_settings(db, patch or {})
    await broadcaster.publish("settings", {"settings": updated})
    return {"settings": updated}


@router.post("/scanner/pause")
async def pause(db: AsyncSession = Depends(get_session)):
    return {"settings": await update_settings(db, {"paused": True})}


@router.post("/scanner/resume")
async def resume(db: AsyncSession = Depends(get_session)):
    return {"settings": await update_settings(db, {"paused": False})}


@router.get("/health/detail")
async def health_detail(request: Request, db: AsyncSession = Depends(get_session)):
    cut = datetime.now(timezone.utc) - timedelta(hours=6)
    reqs = (await db.execute(select(ProviderRequest).where(ProviderRequest.ts_utc >= cut)
                             .order_by(desc(ProviderRequest.id)).limit(400))).scalars().all()
    by_ep: Dict[str, Dict[str, Any]] = {}
    for r in reqs:
        e = by_ep.setdefault(f"{r.provider}:{r.endpoint}",
                             {"provider": r.provider, "endpoint": r.endpoint, "calls": 0,
                              "ok": 0, "last_status": None, "last_ts": None,
                              "avg_latency_ms": 0, "last_count": 0, "_lat": []})
        e["calls"] += 1
        e["ok"] += 1 if r.ok else 0
        e["_lat"].append(r.latency_ms)
        if e["last_ts"] is None or r.ts_utc.isoformat() > e["last_ts"]:
            e["last_ts"] = r.ts_utc.isoformat()
            e["last_status"] = r.status_code
            e["last_count"] = r.record_count
    for e in by_ep.values():
        lat = e.pop("_lat")
        e["avg_latency_ms"] = int(sum(lat) / len(lat)) if lat else 0
    events = (await db.execute(select(HealthEvent).order_by(desc(HealthEvent.id)).limit(50))
              ).scalars().all()
    runs = (await db.execute(select(ScannerRun).order_by(desc(ScannerRun.id)).limit(20))
            ).scalars().all()
    shared = app_state(request)
    sched = shared.get("scheduler")
    ctx = shared.get("ctx")
    return {
        "env_status": get_config().provider_status(),
        "entitlements": (ctx.fmp.entitlements if ctx else {}),
        "endpoints": sorted(by_ep.values(), key=lambda x: (x["provider"], x["endpoint"])),
        "events": [{"ts": e.ts_utc.isoformat(), "level": e.level,
                    "component": e.component, "message": e.message} for e in events],
        "runs": [{"id": r.id, "started": r.started_at.isoformat(),
                  "finished": r.finished_at.isoformat() if r.finished_at else None,
                  "phase": r.phase, "status": r.status, "universe": r.universe_size,
                  "shortlisted": r.shortlisted, "enriched": r.enriched,
                  "api_calls": r.api_calls, "error": r.error} for r in runs],
        "scheduler": (sched.state if sched else None),
    }


@router.get("/stream")
async def stream(request: Request):
    q = await broadcaster.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {event}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await broadcaster.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/report/canonical")
async def report_canonical(profile: str = "", db: AsyncSession = Depends(get_session)):
    """THE single source for every displayed total."""
    return await canonical_report(db, await _get_settings(db), profile=profile or None)


@router.get("/profiles")
async def profiles_get(db: AsyncSession = Depends(get_session)):
    s = await _get_settings(db)
    return {"profiles": get_profiles(s)}


@router.put("/profiles")
async def profiles_put(payload: Dict[str, Any],
                       db: AsyncSession = Depends(get_session)):
    """payload = {profiles: {id: {name, enabled, color, description, overrides}}}"""
    from ..settings_service import update_settings as _upd
    incoming = payload.get("profiles")
    if not isinstance(incoming, dict):
        raise HTTPException(400, "profiles dict required")
    clean = {}
    for pid, cfg in list(incoming.items())[:12]:
        if not isinstance(cfg, dict):
            continue
        clean[str(pid)[:24]] = {
            "name": str(cfg.get("name", pid))[:32],
            "enabled": bool(cfg.get("enabled")),
            "color": str(cfg.get("color", "#93a1bd"))[:16],
            "description": str(cfg.get("description", ""))[:300],
            "overrides": {k: v for k, v in (cfg.get("overrides") or {}).items()
                          if isinstance(k, str)}}
    s = await _upd(db, {"profiles": clean})
    return {"profiles": get_profiles(s)}


@router.get("/rejected")
async def rejected(limit: int = 100, profile: str = "",
                   db: AsyncSession = Depends(get_session)):
    q = select(RejectedCandidate).order_by(desc(RejectedCandidate.id)).limit(min(limit, 400))
    if profile:
        q = q.where(RejectedCandidate.profile == profile)
    rows = (await db.execute(q)).scalars().all()
    return {"rows": [{
        "symbol": r.symbol, "session_date": r.session_date,
        "profile": r.profile,
        "rejected_at": r.rejected_at.isoformat(), "lifecycle": r.lifecycle,
        "reason": r.rejection_reason, "failed_gates": r.failed_gates,
        "price": r.price_at_reject, "score": r.score,
        "shadow_high": r.shadow_high, "shadow_low": r.shadow_low,
        "shadow_last": r.shadow_last,
        "missed_move_pct": (round((r.shadow_high - r.price_at_reject)
                                  / r.price_at_reject * 100, 2)
                            if r.shadow_high and r.price_at_reject else None),
    } for r in rows]}


@router.get("/positions")
async def positions(profile: str = "", db: AsyncSession = Depends(get_session)):
    q = select(PaperPosition).order_by(desc(PaperPosition.id)).limit(200)
    if profile:
        q = q.where(PaperPosition.profile == profile)
    rows = (await db.execute(q)).scalars().all()
    return {"rows": [{
        "symbol": p.symbol, "status": p.status, "profile": p.profile,
        "opened_at": p.opened_at.isoformat(),
        "entry_fill": p.entry_fill, "stop": p.stop, "target1": p.target1,
        "target2": p.target2, "size_usd": p.size_usd,
        "remaining_frac": p.remaining_frac, "realized_r": p.realized_r,
        "exit_reason": p.exit_reason,
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "strategy_version": p.strategy_version, "events": (p.events or [])[-8:],
    } for p in rows]}


@router.post("/backtest/import")
async def backtest_import(payload: Dict[str, Any],
                          db: AsyncSession = Depends(get_session)):
    """The local runner posts its final artifacts here for the dashboard."""
    job = BtJob(kind=str(payload.get("kind", "replay"))[:24],
                config=payload.get("config") or {},
                config_hash=str(payload.get("config_hash", ""))[:64],
                status="done", result=payload.get("result") or {})
    db.add(job)
    await db.commit()
    return {"ok": True, "job_id": job.id}


@router.get("/backtest/report")
async def backtest_report(db: AsyncSession = Depends(get_session)):
    job = (await db.execute(select(BtJob).where(BtJob.status == "done")
                            .order_by(desc(BtJob.id)).limit(1))).scalar_one_or_none()
    if not job:
        return {"available": False,
                "note": "No backtest imported yet. Run backend/scripts/backtest_run.py."}
    return {"available": True, "created_at": job.created_at.isoformat(),
            "kind": job.kind, "config_hash": job.config_hash, "result": job.result}


@router.get("/models")
async def models_list(request: Request, db: AsyncSession = Depends(get_session)):
    """Registry + per-model account, signal counts, and enablement."""
    s = await _get_settings(db)
    profs = get_profiles(s)
    accounts = {a.model_id: a for a in
                (await db.execute(select(PaperAccount))).scalars().all()}
    sig_counts: Dict[str, Dict[str, int]] = {}
    rows = (await db.execute(select(BuySignal.profile, BuySignal.lifecycle,
                                    func.count(BuySignal.id))
                             .where(BuySignal.is_demo == False)  # noqa: E712
                             .group_by(BuySignal.profile, BuySignal.lifecycle))).all()
    for prof, lc, n in rows:
        sig_counts.setdefault(prof or "primary", {})[lc or "legacy"] = n
    shared = app_state(request)
    sched = shared.get("scheduler")
    out = []
    for mid, meta in MODELS.items():
        acc = accounts.get(mid)
        pcfg = profs.get(mid) or {}
        out.append({
            "id": mid, **{k: meta.get(k) for k in
                          ("name", "engine", "asset_classes", "cadence",
                           "horizon", "color", "edge", "universe",
                           "experimental", "data_notes", "hypothesis")},
            "enabled": pcfg.get("enabled", True),
            "account": ({"cash": acc.cash, "equity": acc.equity,
                         "realized_pnl": acc.realized_pnl,
                         "max_drawdown_pct": acc.max_drawdown_pct,
                         "trades_closed": acc.trades_closed, "wins": acc.wins,
                         "return_pct": round((acc.equity / acc.starting_cash - 1)
                                             * 100, 2)}
                        if acc else {"cash": STARTING_CASH,
                                     "equity": STARTING_CASH,
                                     "realized_pnl": 0, "max_drawdown_pct": 0,
                                     "trades_closed": 0, "wins": 0,
                                     "return_pct": 0.0}),
            "signals": sig_counts.get(mid, {}),
        })
    return {"models": out, "research_only": RESEARCH_ONLY,
            "regime": (getattr(sched, "last_regime", None) if sched else None),
            "universes": {"stocks": ETF_UNIVERSE, "crypto": CRYPTO_UNIVERSE}}


@router.get("/competition")
async def competition(db: AsyncSession = Depends(get_session)):
    """The $10,000 leaderboards. Cohort: live forward paper only."""
    accs = (await db.execute(select(PaperAccount))).scalars().all()
    cards = []
    for a in accs:
        meta = MODELS.get(a.model_id, {})
        wr = (a.wins / a.trades_closed) if a.trades_closed else None
        cards.append({"model_id": a.model_id,
                      "name": meta.get("name", a.model_id),
                      "color": meta.get("color", "#93a1bd"),
                      "experimental": meta.get("experimental", False),
                      "season": a.season, "equity": a.equity, "cash": a.cash,
                      "return_pct": round((a.equity / a.starting_cash - 1) * 100, 2),
                      "realized_pnl": a.realized_pnl,
                      "max_drawdown_pct": a.max_drawdown_pct,
                      "trades": a.trades_closed, "wins": a.wins,
                      "win_rate": round(wr, 3) if wr is not None else None})
    boards = {}
    with_trades = [c for c in cards if c["trades"] >= 1]
    boards["net_return"] = sorted(cards, key=lambda c: c["return_pct"],
                                  reverse=True)[:5]
    boards["win_rate"] = sorted(with_trades,
                                key=lambda c: (c["win_rate"] or 0, c["trades"]),
                                reverse=True)[:5]
    boards["drawdown"] = sorted(with_trades,
                                key=lambda c: c["max_drawdown_pct"],
                                reverse=True)[:5]
    return {"cards": sorted(cards, key=lambda c: c["equity"], reverse=True),
            "leaderboards": boards, "cohort": "live_paper",
            "note": ("Every model starts with $10,000, identical costs and "
                     "conservative fills. No winner is declared without "
                     "sufficient forward sample.")}


@router.get("/chart/bars")
async def chart_bars(symbol: str, tf: str = "5min", request: Request = None,
                     db: AsyncSession = Depends(get_session)):
    """Bars for the Chart Workstation (stocks + entitled crypto)."""
    shared = app_state(request)
    ctx = shared.get("ctx")
    symbol = symbol.upper()
    if tf not in ("5min", "1hour", "daily"):
        raise HTTPException(400, "tf must be 5min|1hour|daily")
    try:
        if tf == "daily":
            data = await ctx.fmp._get("historical-price-eod/full",
                                      {"symbol": symbol, "from": "2024-01-01",
                                       "to": "2027-01-01"}, cache_ttl=1800,
                                      endpoint_name="chart-eod")
            rows = sorted(data or [], key=lambda r: r.get("date") or "")
            bars = [{"time": r["date"], "open": r["open"], "high": r["high"],
                     "low": r["low"], "close": r["close"],
                     "volume": r.get("volume") or 0} for r in rows
                    if r.get("close")]
        else:
            from datetime import date as _date, timedelta as _td
            frm = str(_date.today() - _td(days=10 if tf == "5min" else 60))
            data = await ctx.fmp._get(f"historical-chart/{tf}",
                                      {"symbol": symbol, "from": frm,
                                       "to": str(_date.today()),
                                       "extended": "true"}, cache_ttl=180,
                                      endpoint_name=f"chart-{tf}")
            rows = sorted(data or [], key=lambda r: r.get("date") or "")
            bars = []
            for r in rows:
                try:
                    from zoneinfo import ZoneInfo as _Z
                    ts = datetime.fromisoformat(r["date"]).replace(
                        tzinfo=_Z("America/New_York"))
                    bars.append({"time": int(ts.timestamp()), "open": r["open"],
                                 "high": r["high"], "low": r["low"],
                                 "close": r["close"],
                                 "volume": r.get("volume") or 0})
                except (KeyError, ValueError):
                    continue
        return {"symbol": symbol, "tf": tf, "bars": bars[-1500:],
                "quality": "LIVE" if bars else "UNAVAILABLE"}
    except Exception as e:
        return {"symbol": symbol, "tf": tf, "bars": [],
                "quality": "UNAVAILABLE", "error": type(e).__name__}


@router.get("/outcomes/noon")
async def noon_outcomes(db: AsyncSession = Depends(get_session)):
    """Locked PREMARKET_SCALPER_OUTCOME_V1 card data."""
    rows = (await db.execute(
        select(LockedOutcome, BuySignal.symbol, BuySignal.initiated_at)
        .join(BuySignal, BuySignal.id == LockedOutcome.signal_id)
        .order_by(desc(LockedOutcome.id)).limit(200))).all()
    out, counts = [], {"WIN_10_TOUCH": 0, "WIN_NOON_GREEN": 0,
                       "LOSS_NOON_RED": 0, "FLAT": 0, "INCOMPLETE": 0}
    for lo, sym, init in rows:
        counts[lo.outcome_class] = counts.get(lo.outcome_class, 0) + 1
        out.append({"symbol": sym, "class": lo.outcome_class,
                    "call_price": lo.call_price, "reference": lo.reference_price,
                    "quality": lo.reference_quality,
                    "initiated_at": init.isoformat(),
                    "locked_at": lo.locked_at.isoformat()})
    wins = counts["WIN_10_TOUCH"] + counts["WIN_NOON_GREEN"]
    losses = counts["LOSS_NOON_RED"]
    denom = wins + losses + counts["FLAT"]
    import math as _m
    wr = wins / denom if denom else None
    lb = None
    if denom:
        z, p = 1.96, wr
        den = 1 + z * z / denom
        lb = round(((p + z * z / (2 * denom)) - z * _m.sqrt(
            p * (1 - p) / denom + z * z / (4 * denom * denom))) / den, 4)
    return {"policy": "PREMARKET_SCALPER_OUTCOME_V1", "counts": counts,
            "call_win_rate": round(wr, 4) if wr is not None else None,
            "win_rate_lb": lb, "denominator": denom,
            "note": "INCOMPLETE excluded from denominator; a +10% touch locks a "
                    "WIN even if price later fades. Call accuracy ≠ captured "
                    "profit.",
            "rows": out[:60]}
