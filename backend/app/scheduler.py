"""Persistent asyncio scheduler with ET market awareness.

Windows (America/New_York, DST-safe via zoneinfo):
  03:45–04:00 prep: health checks, calendar check, cache warmup
  04:00–09:30 premarket: discovery + enrichment + BUY transitions
  09:30–16:00 regular: track signals, update stats
  16:00–20:00 afterhours: finalize day stats, light tracking
Discovery failures never touch existing signals; tracking and discovery fail independently."""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from .db import SessionLocal
from .models import (AlertRule, BuySignal, Candidate, CandidateFeatureSnapshot,
                     EquitySnapshot, HealthEvent, LockedOutcome, MorningBrief,
                     PaperAccount, PaperPosition, ProviderRequest,
                     RejectedCandidate, ScannerRun, SecFiling)
from .providers.fmp import looks_common_stock
from .providers.fmp import EntitlementError
from .scanner import bars as barmod
from .scanner import funnel
from .scoring.engine import STRATEGY_VERSION, score_candidate, universe_gates
from .strategy import paper as paper_engine
from .strategy import platform as mplat
from .strategy.engines import ENGINES, REGIME_ALLOW
from .strategy.engines import regime as regime_fn
from .strategy.profiles import get_profiles, profile_settings
from .strategy.registry import (CRYPTO_UNIVERSE, ETF_UNIVERSE, MODELS, PAIRS)
from .strategy.dilution import assess as assess_dilution
from .strategy.gates import evaluate as evaluate_v2
from .strategy.tiers import tier_for
from .strategy.versions import VERSIONS
from .settings_service import ensure_strategy_version, get_settings
from .signals import service as sigsvc
from .sse import broadcaster
from .util.timeutil import (ET, is_trading_day, next_scan_start, now_et, now_utc,
                            session_phase)

log = logging.getLogger("scheduler")

