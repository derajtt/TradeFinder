"""Candidate funnel: broad discovery -> universe gates -> shortlist -> enrichment
-> scoring -> BUY transition. Symbol-specific API/AI spend only on the shortlist."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (AiUsage, Candidate, CandidateFeatureSnapshot, Catalyst,
                      NewsItem, ScannerRun, SecFiling, Symbol, SymbolReferenceVersion)
from ..providers.fmp import FmpProvider, looks_common_stock
from ..providers.openai_client import OpenAiProvider
from ..providers.sec import SecProvider
from ..scanner import features as F
from ..scoring.engine import STRATEGY_VERSION, score_candidate, universe_gates
from ..signals import service as sigsvc
from ..util.timeutil import ET, minutes_since_4am, now_et, now_utc

POSITIVE_8K_ITEMS = {"1.01", "2.02", "7.01", "8.01"}  # material agreements, results, reg-FD, other events
DILUTION_8K_ITEMS = {"1.01", "3.02"}                  # can carry offering agreements / unregistered sales


def _content_hash(*parts: str) -> str:
    return hashlib.sha256("||".join(p or "" for p in parts).encode()).hexdigest()


class ScanContext:
    def __init__(self, fmp: FmpProvider, sec: SecProvider, oai: OpenAiProvider):
        self.fmp = fmp
        self.sec = sec
        self.oai = oai
        self.last_cycle: Dict[str, Any] = {}
        self.candidates_live: List[Dict[str, Any]] = []   # latest cycle, served to UI
        self.radar_live: List[Dict[str, Any]] = []         # movers beyond the enriched tier
        self.news_cache_ts: Optional[datetime] = None
        self.news_by_symbol: Dict[str, List[dict]] = {}


async def persist_news_items(db: AsyncSession, items) -> None:
    """Dedup-persist normalized news items (used by global feed and per-symbol fetch)."""
    for item in items:
        sym = item.get("symbol") or ""
        if not sym:
            continue
        h = item.get("content_hash") or _content_hash(sym, item.get("headline", ""),
                                                      item.get("kind", ""))
        item["content_hash"] = h
        exists = (await db.execute(select(NewsItem.id).where(NewsItem.content_hash == h))).first()
        if not exists:
            db.add(NewsItem(symbol=sym, kind=item.get("kind", "news"),
                            published_at=item.get("published_at"),
                            source=(item.get("source") or "")[:128], url=item.get("url", ""),
                            headline=item.get("headline", ""), excerpt=item.get("excerpt", ""),
                            content_hash=h))
    await db.commit()


async def refresh_news(ctx: ScanContext, db: AsyncSession) -> None:
    """Global latest news + press releases (2 calls), grouped by symbol, persisted dedup'd."""
    from ..providers.fmp import EntitlementError
    try:
        news = await ctx.fmp.latest_stock_news(limit=150)
    except EntitlementError:
        news = []
    try:
        prs = await ctx.fmp.latest_press_releases(limit=150)
    except EntitlementError:
        prs = []  # press-release endpoint not in current plan; stock news still flows
    by_symbol: Dict[str, List[dict]] = {}
    cutoff = now_utc() - timedelta(hours=30)  # previous close -> now, generous
    for item in news + prs:
        sym = item.get("symbol") or ""
        if not sym:
            continue
        pub = item.get("published_at")
        if pub and pub < cutoff:
            continue
        h = _content_hash(sym, item.get("headline", ""), item.get("kind", ""))
        item["content_hash"] = h
        by_symbol.setdefault(sym, []).append(item)
        exists = (await db.execute(select(NewsItem.id).where(NewsItem.content_hash == h))).first()
        if not exists:
            db.add(NewsItem(symbol=sym, kind=item["kind"], published_at=pub,
                            source=item.get("source", "")[:128], url=item.get("url", ""),
                            headline=item.get("headline", ""), excerpt=item.get("excerpt", ""),
                            content_hash=h))
    await db.commit()
    ctx.news_by_symbol = by_symbol
    ctx.news_cache_ts = now_utc()


