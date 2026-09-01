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
from ..models import (AlertRule, EquitySnapshot, JournalEntry, LockedOutcome,
                      MorningBrief, PaperAccount, Watchlist)
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
    pos_map = {p.signal_id: p for p in (await db.execute(
        select(PaperPosition).where(PaperPosition.signal_id.in_(
            [s.id for s in rows] or [0])))).scalars().all()}
    out = []
    for s in rows:
        pos = pos_map.get(s.id)
        setup = ((s.score_snapshot or {}).get("v2") or {}).get("setup") or {}
        sell_plan = {
            "stop": (pos.stop if pos else None) or setup.get("stop"),
            "target1": (pos.target1 if pos else None) or setup.get("target1"),
            "target2": (pos.target2 if pos else None) or setup.get("target2"),
        }
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
            **sell_plan,
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
    backup = {"status": "UNKNOWN"}
    try:
        import glob as _g
        import os as _os
        files = sorted(_g.glob("/app/backups/*.sql.gz"),
                       key=_os.path.getmtime, reverse=True)
        if files:
            age_h = (datetime.now(timezone.utc).timestamp()
                     - _os.path.getmtime(files[0])) / 3600
            backup = {"status": "OK" if age_h < 30 else "STALE",
                      "latest": _os.path.basename(files[0]),
                      "age_hours": round(age_h, 1),
                      "size_mb": round(_os.path.getsize(files[0]) / 1e6, 1),
                      "count": len(files)}
        else:
            backup = {"status": "NONE", "note": "no backups found yet"}
    except Exception:
        pass
    return {
        "env_status": get_config().provider_status(),
        "backup": backup,
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
    accs = {a.model_id: a for a in
            (await db.execute(select(PaperAccount))).scalars().all()}
    cards = []
    for mid, meta in MODELS.items():
        a = accs.get(mid)
        wr = (a.wins / a.trades_closed) if a and a.trades_closed else None
        cards.append({"model_id": mid,
                      "name": meta.get("name", mid),
                      "color": meta.get("color", "#93a1bd"),
                      "experimental": meta.get("experimental", False),
                      "season": a.season if a else 1,
                      "equity": a.equity if a else STARTING_CASH,
                      "cash": a.cash if a else STARTING_CASH,
                      "return_pct": round((a.equity / a.starting_cash - 1) * 100, 2)
                      if a else 0.0,
                      "realized_pnl": a.realized_pnl if a else 0.0,
                      "max_drawdown_pct": a.max_drawdown_pct if a else 0.0,
                      "trades": a.trades_closed if a else 0,
                      "wins": a.wins if a else 0,
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
    snaps = (await db.execute(select(EquitySnapshot)
                              .order_by(desc(EquitySnapshot.id)).limit(1500)
                              )).scalars().all()
    series: Dict[str, list] = {}
    for sn in reversed(snaps):
        series.setdefault(sn.model_id, []).append(round(sn.equity, 2))
    for c in cards:
        c["spark"] = series.get(c["model_id"], [])[-40:]
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


@router.get("/ops")
async def ops(request: Request, db: AsyncSession = Depends(get_session)):
    """One-glance operations view: every lane's live state + what happens next."""
    from ..strategy.registry import MODELS
    from ..util.timeutil import ET, is_trading_day, next_scan_start, now_et
    from datetime import datetime as _dt, timedelta as _td
    shared = app_state(request)
    sched = shared.get("scheduler")
    t = now_et()
    mins = t.hour * 60 + t.minute
    phase = session_phase()
    s = await _get_settings(db)

    def nxt(hh, mm, label):
        target = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
        d = t.date()
        while target <= t or not is_trading_day(d):
            d = d + _td(days=1)
            if is_trading_day(d):
                target = _dt(d.year, d.month, d.day, hh, mm, tzinfo=ET)
                break
            target = _dt(d.year, d.month, d.day, hh, mm, tzinfo=ET)
        return {"event": label, "at_et": target.isoformat()}

    lanes = [
        {"lane": "Premarket Scalper discovery", "state":
            "RUNNING" if phase in ("prep", "premarket") else "SCHEDULED",
         "detail": "scanning movers+news+sweep across the microcap universe"
                   if phase in ("prep", "premarket") else
                   "arms at 4:00 AM ET on trading days"},
        {"lane": "Actionable BUY window", "state":
            "OPEN" if phase == "premarket" and 420 <= mins <= 560 else "CLOSED",
         "detail": "7:00–9:20 AM ET — EARLY WATCH before, EXPIRED after"},
        {"lane": "Model fleet (stocks)", "state":
            "RUNNING" if phase == "regular" else
            ("DAILY MODELS ONLY" if phase == "afterhours" else "SCHEDULED"),
         "detail": f"{sum(1 for m in MODELS.values() if m.get('build'))} models; "
                   f"intraday engines run 9:30–16:00, daily engines after close"},
        {"lane": "Crypto lane", "state": "RUNNING 24/7",
         "detail": "crypto-eligible models on BTC/ETH/SOL/XRP/DOGE/ADA"},
        {"lane": "Position tracking & exits", "state":
            "RUNNING" if phase != "closed" else "IDLE (no session)",
         "detail": "stops, targets, shadow exits, MFE/MAE every cycle"},
        {"lane": "Noon outcome finalizer", "state":
            "DONE TODAY" if mins >= 725 and phase != "closed" else "SCHEDULED",
         "detail": "locks PREMARKET_SCALPER_OUTCOME_V1 at 12:00 ET"},
        {"lane": "Nightly research", "state":
            "RUNNING" if phase == "afterhours" and mins >= 1215 else "SCHEDULED",
         "detail": "after 20:15 ET: replay today, update challengers, "
                   "promotion audit (hold until 100 forward trades)"},
        {"lane": "Regime controller", "state":
            (getattr(sched, "last_regime", {}) or {}).get("state", "n/a").upper(),
         "detail": (getattr(sched, "last_regime", {}) or {}).get("why", "")},
    ]
    upcoming = sorted([
        nxt(4, 0, "Premarket discovery begins"),
        nxt(7, 0, "Broker window opens — EARLY WATCH can convert to BUY"),
        nxt(9, 20, "Last new premarket entry"),
        nxt(9, 30, "Regular session — intraday models activate"),
        nxt(12, 0, "Noon outcomes lock"),
        nxt(15, 55, "Intraday paper positions time-exit"),
        nxt(20, 15, "Nightly research replay"),
    ], key=lambda x: x["at_et"])[:5]
    reg = (getattr(sched, "last_regime", {}) or {}) if sched else {}
    quiet = None
    if phase == "closed":
        quiet = "Market closed — crypto lane keeps running; stocks resume at the next session."
    elif phase == "premarket" and mins < 420:
        quiet = "Premarket before 7:00 — qualifying candidates appear as EARLY WATCH only."
    elif reg.get("state") == "high_risk":
        quiet = f"Regime controller: high-risk ({reg.get('why')}) — models abstain."
    elif phase == "afterhours":
        quiet = "After hours — daily models and crypto only; intraday lanes resume 9:30."
    return {"now_et": t.isoformat(), "phase": phase, "lanes": lanes,
            "upcoming": upcoming,
            "regime_text": REGIME_TEXT.get(reg.get("state", ""), ""),
            "quiet_reason": quiet,
            "not_running": [
                {"what": "Local Mac backend", "why": "droplet owns scanning "
                 "(avoids double API spend); launchd plist kept for fallback"},
                {"what": "Optimized strategy promotion", "why":
                 "research refused honestly (n too small); gate needs 100 "
                 "forward paper trades"},
                {"what": "Research-only methods", "why":
                 "HFT/order-flow/short-vol need data feeds this plan lacks"},
            ]}


REGIME_TEXT = {
    "trend": "Trend day: trend/breakout/momentum models active; mean-reversion abstains.",
    "range": "Range day: mean-reversion and pairs favored; pure trend models abstain.",
    "high_risk": "High-risk conditions: all models abstain until spreads and volatility normalize.",
    "uncertain": "Mixed evidence: most models may trade, sizing stays conservative.",
    "event": "Event-driven tape: catalyst-gated models favored.",
}


@router.get("/digest")
async def digest(request: Request, db: AsyncSession = Depends(get_session)):
    """Today vs previous session in one glance."""
    from ..util.timeutil import is_trading_day, now_et
    from datetime import timedelta as _td
    t = now_et()
    today = str(t.date())
    d = t.date() - _td(days=1)
    while not is_trading_day(d):
        d -= _td(days=1)
    prev = str(d)

    async def day_counts(day):
        sigs = (await db.execute(select(BuySignal).where(
            BuySignal.session_date == day,
            BuySignal.is_demo == False))).scalars().all()  # noqa: E712
        rej = (await db.execute(select(func.count(RejectedCandidate.id)).where(
            RejectedCandidate.session_date == day))).scalar() or 0
        locks = (await db.execute(
            select(LockedOutcome.outcome_class, func.count(LockedOutcome.id))
            .join(BuySignal, BuySignal.id == LockedOutcome.signal_id)
            .where(BuySignal.session_date == day)
            .group_by(LockedOutcome.outcome_class))).all()
        lc = {}
        for s in sigs:
            lc[s.lifecycle or "legacy"] = lc.get(s.lifecycle or "legacy", 0) + 1
        return {"lifecycles": lc, "rejected": rej,
                "locks": {k: v for k, v in locks},
                "buys": lc.get("ACTIONABLE_BUY", 0),
                "early": lc.get("EARLY_WATCH", 0),
                "watch": lc.get("QUALIFIED_WATCH", 0)}

    t_c, p_c = await day_counts(today), await day_counts(prev)
    shared = app_state(request)
    sched = shared.get("scheduler")
    reg = getattr(sched, "last_regime", {}) if sched else {}
    wins_t = t_c["locks"].get("WIN_10_TOUCH", 0) + t_c["locks"].get("WIN_NOON_GREEN", 0)
    line = (f"Today: {t_c['buys']} BUY, {t_c['early']} early, {t_c['watch']} watch, "
            f"{t_c['rejected']} rejected"
            + (f", {wins_t}W/{t_c['locks'].get('LOSS_NOON_RED', 0)}L locked at noon"
               if t_c["locks"] else "")
            + f" — vs {prev}: {p_c['buys']} BUY, {p_c['rejected']} rejected.")
    return {"today": {"date": today, **t_c}, "prev": {"date": prev, **p_c},
            "line": line,
            "regime_text": REGIME_TEXT.get(reg.get("state", ""), "") +
                           (f" ({reg.get('why')})" if reg.get("why") else "")}


@router.get("/brief")
async def brief(db: AsyncSession = Depends(get_session)):
    row = (await db.execute(select(MorningBrief)
                            .order_by(desc(MorningBrief.id)).limit(1)
                            )).scalar_one_or_none()
    if not row:
        return {"available": False,
                "note": "First morning brief writes itself at 9:25 AM ET."}
    return {"available": True, "session_date": row.session_date,
            "kind": row.kind, "created_at": row.created_at.isoformat(),
            "content": row.content}


@router.get("/journal")
async def journal_list(symbol: str = "", limit: int = 100,
                       db: AsyncSession = Depends(get_session)):
    q = select(JournalEntry).order_by(desc(JournalEntry.id)).limit(min(limit, 400))
    if symbol:
        q = q.where(JournalEntry.symbol == symbol.upper())
    rows = (await db.execute(q)).scalars().all()
    return {"rows": [{"id": r.id, "created_at": r.created_at.isoformat(),
                      "symbol": r.symbol, "signal_uid": r.signal_uid,
                      "note": r.note, "tags": r.tags,
                      "rules_followed": r.rules_followed, "review": r.review}
                     for r in rows]}


@router.post("/journal")
async def journal_create(payload: Dict[str, Any],
                         db: AsyncSession = Depends(get_session)):
    note = str(payload.get("note", "")).strip()
    if not note:
        raise HTTPException(400, "note required")
    row = JournalEntry(symbol=str(payload.get("symbol", "")).upper()[:16],
                       signal_uid=str(payload.get("signal_uid", ""))[:40],
                       note=note[:5000],
                       tags=[str(x)[:24] for x in payload.get("tags", [])][:10],
                       rules_followed=bool(payload.get("rules_followed", True)),
                       review=str(payload.get("review", ""))[:5000])
    db.add(row)
    await db.commit()
    return {"ok": True, "id": row.id}


@router.get("/watchlists")
async def watchlists(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(Watchlist))).scalars().all()
    if not rows:
        db.add(Watchlist(name="Default", symbols=[], notes={}))
        await db.commit()
        rows = (await db.execute(select(Watchlist))).scalars().all()
    return {"rows": [{"id": w.id, "name": w.name, "symbols": w.symbols,
                      "notes": w.notes} for w in rows]}


@router.put("/watchlists/{wl_id}")
async def watchlist_update(wl_id: int, payload: Dict[str, Any],
                           db: AsyncSession = Depends(get_session)):
    w = await db.get(Watchlist, wl_id)
    if not w:
        raise HTTPException(404, "watchlist not found")
    if "symbols" in payload:
        w.symbols = [str(s).upper()[:16] for s in payload["symbols"]][:100]
    if "notes" in payload and isinstance(payload["notes"], dict):
        w.notes = {str(k).upper()[:16]: str(v)[:300]
                   for k, v in payload["notes"].items()}
    if "name" in payload:
        w.name = str(payload["name"])[:48]
    await db.commit()
    return {"ok": True}


@router.get("/alerts")
async def alerts_list(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(AlertRule)
                             .order_by(desc(AlertRule.id)).limit(200))).scalars().all()
    return {"rows": [{"id": r.id, "symbol": r.symbol, "condition": r.condition,
                      "price": r.price, "note": r.note, "active": r.active,
                      "fired_at": r.fired_at.isoformat() if r.fired_at else None,
                      "fired_price": r.fired_price} for r in rows]}


@router.post("/alerts")
async def alerts_create(payload: Dict[str, Any],
                        db: AsyncSession = Depends(get_session)):
    try:
        price = float(payload["price"])
        symbol = str(payload["symbol"]).upper()[:16]
        cond = payload.get("condition", "above")
        assert cond in ("above", "below") and symbol and price > 0
    except (KeyError, ValueError, AssertionError):
        raise HTTPException(400, "symbol, condition(above|below), price required")
    db.add(AlertRule(symbol=symbol, condition=cond, price=price,
                     note=str(payload.get("note", ""))[:200]))
    await db.commit()
    return {"ok": True}


@router.delete("/alerts/{alert_id}")
async def alerts_delete(alert_id: int, db: AsyncSession = Depends(get_session)):
    r = await db.get(AlertRule, alert_id)
    if r:
        r.active = False
        await db.commit()
    return {"ok": True}


@router.get("/feed")
async def feed(form: str = "", symbol: str = "", limit: int = 80,
               db: AsyncSession = Depends(get_session)):
    """Unified news + filings stream with source timestamps."""
    nq = select(NewsItem).order_by(desc(NewsItem.published_at)).limit(min(limit, 200))
    fq = select(SecFiling).order_by(desc(SecFiling.accepted_at)).limit(min(limit, 200))
    if symbol:
        nq = nq.where(NewsItem.symbol == symbol.upper())
        fq = fq.where(SecFiling.symbol == symbol.upper())
    if form:
        fq = fq.where(SecFiling.form_type == form)
    news = (await db.execute(nq)).scalars().all()
    filings = (await db.execute(fq)).scalars().all()
    items = ([{"kind": "news", "symbol": n.symbol, "ts": n.published_at.isoformat()
               if n.published_at else None, "title": n.headline,
               "source": n.source, "url": n.url} for n in news] +
             [{"kind": "filing", "symbol": f.symbol,
               "ts": f.accepted_at.isoformat() if f.accepted_at else None,
               "title": f"{f.form_type} — {f.title or 'filing'}",
               "source": "SEC EDGAR", "url": f.primary_doc_url,
               "form": f.form_type} for f in filings])
    items = [i for i in items if i["ts"]]
    items.sort(key=lambda i: i["ts"], reverse=True)
    return {"rows": items[:limit],
            "forms": sorted({f.form_type for f in filings})}


@router.get("/calendar")
async def calendar(request: Request, db: AsyncSession = Depends(get_session)):
    from datetime import date as _date, timedelta as _td
    from ..util.timeutil import half_days, market_holidays
    shared = app_state(request)
    ctx = shared.get("ctx")
    today = _date.today()
    out = {"earnings": [], "holidays": [], "half_days": []}
    try:
        data = await ctx.fmp._get("earnings-calendar",
                                  {"from": str(today),
                                   "to": str(today + _td(days=7))},
                                  cache_ttl=3600, endpoint_name="earnings-cal")
        for r in (data if isinstance(data, list) else [])[:400]:
            out["earnings"].append({"symbol": r.get("symbol"),
                                    "date": r.get("date"),
                                    "eps_est": r.get("epsEstimated"),
                                    "rev_est": r.get("revenueEstimated")})
    except Exception:
        out["earnings_quality"] = "UNAVAILABLE"
    for y in (today.year, today.year + 1):
        out["holidays"] += [str(d) for d in sorted(market_holidays(y))
                            if d >= today]
        out["half_days"] += [str(d) for d in sorted(half_days(y)) if d >= today]
    out["holidays"] = out["holidays"][:8]
    out["half_days"] = out["half_days"][:4]
    return out


@router.get("/chart/analyze")
async def chart_analyze(symbol: str, tf: str = "5min", request: Request = None,
                        db: AsyncSession = Depends(get_session)):
    """Deterministic pattern detection over the same bars the chart renders."""
    from ..strategy.charting import detect
    bars_resp = await chart_bars(symbol, tf, request, db)
    raw = bars_resp["bars"]
    bars = [{"o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"],
             "v": b["volume"], "time": b["time"]} for b in raw]
    det = detect(bars)
    for s in det["signals"]:
        s["time"] = bars[s["i"]]["time"] if 0 <= s["i"] < len(bars) else None
    for t in det["trendlines"]:
        t["t1"] = bars[t["i1"]]["time"] if 0 <= t["i1"] < len(bars) else None
        t["t2"] = bars[t["i2"]]["time"] if 0 <= t["i2"] < len(bars) else None
    for pat in det["patterns"]:
        pat["t1"] = bars[pat["i1"]]["time"] if 0 <= pat["i1"] < len(bars) else None
        pat["t2"] = bars[pat["i2"]]["time"] if 0 <= pat["i2"] < len(bars) else None
        if pat.get("neck_i") is not None and 0 <= pat["neck_i"] < len(bars):
            pat["neck_t"] = bars[pat["neck_i"]]["time"]
        pat["t_end"] = bars[-1]["time"] if bars else None
    return {"symbol": symbol.upper(), "tf": tf, "quality": bars_resp["quality"],
            **det,
            "note": "confirmed-pivot detection — signals use only data available "
                    "at their bar; nothing repaints"}


@router.get("/backtest/reports")
async def backtest_reports(db: AsyncSession = Depends(get_session)):
    """Latest imported report per kind (scalper walk-forward, fleet, nightly)."""
    rows = (await db.execute(select(BtJob).where(BtJob.status == "done")
                             .order_by(desc(BtJob.id)).limit(30))).scalars().all()
    latest: Dict[str, Any] = {}
    for j in rows:
        if j.kind not in latest:
            latest[j.kind] = {"created_at": j.created_at.isoformat(),
                              "config_hash": j.config_hash, "result": j.result}
    return {"kinds": list(latest.keys()), "reports": latest}