ALLOWED_EXCH = {"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "NYSEAMERICAN", "NYSE MKT"}


class Scheduler:
    def __init__(self, ctx: funnel.ScanContext, scan_enabled: bool):
        self.ctx = ctx
        self.scan_enabled = scan_enabled
        self.acc = barmod.Accumulator()
        self._split_cache: Dict[str, bool] = {}
        self._last_shortlist: List[str] = []
        self._pool_seen: Dict[str, float] = {}
        self._universe: List[str] = []
        self._universe_meta: Dict[str, dict] = {}
        self._universe_ts: float = 0.0
        self._sweep_idx: int = 0
        self._news_quoted: Dict[str, float] = {}
        self._first_seen: Dict[str, str] = {}   # f"{sym}:{date}" -> iso ts
        self.mctx = None            # shared multi-model market context
        self._daily_models_ran: str = ""
        self.last_regime: Dict[str, Any] = {"state": "uncertain",
                                            "why": "not yet evaluated"}
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.state: Dict[str, Any] = {"phase": "idle", "last_cycle_at": None,
                                      "last_cycle_ok": None, "next_run_at": None,
                                      "cycles": 0, "last_error": "",
                                      "candidates": 0, "demo_mode": False}

    def start(self):
        self.running = True
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass

    async def _flush_provider_logs(self):
        rows = []
        for client in (self.ctx.fmp.http, self.ctx.sec.http):
            rows.extend(client.request_log)
            client.request_log = []
        if not rows:
            return
        async with SessionLocal() as db:
            for r in rows[-200:]:
                db.add(ProviderRequest(**r))
            await db.commit()

    async def _health(self, level: str, component: str, message: str):
        try:
            async with SessionLocal() as db:
                db.add(HealthEvent(level=level, component=component, message=message[:2000]))
                await db.commit()
        except Exception:
            pass

    async def _loop(self):
        await asyncio.sleep(2)
        async with SessionLocal() as db:
            settings = await get_settings(db)
            await ensure_strategy_version(db, settings)
        while self.running:
            try:
                async with SessionLocal() as db:
                    settings = await get_settings(db)
                phase = session_phase()
                self.state["phase"] = phase
                paused = bool(settings.get("paused")) or not self.scan_enabled
                interval = max(20, int(settings.get("scan_interval_sec") or 60))
                if paused:
                    self.state["next_run_at"] = None
                    await broadcaster.publish("scanner", {"phase": phase, "paused": True})
                    await asyncio.sleep(10)
                    continue
                if phase in ("prep", "premarket"):
                    await self._discovery_cycle(settings, phase)
                    await self._tracking_cycle(settings, finalize=False)
                elif phase == "regular":
                    await self._tracking_cycle(settings, finalize=False)
                    if self.state["cycles"] % 5 == 0:
                        await self._discovery_cycle(settings, phase)  # keep candidate table fresh
                    if self.state["cycles"] % 3 == 0:
                        await self._models_cycle(settings, phase)
                elif phase == "afterhours":
                    await self._tracking_cycle(settings, finalize=True)
                    if self.state["cycles"] % 10 == 0:
                        await self._models_cycle(settings, phase)  # crypto lane
                    await self._nightly_research(settings)
                    interval = max(interval, 300)
                elif self.state["cycles"] % 10 == 0 and phase == "closed":
                    await self._models_cycle(settings, phase)      # crypto is 24/7
                    nxt = next_scan_start()
                    self.state["next_run_at"] = nxt.isoformat()
                elif True:
                    nxt = next_scan_start()
                    self.state["next_run_at"] = nxt.isoformat()
                    await broadcaster.publish("scanner", {"phase": "closed",
                                                          "next_run_at": nxt.isoformat()})
                    interval = 60
                self.state["cycles"] += 1
                self.state["last_cycle_at"] = now_utc().isoformat()
                await self._flush_provider_logs()
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.state["last_error"] = f"{type(e).__name__}: {e}"
                self.state["last_cycle_ok"] = False
                log.error("cycle error: %s\n%s", e, traceback.format_exc(limit=4))
                await self._health("error", "scheduler", self.state["last_error"])
            await asyncio.sleep(interval)

    # ---------------- discovery ----------------
    async def _discovery_cycle(self, settings: Dict[str, Any], phase: str):
        ctx = self.ctx
        t_now_et = now_et()
        session_date = str(t_now_et.date())
        async with SessionLocal() as db:
            run = ScannerRun(phase=phase)
            db.add(run)
            await db.commit()
            run_id = run.id
        api_calls_before = len(ctx.fmp.http.request_log)
        try:
            gainers = await ctx.fmp.biggest_gainers()
            actives = await ctx.fmp.most_actives()
            pool: Dict[str, dict] = {}
            for row in gainers + actives:
                sym = row["symbol"]
                if sym and looks_common_stock(sym, row.get("name", "")):
                    pool.setdefault(sym, row)
            # widen with exchange batches every 10 cycles, only if the plan allows it
            if self.state["cycles"] % 10 == 0:
                for exch in ("NASDAQ", "NYSE", "AMEX"):
                    try:
                        for q in await ctx.fmp.exchange_quotes(exch):
                            sym = q["symbol"]
                            if not sym or not looks_common_stock(sym, q.get("name", "")):
                                continue
                            gp = None
                            if q.get("price") and q.get("previous_close"):
                                gp = (q["price"] - q["previous_close"]) / q["previous_close"] * 100
                            elif q.get("change_pct") is not None:
                                gp = q["change_pct"]
                            if gp is not None and gp >= 5 and (q.get("price") or 0) > 0:
                                pool.setdefault(sym, q)
                    except EntitlementError:
                        break  # plan does not include batch quotes; movers-only discovery
                    except Exception as e:
                        await self._health("warn", "fmp", f"exchange sweep {exch} failed: {e}")
            # full-exchange universe from the screener (refreshed twice a day)
            import time as _tt
            if not self._universe or _tt.monotonic() - self._universe_ts > 12 * 3600:
                try:
                    uni = await ctx.fmp.screener_universe(
                        price_min=settings.get("price_min"),
                        price_max=settings.get("price_max"),
                        mcap_min=settings.get("market_cap_min"),
                        mcap_max=settings.get("market_cap_max"))
                    keep = [u for u in uni if looks_common_stock(u["symbol"], u["name"])]
                    if keep:
                        self._universe = [u["symbol"] for u in keep]
                        self._universe_meta = {u["symbol"]: u for u in keep}
                        self._universe_ts = _tt.monotonic()
                        await self._health("info", "universe",
                                           f"screener universe: {len(keep)} symbols")
                except EntitlementError:
                    pass
                except Exception as e:
                    await self._health("warn", "universe", f"screener failed: {e}")
            universe_size = max(len(pool), len(self._universe))

            # cheap price prefilter using mover-list prices before spending quote calls
            pmin = settings.get("price_min") or 0
            pmax = settings.get("price_max")
            pre = [s for s, r in pool.items()
                   if r.get("price") is None or
                   ((r["price"] >= pmin) and (pmax is None or r["price"] <= pmax))]
            # order by absolute move so quote spend goes to the strongest movers first
            pre.sort(key=lambda s: abs(pool[s].get("change_pct") or 0), reverse=True)
            # API-spend control: full 45-symbol sweep every 5th cycle; between sweeps,
            # re-quote only the working shortlist plus pool entrants not seen recently.
            import time as _t
            now_mono = _t.monotonic()
            if self.state["cycles"] % 5 == 0:
                quote_syms = pre[:45]
            else:
                fresh_entrants = [s for s in pre if now_mono - self._pool_seen.get(s, 0) > 600]
                quote_syms = list(dict.fromkeys(self._last_shortlist + fresh_entrants))[:45]
            uni_set = set(self._universe)
            # news-driven adds: universe symbols with fresh news get quoted immediately
            news_adds = []
            for s_ in ctx.news_by_symbol.keys():
                if len(news_adds) >= 20:
                    break
                if s_ in uni_set and s_ not in quote_syms and                         now_mono - self._news_quoted.get(s_, 0) > 600:
                    news_adds.append(s_)
                    self._news_quoted[s_] = now_mono
            # rotating sweep across the whole exchange universe
            sweep_n = int(settings.get("universe_sweep_per_cycle") or 0)
            sweep_adds = []
            if sweep_n > 0 and self._universe:
                taken = 0
                start = self._sweep_idx
                while taken < sweep_n and taken < len(self._universe):
                    s_ = self._universe[self._sweep_idx % len(self._universe)]
                    self._sweep_idx += 1
                    if s_ not in quote_syms and s_ not in news_adds:
                        sweep_adds.append(s_)
                        taken += 1
                    if self._sweep_idx - start > len(self._universe):
                        break
            quote_syms = list(dict.fromkeys(list(quote_syms) + news_adds + sweep_adds))
            for s_ in quote_syms:
                self._pool_seen[s_] = now_mono
            quotes = {q["symbol"]: q for q in await ctx.fmp.quotes(quote_syms)}
            # swept/news symbols join the pool when they are actually moving
            for s_, q in quotes.items():
                if s_ in pool:
                    continue
                gp = None
                if q.get("price") and q.get("previous_close"):
                    gp = (q["price"] - q["previous_close"]) / q["previous_close"] * 100
                has_news = s_ in ctx.news_by_symbol
                if gp is not None and (gp >= 3 or (has_news and abs(gp) >= 1)):
                    meta = self._universe_meta.get(s_, {})
                    pool[s_] = {"symbol": s_, "name": q.get("name") or meta.get("name", ""),
                                "exchange": q.get("exchange") or meta.get("exchange", ""),
                                "price": q.get("price"), "change_pct": gp}
            if phase == "regular":
                # regular hours: quote volume is the true day counter -> derive bars
                async with SessionLocal() as db:
                    for sym, q in quotes.items():
                        try:
                            await self.acc.record(db, sym, q.get("price"), q.get("volume"),
                                                  q.get("provider_ts"))
                        except Exception:
                            pass
                    await db.commit()
            scored_pool = []
            for sym in list(dict.fromkeys(list(pre) + [s_ for s_ in quotes if s_ in pool])):
                q = quotes.get(sym)
                if not q or not q.get("price"):
                    continue
                if q.get("exchange") and q["exchange"] not in ALLOWED_EXCH and not settings.get("include_otc"):
                    continue
                if not (pmin <= q["price"] <= (pmax if pmax is not None else 1e12)):
                    continue
                gp = None
                if q.get("previous_close"):
                    gp = (q["price"] - q["previous_close"]) / q["previous_close"] * 100
                heur = (gp or 0) * ((q.get("volume") or 0) ** 0.5)
                scored_pool.append((heur, sym, q))
            scored_pool.sort(reverse=True, key=lambda x: x[0])
            top_n = int(settings.get("enrich_top_n") or 20)
            shortlist = scored_pool[:top_n]

            if ctx.news_cache_ts is None or (now_utc() - ctx.news_cache_ts).total_seconds() > 240:
                async with SessionLocal() as db:
                    await funnel.refresh_news(ctx, db)

            live_rows: List[Dict[str, Any]] = []
            enriched = 0
            for _, sym, q in shortlist:
                try:
                    row = await self._enrich_and_score(sym, q, settings, session_date, run_id)
                    if row:
                        live_rows.append(row)
                        enriched += 1
                except Exception as e:
                    await self._health("warn", "enrich", f"{sym}: {type(e).__name__}: {e}")
            live_rows.sort(key=lambda r: r["score"], reverse=True)
            self._last_shortlist = [r["symbol"] for r in live_rows]
            ctx.candidates_live = live_rows
            # radar tier: every pool mover NOT in the enriched shortlist — full visibility
            enriched_syms = {r["symbol"] for r in live_rows}
            radar = []
            for s_, meta in pool.items():
                if s_ in enriched_syms:
                    continue
                q = quotes.get(s_) or {}
                gp = meta.get("change_pct")
                if q.get("price") and q.get("previous_close"):
                    gp = (q["price"] - q["previous_close"]) / q["previous_close"] * 100
                radar.append({
                    "symbol": s_, "name": meta.get("name") or q.get("name", ""),
                    "exchange": meta.get("exchange") or q.get("exchange", ""),
                    "price": q.get("price") or meta.get("price"),
                    "gap_pct": gp,
                    "volume": q.get("volume"),
                    "market_cap": q.get("market_cap") or
                                  self._universe_meta.get(s_, {}).get("market_cap"),
                    "has_news": s_ in ctx.news_by_symbol,
                    "provider_ts": (q.get("provider_ts").isoformat()
                                    if q.get("provider_ts") else None),
                })
            radar.sort(key=lambda r: abs(r.get("gap_pct") or 0), reverse=True)
            ctx.radar_live = radar[:150]
            self.state["candidates"] = len(live_rows)
            self.state["last_cycle_ok"] = True
            async with SessionLocal() as db:
                run = await db.get(ScannerRun, run_id)
                if run:
                    run.finished_at = now_utc()
                    run.status = "ok"
                    run.universe_size = universe_size
                    run.shortlisted = len(shortlist)
                    run.enriched = enriched
                    run.api_calls = len(ctx.fmp.http.request_log) - api_calls_before
                    await db.commit()
            await broadcaster.publish("candidates", {"rows": live_rows[:60],
                                                     "radar": ctx.radar_live[:80],
                                                     "phase": phase,
                                                     "ts": now_utc().isoformat()})
        except Exception as e:
            async with SessionLocal() as db:
                run = await db.get(ScannerRun, run_id)
                if run:
                    run.finished_at = now_utc()
                    run.status = "error"
                    run.error = f"{type(e).__name__}: {e}"[:2000]
                    await db.commit()
            raise

    async def _enrich_and_score(self, sym: str, quote: dict, settings: Dict[str, Any],
                                session_date: str, run_id: int) -> Optional[Dict[str, Any]]:
        ctx = self.ctx
        profile = await ctx.fmp.profile(sym)
        if profile and (profile.get("is_etf") or profile.get("is_fund")
                        or profile.get("is_actively_trading") is False):
            return None
        float_data = await ctx.fmp.shares_float(sym)
        t_et = now_et()
        session_dt = str(t_et.date())
        cur_minute = t_et.hour * 60 + t_et.minute
        # bars: provider 1-min history when entitled, else our accumulated history
        today_pm: List[dict] = []
        baselines: List[float] = []
        bar_source = "derived"
        try:
            raw = await ctx.fmp.minute_bars(sym, str((t_et - timedelta(days=21)).date()),
                                            session_dt)
            if raw:
                bar_source = "fmp_1min"
                from ..util.timeutil import ET as _ET
                by_date = {}
                for b in raw:
                    ts_et = b["ts_utc"].astimezone(_ET)
                    m = ts_et.hour * 60 + ts_et.minute
                    if 240 <= m < 570:
                        by_date.setdefault(str(ts_et.date()), []).append(
                            {**b, "minute_of_day": m})
                today_pm = by_date.get(session_dt, [])
                baselines = [sum(x["volume"] or 0 for x in v if x["minute_of_day"] <= cur_minute)
                             for d, v in sorted(by_date.items(), reverse=True)
                             if d != session_dt][:10]
        except EntitlementError:
            pass
        if bar_source == "derived":
            async with SessionLocal() as db:
                today_pm = await barmod.today_pm_bars(db, sym, session_dt)
                baselines = await barmod.baseline_pm_cum_volumes(db, sym, session_dt,
                                                                 cur_minute)
        amq = await ctx.fmp.aftermarket_quote(sym) if session_phase() in ("premarket", "afterhours") else None
        if session_phase() == "premarket":
            # /quote trade prints lag for small caps premarket; the aftermarket-trade
            # endpoint carries the real extended-hours tape. Use whichever is fresher.
            amt = await ctx.fmp.aftermarket_trade(sym)
            if amt and amt.get("price") and amt.get("provider_ts"):
                q_ts0 = quote.get("provider_ts")
                if q_ts0 is None or amt["provider_ts"] > q_ts0:
                    quote = {**quote, "price": amt["price"],
                             "provider_ts": amt["provider_ts"]}
        if amq is not None and session_phase() == "premarket":
            # extended-hours volume counter (resets each session) is the honest PM source
            amq_ts = amq.get("provider_ts")
            q_ts = quote.get("provider_ts")
            fresh_trade = q_ts is not None and (now_utc() - q_ts).total_seconds() <= 180
            obs_price = quote.get("price") if fresh_trade else None
            if obs_price is None and amq.get("bid") and amq.get("ask") and amq["ask"] >= amq["bid"] > 0:
                _mid = (amq["bid"] + amq["ask"]) / 2.0
                if (amq["ask"] - amq["bid"]) / _mid * 100.0 <= 15.0:
                    obs_price = _mid  # tight-book indicative mid; BUY still needs a fresh trade
            async with SessionLocal() as db:
                try:
                    await self.acc.record(db, sym, obs_price, amq.get("volume"), amq_ts)
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    await self._health("warn", "bars", f"{sym}: {type(e).__name__}: {e}")
            async with SessionLocal() as db:
                today_pm = await barmod.today_pm_bars(db, sym, session_dt)
                baselines = await barmod.baseline_pm_cum_volumes(db, sym, session_dt,
                                                                 cur_minute)
        feats = funnel.compute_market_features(
            quote, today_pm, baselines, amq, settings, t_et,
            avg_daily_volume=(profile or {}).get("avg_volume") or quote.get("avg_volume"),
            bar_source=bar_source)
        feats["market_cap"] = (profile or {}).get("market_cap") or quote.get("market_cap")
        feats["float_shares"] = (float_data or {}).get("float_shares")
        feats["float_rotation"] = ((feats.get("pm_volume") or 0) / feats["float_shares"]
                                   if feats.get("float_shares") and feats.get("pm_volume")
                                   else None)
        feats["shares_outstanding"] = (float_data or {}).get("shares_outstanding")
        feats["has_revenue"] = bool((profile or {}).get("sector"))
        feats["recent_reverse_split"] = await self._had_recent_reverse_split(sym)

        gates_failed = universe_gates(feats, settings)
        # SEC filings, news matching and reference snapshots for every enriched candidate
        # (SEC is free+cached; AI is content-hash cached and budget-capped).
        filings: List[dict] = []
        catalyst = None
        try:
            filings = await ctx.sec.recent_filings(sym, days=7)
        except Exception:
            filings = []
        news_items = list(ctx.news_by_symbol.get(sym, []))
        # multiple catalysts: per-symbol news query joins the global feed + SEC filings
        try:
            per_sym = await ctx.fmp.stock_news_for(sym)
        except Exception:
            per_sym = []
        cutoff = now_utc() - timedelta(hours=30)
        seen_h = {n.get("content_hash") for n in news_items}
        for item in per_sym:
            if item.get("published_at") and item["published_at"] < cutoff:
                continue
            h = funnel._content_hash(sym, item.get("headline", ""), item.get("kind", ""))
            item["content_hash"] = h
            if h not in seen_h:
                news_items.append(item)
                seen_h.add(h)
        news_items.sort(key=lambda n: n.get("published_at") or cutoff, reverse=True)
        async with SessionLocal() as db:
            await funnel.persist_news_items(db, news_items)
            if news_items or filings:
                catalyst = await funnel.analyze_catalyst(ctx, db, sym, news_items, filings, settings)
            for f in filings:
                exists = (await db.execute(
                    select(SecFiling.id).where(SecFiling.accession == f["accession"],
                                               SecFiling.symbol == sym))).first()
                if not exists:
                    db.add(SecFiling(symbol=sym, cik=f["cik"], accession=f["accession"],
                                     form_type=f["form_type"], filed_at=f["filed_at"],
                                     accepted_at=f["accepted_at"], items=f.get("items", ""),
                                     title=f.get("title", "")[:500],
                                     primary_doc_url=f.get("primary_doc_url", "")))
            await db.commit()
            await funnel.persist_reference(db, sym, profile, float_data)
            await db.commit()

        feats["catalyst"] = catalyst
        feats["filing_context"] = funnel.filing_context_from(filings)
        feats["session_phase"] = session_phase()
        feats["et_minutes"] = t_et.hour * 60 + t_et.minute
        result = score_candidate(feats, settings)
        # ── v2 decision engine (lifecycle, hard gates, executable pricing) ──
        feats["bid"] = (amq or {}).get("bid")
        feats["ask"] = (amq or {}).get("ask")
        feats["bid_size"] = (amq or {}).get("bid_size")
        feats["ask_size"] = (amq or {}).get("ask_size")
        extraction = (catalyst or {}).get("extraction")
        dilution = assess_dilution(filings, extraction,
                                   feats.get("recent_reverse_split", False))
        fs_key = f"{sym}:{session_date}"
        if fs_key not in self._first_seen:
            self._first_seen[fs_key] = now_utc().isoformat()
        # every enabled strategy model evaluates the same data with its own settings
        profiles = get_profiles(settings)
        verdict = None
        for pid, pcfg in profiles.items():
            if not pcfg.get("enabled"):
                continue
            p_settings = profile_settings(settings, pid, profiles)
            v = evaluate_v2(feats, extraction, dilution, p_settings, t_et)
            await self._record_lifecycle(sym, session_date, feats, v, catalyst,
                                         dilution, p_settings, profile=pid)
            if pid == "primary":
                verdict = v
        if verdict is None:
            verdict = evaluate_v2(feats, extraction, dilution, settings, t_et)

        async with SessionLocal() as db:
            cand = Candidate(run_id=run_id, symbol=sym, session_date=session_date,
                             score=result["score"], qualified=result["buy"],
                             status="blocked" if result["hard_blocks"] else
                             ("buy" if result["buy"] else "candidate"),
                             block_reasons=result["hard_blocks"] + gates_failed)
            db.add(cand)
            await db.flush()
            db.add(CandidateFeatureSnapshot(candidate_id=cand.id,
                                            features={k: v for k, v in feats.items()
                                                      if k not in ("catalyst", "filing_context")},
                                            score_detail=result))
            await db.commit()

        if False and result["buy"] and not gates_failed:  # superseded by v2 lifecycle
            await self._maybe_fire_buy(sym, feats, result, catalyst, filings, session_date)
        elif False and (settings.get("watch_enabled", True)
              and result["score"] >= (settings.get("watch_score_min") or 50)
              and not result["hard_blocks"]
              and feats.get("quote_fresh")
              and (feats.get("pm_volume") or 0) > 0
              # loss-reduction filters (learned from tracked outcomes):
              # 1) never chase an already-extended spike
              and (feats.get("gap_pct") is None or
                   feats["gap_pct"] <= (settings.get("watch_max_gap_pct") or 100))
              # 2) a real positive, fresh catalyst must exist
              and catalyst is not None
              and catalyst.get("direction") == "positive"
              and catalyst.get("novelty") in ("new", "update")
              # 3) buyers must be in control (above VWAP, or VWAP unknown)
              and feats.get("above_vwap") is not False):
            # WATCH pick: notable but not fully qualified — permanently recorded and
            # tracked from the price it was found at, clearly labeled non-BUY.
            await self._maybe_fire_watch(sym, feats, result, catalyst, session_date)

        def _fmt_n(v):
            if v is None: return "?"
            v = float(v)
            for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
                if abs(v) >= div: return f"{v/div:.1f}{suf}"
            return f"{v:.0f}"
        REASON = {
            "price_range": lambda: f"price ${feats.get('price') or 0:.2f} outside ${settings.get('price_min') or 0:g}-{('$%g' % settings['price_max']) if settings.get('price_max') is not None else 'inf'}",
            "market_cap_range": lambda: f"mkt cap ${_fmt_n(feats.get('market_cap'))} outside limits",
            "float_range": lambda: f"float {_fmt_n(feats.get('float_shares'))} outside limits",
            "shares_outstanding_range": lambda: f"shares out {_fmt_n(feats.get('shares_outstanding'))} outside limits",
            "min_pm_volume": lambda: f"PM vol {_fmt_n(feats.get('pm_volume'))} < {_fmt_n(settings.get('min_pm_volume'))}",
            "min_pm_dollar_volume": lambda: f"PM $vol ${_fmt_n(feats.get('pm_dollar_volume'))} < ${_fmt_n(settings.get('min_pm_dollar_volume'))}",
        }
        gate_reasons = []
        for g in gates_failed:
            try:
                gate_reasons.append(REASON.get(g, lambda: g)())
            except Exception:
                gate_reasons.append(g)

        return {
            "symbol": sym, "name": (profile or {}).get("name") or quote.get("name", ""),
            "exchange": (profile or {}).get("exchange") or quote.get("exchange", ""),
            "score": result["score"], "buy": result["buy"],
            "price": feats.get("price"), "price_indicative": feats.get("price_indicative"),
            "gap_pct": feats.get("gap_pct"),
            "rvol": feats.get("rvol"), "rvol_estimated": feats.get("rvol_estimated"),
            "rvol_confidence": feats.get("rvol_confidence"),
            "pm_volume": feats.get("pm_volume"), "pm_dollar_volume": feats.get("pm_dollar_volume"),
            "float_shares": feats.get("float_shares"),
            "float_rotation": feats.get("float_rotation"),
            "shares_outstanding": feats.get("shares_outstanding"),
            "market_cap": feats.get("market_cap"),
            "spread_pct": feats.get("spread_pct"), "vwap": feats.get("vwap"),
            "above_vwap": feats.get("above_vwap"),
            "early": result.get("early", False),
            "catalyst_type": (catalyst or {}).get("catalyst_type", ""),
            "catalyst_sources": {"news": len(news_items),
                                 "filings": len(filings)},
            "catalyst_direction": (catalyst or {}).get("direction", ""),
            "catalyst_summary": (catalyst or {}).get("summary", ""),
            "filing_forms": feats["filing_context"].get("recent_forms", [])[:6],
            "filing_links": [{"form": f_["form_type"], "url": f_.get("primary_doc_url", "")}
                             for f_ in filings[:5]],
            "hard_blocks": result["hard_blocks"], "gates_failed": gates_failed,
            "gate_reasons": gate_reasons, "explain": result.get("explain", []),
            "gates": result["gates"], "components": result["components"],
            "penalties": result["penalties"],
            "quote_fresh": feats.get("quote_fresh"),
            "provider_ts": feats.get("provider_ts"),
            "sector": (profile or {}).get("sector", ""),
            "ts": now_utc().isoformat(),
        }

    async def _had_recent_reverse_split(self, sym: str) -> bool:
        if sym in self._split_cache:
            return self._split_cache[sym]
        flag = False
        try:
            from datetime import date as _date
            for s in await self.ctx.fmp.splits(sym):
                try:
                    d = _date.fromisoformat(s["date"][:10])
                except (ValueError, TypeError):
                    continue
                num, den = s.get("numerator"), s.get("denominator")
                if (num and den and num < den and
                        (now_et().date() - d).days <= 90):
                    flag = True
                    break
        except Exception:
            flag = False
        self._split_cache[sym] = flag
        return flag

    async def _maybe_fire_buy(self, sym: str, feats: Dict[str, Any], result: Dict[str, Any],
                              catalyst: Optional[Dict[str, Any]], filings: List[dict],
                              session_date: str):
        """Transition-only signal creation with idempotency + fresh-price requirement."""
        if not feats.get("quote_fresh") or not feats.get("price"):
            return  # never backdate; wait for a fresh quote
        fp = sigsvc.catalyst_fingerprint(catalyst)
        async with SessionLocal() as db:
            if await sigsvc.recent_signal_exists(db, sym, session_date, STRATEGY_VERSION, fp):
                return
            evidence = {
                "catalyst": catalyst,
                "filings": [{k: (v.isoformat() if hasattr(v, "isoformat") else v)
                             for k, v in f.items()} for f in filings[:10]],
                "news": [{"headline": n.get("headline"), "url": n.get("url"),
                          "published_at": str(n.get("published_at"))}
                         for n in self.ctx.news_by_symbol.get(sym, [])[:10]],
                "features": {k: feats.get(k) for k in (
                    "price", "gap_pct", "rvol", "pm_volume", "pm_dollar_volume",
                    "vwap", "pm_high", "pm_low", "spread_pct", "provider_ts")},
            }
            sig = await sigsvc.create_buy_signal(
                db, symbol=sym, session_date=session_date,
                strategy_version=STRATEGY_VERSION,
                price=feats["price"],
                price_source=f"fmp:quote@{feats.get('provider_ts') or 'bar'}",
                provider_ts=None,
                score_snapshot=result, evidence_snapshot=evidence)
            if sig:
                await self._health("info", "signals", f"BUY {sym} @ {feats['price']}")
                await broadcaster.publish("buy_signal", {
                    "symbol": sym, "price": feats["price"],
                    "score": result["score"], "initiated_at": sig.initiated_at.isoformat(),
                    "signal_uid": sig.signal_uid})

    async def _maybe_fire_watch(self, sym: str, feats: Dict[str, Any],
                                result: Dict[str, Any],
                                catalyst: Optional[Dict[str, Any]], session_date: str):
        if not feats.get("price"):
            return
        fp = "watch"  # one WATCH record per symbol per trading day
        async with SessionLocal() as db:
            if await sigsvc.recent_signal_exists(db, sym, session_date, STRATEGY_VERSION, fp):
                return
            evidence = {"catalyst": catalyst,
                        "features": {k: feats.get(k) for k in (
                            "price", "gap_pct", "rvol", "pm_volume", "pm_dollar_volume",
                            "float_shares", "float_rotation", "vwap", "spread_pct",
                            "provider_ts")}}
            # reuse the immutable-signal machinery; fingerprint prefix separates
            # watch idempotency from a later full BUY on the same catalyst
            sig = await sigsvc.create_buy_signal(
                db, symbol=sym, session_date=session_date,
                strategy_version=STRATEGY_VERSION, price=feats["price"],
                price_source=f"fmp:premarket-trade@{feats.get('provider_ts') or 'obs'}",
                provider_ts=None, score_snapshot=result,
                evidence_snapshot=evidence, signal_type="watch")
            if sig:
                # patch fingerprint to the watch-prefixed one for idempotency
                sig.catalyst_fingerprint = fp
                await db.commit()
                await self._health("info", "signals", f"WATCH {sym} @ {feats['price']} (score {result['score']})")
                await broadcaster.publish("buy_signal", {
                    "symbol": sym, "price": feats["price"], "type": "watch",
                    "score": result["score"], "initiated_at": sig.initiated_at.isoformat(),
                    "signal_uid": sig.signal_uid})

    async def _record_lifecycle(self, sym, session_date, feats, verdict, catalyst,
                                dilution, settings, profile="primary"):
        """Persist the v2 decision: ACTIONABLE_BUY / EARLY_WATCH / QUALIFIED_WATCH
        signals (immutable at signal time) and REJECTED shadow rows. Idempotent
        per (symbol, day, lifecycle-kind)."""
        from sqlalchemy import select as _sel
        lc = verdict["lifecycle"]
        news_items = self.ctx.news_by_symbol.get(sym, [])
        cat_pub = min((n["published_at"] for n in news_items
                       if n.get("published_at")), default=None)
        if lc in ("ACTIONABLE_BUY", "EARLY_WATCH") or                 (lc == "REJECTED" and verdict["score"] >=
                 (settings.get("watch_score_min") or 50)):
            sig_type = "buy" if lc == "ACTIONABLE_BUY" else "watch"
            lc_store = lc if lc != "REJECTED" else "QUALIFIED_WATCH"
            fp = f"v2:{profile}:{lc_store}"
            async with SessionLocal() as db:
                if not await sigsvc.recent_signal_exists(db, sym, session_date,
                                                         STRATEGY_VERSION, fp):
                    fill = verdict["fill"]
                    price_basis = (fill.get("fill_price") if lc == "ACTIONABLE_BUY"
                                   and fill.get("filled") else feats.get("price"))
                    sig = await sigsvc.create_buy_signal(
                        db, symbol=sym, session_date=session_date,
                        strategy_version=STRATEGY_VERSION,
                        price=price_basis or feats.get("price") or 0.0,
                        price_source="fmp:premarket-tape+book",
                        provider_ts=None,
                        score_snapshot={"v2": {k: verdict[k] for k in
                                        ("score", "score_components", "gates",
                                         "setup", "tier", "catalyst_grade",
                                         "rotation_zone")},
                                        "score": verdict["score"],
                                        "components": verdict["score_components"],
                                        "strategy_version": STRATEGY_VERSION},
                        evidence_snapshot={"catalyst": catalyst,
                                           "dilution": dilution,
                                           "features": {k: feats.get(k) for k in
                                            ("price", "bid", "ask", "gap_pct",
                                             "pm_volume", "pm_dollar_volume",
                                             "float_shares", "float_rotation",
                                             "vwap", "spread_pct")}},
                        signal_type=sig_type)
                    if sig:
                        sig.catalyst_fingerprint = fp
                        sig.lifecycle = lc_store
                        sig.profile = profile
                        sig.price_tier = verdict.get("tier") or ""
                        sig.versions = VERSIONS
                        sig.executable = bool(fill.get("filled")) and lc == "ACTIONABLE_BUY"
                        sig.data_quality = "complete" if feats.get("quote_fresh") else "stale"
                        sig.sig_bid = feats.get("bid")
                        sig.sig_ask = feats.get("ask")
                        sig.sig_bid_size = feats.get("bid_size")
                        sig.sig_ask_size = feats.get("ask_size")
                        sig.sig_spread_pct = feats.get("spread_pct")
                        sig.proposed_entry = verdict["setup"].get("entry")
                        sig.sim_fill_price = fill.get("fill_price")
                        sig.no_fill_reason = fill.get("no_fill_reason") or ""
                        sig.catalyst_published_at = cat_pub
                        fs = self._first_seen.get(f"{sym}:{session_date}")
                        from datetime import datetime as _dt
                        sig.detected_at = _dt.fromisoformat(fs) if fs else now_utc()
                        sig.first_pass_at = now_utc()
                        if lc == "ACTIONABLE_BUY":
                            sig.first_actionable_quote_at = now_utc()
                            sig.first_actionable_ask = feats.get("ask")
                            pos = await paper_engine.open_position(db, sig,
                                                                   verdict["setup"])
                            if pos:
                                pos.profile = profile
                        await db.commit()
                        await self._health("info", "signals",
                                           f"{lc_store} {sym} @ {price_basis} "
                                           f"(score {verdict['score']})")
                        await broadcaster.publish("buy_signal", {
                            "symbol": sym, "price": price_basis,
                            "type": sig_type, "lifecycle": lc_store,
                            "score": verdict["score"],
                            "initiated_at": sig.initiated_at.isoformat(),
                            "signal_uid": sig.signal_uid})
        if lc in ("REJECTED", "EXPIRED"):
            async with SessionLocal() as db:
                exists = (await db.execute(_sel(RejectedCandidate.id).where(
                    RejectedCandidate.symbol == sym,
                    RejectedCandidate.session_date == session_date,
                    RejectedCandidate.profile == profile))).first()
                if not exists:
                    db.add(RejectedCandidate(
                        symbol=sym, session_date=session_date, lifecycle=lc,
                        profile=profile,
                        rejection_reason=(verdict.get("rejection_reason") or "")[:2000],
                        failed_gates=[g["gate"] for g in verdict["gates"]
                                      if not g["pass"]],
                        price_at_reject=feats.get("price"),
                        score=verdict["score"],
                        snapshot={"features": {k: feats.get(k) for k in
                                  ("price", "gap_pct", "pm_dollar_volume",
                                   "float_rotation", "spread_pct", "vwap")},
                                  "gates": verdict["gates"]},
                        versions=VERSIONS,
                        shadow_until=now_utc() + timedelta(hours=8)))
                    await db.commit()

    async def _models_cycle(self, settings, phase):
        """One pass of the 14 non-scalper model engines over the shared bounded
        universes. Cadence: intraday engines each call (RTH; crypto 24/7),
        daily/weekly/monthly engines once per trading day."""
        if self.mctx is None:
            self.mctx = mplat.ModelContext(self.ctx.fmp)
        t = now_et()
        today = str(t.date())
        run_daily = (self._daily_models_ran != today and
                     (t.hour > 15 or phase in ("afterhours", "closed")))
        # shared context
        ctx: Dict[str, Any] = {"bars_daily": {}, "bars_5m": {}}
        stock_syms = list(ETF_UNIVERSE)
        crypto_syms = list(CRYPTO_UNIVERSE)
        for s in stock_syms + crypto_syms:
            ctx["bars_daily"][s] = await self.mctx.daily(s)
        ctx["spy_daily"] = ctx["bars_daily"].get("SPY") or []
        self.last_regime = regime_fn(ctx)
        reg_state = self.last_regime["state"]
        if reg_state == "high_risk":
            await self._health("warn", "regime",
                               f"high_risk: {self.last_regime['why']} — models abstain")
            return
        intraday_ok = phase == "regular"
        live_syms = (stock_syms if intraday_ok else []) + crypto_syms
        for s in live_syms:
            ctx["bars_5m"][s] = await self.mctx.m5(s)
        ctx["earnings"] = await self.mctx.earnings_today() if run_daily else {}
        ctx["insider_clusters"] = {}
        ctx["fundamentals"] = {}
        profiles_cfg = get_profiles(settings)
        fired = 0
        for mid, meta in MODELS.items():
            if meta["engine"] == "scalper" or not meta.get("build"):
                continue
            pcfg = profiles_cfg.get(mid) or {}
            if pcfg.get("enabled") is False:
                continue
            eng = ENGINES.get(meta["engine"])
            if eng is None:
                continue
            allowed = REGIME_ALLOW.get(meta["engine"], {"trend", "range",
                                                        "uncertain"})
            if reg_state not in allowed:
                continue
            cadence = meta.get("cadence", "intraday")
            if cadence == "intraday" and not (intraday_ok or
                                              "crypto" in meta["asset_classes"]):
                continue
            if cadence in ("daily", "weekly", "monthly") and not run_daily:
                continue
            cfg = (pcfg.get("overrides") or {})
            if meta["engine"] == "pairs":
                symbols = [f"{a}|{b}" for a, b in PAIRS]
            elif meta["asset_classes"] == ["crypto"]:
                symbols = crypto_syms
            elif "crypto" in meta["asset_classes"]:
                symbols = (stock_syms if intraday_ok or cadence != "intraday"
                           else []) + crypto_syms
            else:
                symbols = stock_syms if (intraday_ok or cadence != "intraday") else []
            for sym in symbols:
                try:
                    v = eng(ctx, sym, cfg)
                except Exception as e:
                    await self._health("warn", f"model:{mid}",
                                       f"{sym}: {type(e).__name__}: {e}")
                    continue
                if not v or v["action"] != "buy":
                    continue
                base = sym.split("|")[0]
                q = (ctx["bars_5m"].get(base) or ctx["bars_daily"].get(base) or [])
                qp = q[-1]["c"] if q else v["entry"]
                sig = await mplat.record_model_signal(mid, base, v, qp, today,
                                                      settings)
                if sig:
                    fired += 1
                    await broadcaster.publish("buy_signal", {
                        "symbol": base, "price": v["entry"], "type": "buy",
                        "model": mid, "score": v["score"],
                        "signal_uid": sig.signal_uid,
                        "initiated_at": sig.initiated_at.isoformat()})
        if run_daily:
            self._daily_models_ran = today
        if fired:
            await self._health("info", "models", f"{fired} model signal(s) fired "
                               f"(regime {reg_state})")

    _last_snap = 0.0

    async def _equity_snapshots(self, db):
        import time as _t
        if _t.monotonic() - self._last_snap < 600:
            return
        self._last_snap = _t.monotonic()
        from sqlalchemy import select as _sel
        accs = (await db.execute(_sel(PaperAccount))).scalars().all()
        for a in accs:
            db.add(EquitySnapshot(model_id=a.model_id, equity=a.equity))

    async def _check_alerts(self, db, quotes):
        from sqlalchemy import select as _sel
        rules = (await db.execute(_sel(AlertRule).where(
            AlertRule.active == True,  # noqa: E712
            AlertRule.fired_at.is_(None)))).scalars().all()
        for r in rules:
            q = quotes.get(r.symbol)
            px = (q or {}).get("price")
            if px is None:
                continue
            hit = px >= r.price if r.condition == "above" else px <= r.price
            if hit:
                r.fired_at = now_utc()
                r.fired_price = px
                await self._health("info", "alerts",
                                   f"ALERT {r.symbol} {r.condition} {r.price} "
                                   f"@ {px} {('— ' + r.note) if r.note else ''}")
                await broadcaster.publish("alert", {
                    "symbol": r.symbol, "condition": r.condition,
                    "price": r.price, "fired_price": px, "note": r.note})

    async def _maybe_morning_brief(self, db):
        """9:25 AM ET on trading days: the system writes its own premarket
        debrief before the open."""
        t = now_et()
        if not (t.hour == 9 and 25 <= t.minute <= 35) or not is_trading_day(t.date()):
            return
        today = str(t.date())
        from sqlalchemy import select as _sel
        exists = (await db.execute(_sel(MorningBrief.id).where(
            MorningBrief.session_date == today,
            MorningBrief.kind == "morning"))).first()
        if exists:
            return
        sigs = (await db.execute(_sel(BuySignal).where(
            BuySignal.session_date == today,
            BuySignal.is_demo == False))).scalars().all()  # noqa: E712
        rej = (await db.execute(_sel(RejectedCandidate).where(
            RejectedCandidate.session_date == today))).scalars().all()
        pos = (await db.execute(_sel(PaperPosition).where(
            PaperPosition.status == "open"))).scalars().all()
        early = [s for s in sigs if s.lifecycle == "EARLY_WATCH"]
        buys = [s for s in sigs if s.lifecycle == "ACTIONABLE_BUY"]
        watch = [s for s in sigs if s.lifecycle == "QUALIFIED_WATCH"]
        from collections import Counter
        top_rej = Counter()
        for r in rej:
            for g in (r.failed_gates or [])[:2]:
                top_rej[g] += 1
        content = {
            "headline": (f"{len(buys)} actionable BUY(s), {len(early)} early "
                         f"watch(es), {len(watch)} qualified watch(es), "
                         f"{len(rej)} rejected this premarket."),
            "regime": self.last_regime,
            "buys": [{"symbol": s.symbol, "profile": s.profile,
                      "price": s.buy_signal_price,
                      "time_et": s.initiated_at.astimezone(ET).strftime("%H:%M")
                      if s.initiated_at else None} for s in buys[:10]],
            "early_watches": [{"symbol": s.symbol, "price": s.buy_signal_price}
                              for s in early[:10]],
            "carried_positions": [{"symbol": p.symbol, "model": p.profile,
                                   "entry": p.entry_fill, "stop": p.stop}
                                  for p in pos[:10]],
            "top_rejection_reasons": top_rej.most_common(5),
            "noon_locks_pending": len(buys) + len(early) + len(watch),
            "note": "Auto-generated at 9:25 ET. Noon outcomes lock at 12:00.",
        }
        db.add(MorningBrief(session_date=today, kind="morning", content=content))
        await self._health("info", "brief", f"morning brief written: "
                           f"{content['headline']}")

    async def _nightly_research(self, settings):
        """After-close research: replay the completed day through the frozen
        engine, update challenger stats, and audit-log the promotion decision.
        Auto-promotion is hard-gated on predefined requirements (>=100 forward
        paper trades among them) — until then every decision is 'hold' with the
        evidence recorded. Never changes the primary strategy during hours."""
        from sqlalchemy import select as _sel
        from .models import BtJob
        t = now_et()
        if t.hour < 20 or not is_trading_day(t.date()):
            return
        d = str(t.date())
        async with SessionLocal() as db:
            done = (await db.execute(_sel(BtJob.id).where(
                BtJob.kind == "nightly", BtJob.config_hash == d))).first()
            if done:
                return
            job = BtJob(kind="nightly", config={"date": d}, config_hash=d,
                        status="running")
            db.add(job)
            await db.commit()
            job_id = job.id
        try:
            from .bt.data import BtData
            from .bt.replay import SessionReplay
            data = BtData(SessionLocal, rps=1.5)
            rp = SessionReplay(data, ai_client=None, cfg={"cand_cap": 15})
            res = await rp.run_session(d, {**settings,
                                           "max_ext_above_vwap_pct": 20.0,
                                           "rotation_hard_cap": 1.0})
            await data.close()
            async with SessionLocal() as db:
                from .signals.service import metrics_with_outcome
                paper_n = len((await db.execute(_sel(BuySignal).where(
                    BuySignal.lifecycle == "ACTIONABLE_BUY",
                    BuySignal.is_demo == False))).scalars().all())  # noqa: E712
                promo = {"decision": "hold",
                         "reason": (f"forward paper sample {paper_n}/100 below "
                                    "predefined promotion minimum"),
                         "requirements": {"min_forward_trades": 100,
                                          "better_reliable_wr": None,
                                          "positive_expectancy": None},
                         "audit": "no automatic strategy change; rollback n/a"}
                job = await db.get(BtJob, job_id)
                job.status = "done"
                job.result = {"replay": {"date": d,
                                          "candidates": len(res["candidates"]),
                                          "signals": len(res["signals"]),
                                          "early": len(res["early"]),
                                          "rejects": len(res["rejects"])},
                              "promotion": promo}
                await db.commit()
                await self._health("info", "research",
                                   f"nightly replay {d}: {len(res['signals'])} "
                                   f"signals, promotion=hold ({paper_n}/100 fwd)")
        except Exception as e:
            async with SessionLocal() as db:
                job = await db.get(BtJob, job_id)
                if job:
                    job.status = "failed"
                    job.error = f"{type(e).__name__}: {e}"[:500]
                    await db.commit()

    # ---------------- tracking ----------------
    async def _tracking_cycle(self, settings: Dict[str, Any], finalize: bool):
        async with SessionLocal() as db:
            sigs = (await db.execute(
                select(BuySignal).where(BuySignal.status == "active",
                                        BuySignal.is_demo == False)  # noqa: E712
            )).scalars().all()
            open_model_pos = (await db.execute(select(PaperPosition).where(
                PaperPosition.status == "open"))).scalars().all()
            symbols = sorted({s.symbol for s in sigs} |
                             {p.symbol for p in open_model_pos})
            if not symbols:
                return
            phase_now = session_phase()
            try:
                quotes = {q["symbol"]: q for q in await self.ctx.fmp.quotes(symbols)}
                if phase_now in ("premarket", "afterhours"):
                    # /quote lags for small caps outside RTH — merge the live
                    # extended-hours trade print when it is fresher
                    for sym_ in symbols:
                        amt = await self.ctx.fmp.aftermarket_trade(sym_)
                        if amt and amt.get("price") and amt.get("provider_ts"):
                            q = quotes.get(sym_) or {"symbol": sym_}
                            q_ts = q.get("provider_ts")
                            if q_ts is None or amt["provider_ts"] > q_ts:
                                quotes[sym_] = {**q, "price": amt["price"],
                                                "provider_ts": amt["provider_ts"]}
                        amq2 = await self.ctx.fmp.aftermarket_quote(sym_)
                        if amq2:
                            q = quotes.get(sym_) or {"symbol": sym_}
                            quotes[sym_] = {**q, "bid": amq2.get("bid"),
                                            "ask": amq2.get("ask")}
            except Exception as e:
                await self._health("warn", "tracking", f"quote fetch failed: {e}")
                return  # tracking failure must not alter signals
            updates = []
            for sig in sigs:
                q = quotes.get(sig.symbol)
                if not q or not q.get("price"):
                    continue
                q_ts = q.get("provider_ts")
                if phase_now != "regular" and (
                        q_ts is None or (now_utc() - q_ts).total_seconds() > 600):
                    continue  # never write stale extended-hours prints into tracking
                await sigsvc.update_tracking(db, sig, price=q["price"],
                                             provider_ts=q.get("provider_ts"),
                                             day_high=q.get("day_high"),
                                             day_low=q.get("day_low"))
                w_start, w_end = sigsvc.outcome_window(sig, settings)
                if w_start <= now_utc() <= w_end:
                    sigsvc.update_post_window(sig, q["price"])
                await sigsvc.record_due_checkpoints(db, sig, q["price"])
                if finalize:
                    await sigsvc.record_close_checkpoint(db, sig, q["price"])
                m = sigsvc.metrics_with_outcome(sig, settings)
                updates.append({"signal_uid": sig.signal_uid, "symbol": sig.symbol,
                                "buy_price": sig.buy_signal_price,
                                "current": sig.current_live_price,
                                "day_high": sig.day_high, "day_low": sig.day_low,
                                "since_high": sig.since_signal_high,
                                "since_low": sig.since_signal_low, **m})
            try:
                pos_updates = await paper_engine.update_positions(
                    db, quotes, settings)
                pos_updates += await mplat.settle_positions(db, quotes, settings)
                locked = await mplat.finalize_noon_outcomes(db, quotes)
                if locked:
                    await self._health("info", "outcomes",
                                       f"{locked} noon outcome(s) locked "
                                       f"({mplat.NOON_POLICY})")
                await self._equity_snapshots(db)
                await self._check_alerts(db, quotes)
                await self._maybe_morning_brief(db)
            except Exception as e:
                pos_updates = []
                await self._health("warn", "paper", f"{type(e).__name__}: {e}")
            # shadow-track today's rejected candidates (false-negative analysis)
            try:
                from sqlalchemy import select as _sel
                rej = (await db.execute(_sel(RejectedCandidate).where(
                    RejectedCandidate.shadow_until > now_utc()))).scalars().all()
                for rc in rej:
                    q = quotes.get(rc.symbol)
                    if q and q.get("price"):
                        px = q["price"]
                        rc.shadow_last = px
                        rc.shadow_high = max(rc.shadow_high or px, px)
                        rc.shadow_low = min(rc.shadow_low or px, px)
            except Exception:
                pass
            await db.commit()
            if updates:
                await broadcaster.publish("signals", {"rows": updates,
                                                      "ts": now_utc().isoformat()})
            if pos_updates:
                await broadcaster.publish("positions", {"rows": pos_updates,
                                                        "ts": now_utc().isoformat()})