async def _monthly_ai_spend(db: AsyncSession) -> float:
    month = now_utc().strftime("%Y-%m")
    rows = (await db.execute(select(AiUsage).where(AiUsage.month == month))).scalars().all()
    return sum(r.est_cost_usd for r in rows)


async def analyze_catalyst(ctx: ScanContext, db: AsyncSession, symbol: str,
                           news_items: List[dict], filings: List[dict],
                           settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """AI classification with content-hash cache and budget guard. Returns catalyst dict or None."""
    if not news_items and not filings:
        return None
    basis = _content_hash(symbol,
                          "|".join(sorted(i["content_hash"] for i in news_items)),
                          "|".join(sorted(f.get("accession", "") for f in filings)),
                          "analyzer-v3-enums")  # bumped when the analysis contract changes
    cached = (await db.execute(select(Catalyst).where(Catalyst.content_hash == basis))
              ).scalar_one_or_none()
    if cached and cached.status == "ok":
        return _catalyst_dict(cached)
    budget = float(settings.get("openai_monthly_budget_usd") or 25.0)
    if not ctx.oai.configured or await _monthly_ai_spend(db) >= budget:
        return _heuristic_catalyst(symbol, news_items, filings, basis)

    evidence = {"symbol": symbol, "news": [
        {"headline": i["headline"], "source": i.get("source"), "published_at":
         i["published_at"].isoformat() if i.get("published_at") else None,
         "url": i.get("url"), "excerpt": i.get("excerpt", "")[:800], "ref": i["content_hash"][:12]}
        for i in news_items[:8]],
        "sec_filings": [
        {"form": f["form_type"], "items": f.get("items"), "accepted_at":
         f["accepted_at"].isoformat() if f.get("accepted_at") else None,
         "title": f.get("title"), "url": f.get("primary_doc_url"), "ref": f.get("accession")}
        for f in filings[:8]],
    }
    extraction = await ctx.oai.analyze_v2(json.dumps(evidence))
    analysis = _extraction_to_legacy(extraction) if extraction else None
    if analysis is not None:
        analysis["extraction"] = extraction
    if analysis is None:
        row = Catalyst(symbol=symbol, content_hash=basis, status="failed")
        db.add(row)
        await db.commit()
        return None  # BUY prevented until evidence available
    usage = ctx.oai.last_usage or {}
    pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    db.add(AiUsage(month=now_utc().strftime("%Y-%m"), model=ctx.oai.model,
                   prompt_tokens=pt, completion_tokens=ct,
                   est_cost_usd=pt * 0.15 / 1e6 + ct * 0.60 / 1e6))
    src_url = ""
    for i in news_items:
        if i.get("url"):
            src_url = i["url"]
            break
    if not src_url and filings:
        src_url = filings[0].get("primary_doc_url", "")
    dilution_sev = max([r.get("severity", 0) for r in analysis.get("risks", [])
                       if "dilut" in str(r.get("type", "")).lower()] or [0])
    row = Catalyst(symbol=symbol, content_hash=basis,
                   direction=analysis["direction"], materiality=analysis["materiality"],
                   novelty=analysis["novelty"], confidence=analysis["confidence"],
                   catalyst_type=analysis.get("catalyst_type", "")[:64],
                   dilution_detected=analysis["dilution_detected"],
                   going_concern_detected=analysis["going_concern_detected"],
                   summary=analysis.get("plain_english_summary", ""),
                   analysis={**analysis, "source_url": src_url, "dilution_severity": dilution_sev},
                   status="ok")
    db.add(row)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        cached = (await db.execute(select(Catalyst).where(Catalyst.content_hash == basis))
                  ).scalar_one_or_none()
        return _catalyst_dict(cached) if cached else None
    return _catalyst_dict(row)


MAT_ENUM_TO_100 = {"transformative": 90, "major": 70, "moderate": 45,
                   "minor": 20, "immaterial": 5}
NEG_CATS = {"rumor_promo", "reverse_split", "dilution_negative", "other_negative"}


def _extraction_to_legacy(e: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic enum->legacy mapping (display/scoring-v1 compatibility).
    The 1-3-scale bug is structurally impossible: enums only."""
    from ..strategy.catalyst import grade_of
    g = grade_of(e)
    direction = ("positive" if g in ("A", "B")
                 else "negative" if e["category"] in NEG_CATS
                 else "neutral")
    novelty = ("recycled" if e["freshness"] == "recycled" or not e.get("novel", True)
               else "new" if e["freshness"] in ("breaking", "today", "overnight")
               else "update")
    return {
        "direction": direction,
        "materiality": MAT_ENUM_TO_100[e["materiality"]],
        "novelty": novelty,
        "confidence": e["confidence"],
        "catalyst_type": e["category"],
        "facts": [{"label": "counterparty", "value": c, "source_ref": ""}
                  for c in e.get("counterparties", [])][:8] +
                 ([{"label": "value", "value": e["quantified_value"],
                    "source_ref": ""}] if e.get("quantified_value") else []),
        "risks": [{"type": t, "severity": 50, "source_ref": ""}
                  for t in e.get("negative_terms", [])][:8],
        "dilution_detected": bool(e.get("dilution_detected")),
        "going_concern_detected": bool(e.get("going_concern_detected")),
        "plain_english_summary": e.get("evidence_summary", ""),
    }


def _catalyst_dict(row: Catalyst) -> Dict[str, Any]:
    a = row.analysis or {}
    return {"direction": row.direction, "materiality": row.materiality,
            "novelty": row.novelty, "confidence": row.confidence,
            "catalyst_type": row.catalyst_type,
            "dilution_detected": row.dilution_detected,
            "dilution_severity": a.get("dilution_severity", 0),
            "going_concern_detected": row.going_concern_detected,
            "summary": row.summary, "source_url": a.get("source_url", ""),
            "has_original_source": bool(a.get("source_url")),
            "content_hash": row.content_hash,
            "facts": a.get("facts", []), "risks": a.get("risks", []),
            "extraction": a.get("extraction"),
            "ai": row.status == "ok" and bool(a.get("plain_english_summary"))}


def _heuristic_catalyst(symbol: str, news_items: List[dict], filings: List[dict],
                        basis: str) -> Optional[Dict[str, Any]]:
    """Deterministic keyword fallback when AI unavailable/over budget. Clearly labeled;
    low confidence so it can inform the score but rarely satisfies the catalyst gate alone."""
    KEYS_POS = ("fda approval", "clearance", "contract", "partnership", "acquisition",
                "merger", "purchase order", "beats", "raises guidance", "breakthrough",
                "phase 3", "phase 2 met", "topline results", "uplist")
    KEYS_NEG = ("offering", "dilution", "reverse split", "going concern", "delisting",
                "investigation", "lawsuit", "misses", "bankruptcy", "chapter 11")
    text = " ".join((i.get("headline", "") + " " + i.get("excerpt", "")).lower()
                    for i in news_items)
    pos = sum(1 for k in KEYS_POS if k in text)
    neg = sum(1 for k in KEYS_NEG if k in text)
    if not news_items:
        return None
    direction = "positive" if pos > neg and pos > 0 else ("negative" if neg > pos else "neutral")
    src = next((i["url"] for i in news_items if i.get("url")), "")
    return {"direction": direction, "materiality": min(60, 25 * pos) if direction == "positive" else 20,
            "novelty": "new", "confidence": 0.35, "catalyst_type": "keyword_heuristic",
            "dilution_detected": "offering" in text or "dilution" in text,
            "dilution_severity": 50 if "offering" in text else 0,
            "going_concern_detected": "going concern" in text,
            "summary": "Heuristic (non-AI) keyword classification — low confidence.",
            "source_url": src, "has_original_source": bool(src),
            "content_hash": basis, "facts": [], "risks": [], "ai": False}


def filing_context_from(filings: List[dict]) -> Dict[str, Any]:
    ctx = {"positive_8k": False, "clean_context": True, "insider_buying": False,
           "active_dilution": False, "dilution_severity": 0, "recent_forms": []}
    for f in filings:
        ctx["recent_forms"].append(f["form_type"])
        items = set(x.strip() for x in (f.get("items") or "").split(",") if x.strip())
        if f["form_type"].startswith("8-K") and items & POSITIVE_8K_ITEMS:
            ctx["positive_8k"] = True
        if f.get("is_dilution_form"):
            ctx["active_dilution"] = True
            ctx["dilution_severity"] = max(ctx["dilution_severity"], 60)
            ctx["clean_context"] = False
        if f["form_type"].startswith("8-K") and items & DILUTION_8K_ITEMS:
            ctx["dilution_severity"] = max(ctx["dilution_severity"], 40)
        if f["form_type"] in ("NT 10-Q", "NT 10-K", "25", "25-NSE"):
            ctx["clean_context"] = False
        if f["form_type"] == "4":
            ctx["insider_buying"] = True  # presence of Form 4; direction unknown without doc parse
    return ctx


async def persist_reference(db: AsyncSession, symbol: str, profile: Optional[dict],
                            float_data: Optional[dict]) -> None:
    sym = (await db.execute(select(Symbol).where(Symbol.symbol == symbol))).scalar_one_or_none()
    if sym is None:
        sym = Symbol(symbol=symbol)
        db.add(sym)
    if profile:
        sym.name = profile.get("name") or sym.name
        sym.exchange = profile.get("exchange") or sym.exchange
        sym.cik = profile.get("cik") or sym.cik
    db.add(SymbolReferenceVersion(
        symbol=symbol,
        market_cap=(profile or {}).get("market_cap"),
        float_shares=(float_data or {}).get("float_shares"),
        shares_outstanding=(float_data or {}).get("shares_outstanding"),
        avg_volume=(profile or {}).get("avg_volume"),
        sector=(profile or {}).get("sector", "")[:128],
        industry=(profile or {}).get("industry", "")[:128],
        country=(profile or {}).get("country", "")[:64],
        payload={"profile": {**{k: v for k, v in (profile or {}).items()
                              if k != "description"},
                              "description": ((profile or {}).get("description") or "")[:700]},
                 "float": float_data or {}}))
    await db.flush()


def _pm_windows(bars: List[dict], session_date_et) -> List[dict]:
    """Bars belonging to the premarket (04:00–09:30 ET) of a given ET date."""
    out = []
    for b in bars:
        ts_et = b["ts_utc"].astimezone(ET)
        if ts_et.date() != session_date_et:
            continue
        mins = ts_et.hour * 60 + ts_et.minute
        if 240 <= mins < 570:
            out.append({**b, "minute_of_day": mins})
    return out


def compute_market_features(quote: dict, today_pm: List[dict], baselines: List[float],
                            amq: Optional[dict], settings: Dict[str, Any], now_et_dt,
                            avg_daily_volume: Optional[float] = None,
                            bar_source: str = "derived") -> Dict[str, Any]:
    """Deterministic market features. today_pm: today's premarket bars (minute_of_day
    annotated) from the provider or our own accumulated history. baselines: prior
    sessions' cumulative premarket volume through the current minute."""
    from . import bars as barmod
    cur_minute = now_et_dt.hour * 60 + now_et_dt.minute
    today_pm = [b for b in today_pm if b.get("minute_of_day", 0) <= cur_minute]
    pm_volume = F.cumulative_volume(today_pm)
    pm_dollar = F.dollar_volume(today_pm)
    rv = F.premarket_rvol(pm_volume, baselines)
    rvol_estimated = False
    if rv["rvol"] is None and pm_volume > 0:
        est = barmod.estimated_rvol(pm_volume, avg_daily_volume, cur_minute)
        if est is not None:
            rv = {"rvol": est, "baseline_median": None,
                  "coverage": rv["coverage"], "confidence": 0.35}
            rvol_estimated = True

    # acceleration: last 5 minutes vs prior premarket 5-minute windows today
    last5 = [b for b in today_pm if b["minute_of_day"] > cur_minute - 5]
    prior5: List[float] = []
    if today_pm:
        start_min = min(b["minute_of_day"] for b in today_pm)
        w = start_min
        while w + 5 <= cur_minute - 5:
            prior5.append(sum(b["volume"] or 0 for b in today_pm
                              if w <= b["minute_of_day"] < w + 5))
            w += 5
    accel = F.volume_acceleration(F.cumulative_volume(last5), [v for v in prior5 if v > 0])

    vol_bars = sum(1 for b in today_pm if (b.get("volume") or 0) > 0)
    vw = F.vwap(today_pm) if vol_bars >= 3 else None  # partial VWAP must not gate BUY
    struct = F.structure_features(today_pm)
    price = quote.get("price")
    price_indicative = False
    spread = F.spread_pct((amq or {}).get("bid"), (amq or {}).get("ask"))
    trade_ts = quote.get("provider_ts")
    book_ts = (amq or {}).get("provider_ts")
    max_age = settings.get("quote_freshness_sec") or 120
    # Trade freshness comes ONLY from the trade print's own timestamp. Never infer
    # it from our derived bars — those update every cycle from the (possibly
    # indicative) book and would mark stale trade prices as fresh.
    fresh = trade_ts is not None and (now_utc() - trade_ts).total_seconds() <= max_age
    book_fresh = book_ts is not None and (now_utc() - book_ts).total_seconds() <= max_age
    if not fresh and book_fresh:
        bid, ask = (amq or {}).get("bid"), (amq or {}).get("ask")
        if bid and ask and ask >= bid > 0:
            mid = (bid + ask) / 2.0
            book_spread = (ask - bid) / mid * 100.0
            # Only trust the mid when the book is reasonably tight — a wide or
            # one-sided 4 AM book produces meaningless mids. Otherwise keep the
            # last real print (shown stale). BUY still requires a fresh trade.
            if book_spread <= 15.0:
                price = mid
                price_indicative = True
    provider_ts = trade_ts if fresh else (book_ts or trade_ts)

    in_premarket = 240 <= cur_minute < 570
    # incomplete when premarket and no reported extended-hours volume has been observed
    volume_incomplete = in_premarket and pm_volume <= 0

    return {
        "price": price,
        "price_indicative": price_indicative,
        "previous_close": quote.get("previous_close"),
        "gap_pct": F.gap_pct(price, quote.get("previous_close")),
        "pm_volume": pm_volume if today_pm else None,
        "pm_dollar_volume": pm_dollar if today_pm else None,
        "pm_volume_source": f"{bar_source} (observed since first poll)",
        "rvol": rv["rvol"], "rvol_coverage": rv["coverage"], "rvol_confidence": rv["confidence"],
        "rvol_estimated": rvol_estimated,
        "rvol_baseline_median": rv["baseline_median"],
        "volume_acceleration": accel,
        "vwap": vw,
        "above_vwap": (price > vw) if (price is not None and vw is not None) else None,
        "pm_high": struct["pm_high"], "pm_low": struct["pm_low"],
        "dist_from_high_pct": struct["dist_from_high_pct"], "hh_hl": struct["hh_hl"],
        "spread_pct": spread,
        "bid": (amq or {}).get("bid"), "ask": (amq or {}).get("ask"),
        "quote_fresh": fresh,
        "provider_ts": provider_ts.isoformat() if provider_ts else None,
        "volume_incomplete": volume_incomplete,
        "halted": False,
        "data_disagreement": False,
        "day_high": quote.get("day_high"), "day_low": quote.get("day_low"),
        "quote_volume": quote.get("volume"),
        "bar_count_today_pm": len(today_pm),
    }
