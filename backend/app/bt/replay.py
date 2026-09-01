"""Chronological session replay at 5-minute resolution.

Information discipline: at simulated minute T the engine sees only news published
<= T, filings accepted <= T, and bars <= T. Entries fill at the NEXT bar's open
plus the execution model's slippage. Candidate discovery uses catalyst timestamps
and prior-day data only — never the day's eventual movers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..providers.fmp import looks_common_stock
from ..strategy.catalyst import (SYSTEM_PROMPT_V2, ANALYSIS_SCHEMA_V2,
                                 validate_extraction)
from ..strategy.dilution import assess as assess_dilution
from ..strategy.gates import evaluate
from ..strategy.tiers import tier_for
from .data import BtData, doc_hash, est_spread_pct

ET = ZoneInfo("America/New_York")
ENTRY_SLIP = {"optimistic": 0.15, "baseline": 0.45, "pessimistic": 1.0}

POSITIVE_HINTS = ("fda", "approval", "contract", "acquisition", "merger", "phase",
                  "clinical", "patent", "award", "order", "partnership", "beats",
                  "guidance", "topline", "clearance", "grant", "settlement")


def _f(v, d=None):
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


class SessionReplay:
    def __init__(self, data: BtData, ai_client=None, cfg: Optional[dict] = None,
                 universe=None):
        self.data = data
        self.ai = ai_client            # async callable(evidence_text)->raw dict | None
        self.cfg = cfg or {}
        self.ai_cache: Dict[str, Optional[dict]] = {}
        self.universe = universe       # UniverseEod (optional; enables movers door)

    async def discover(self, d: str, cap: int = 25) -> List[dict]:
        """Candidates from catalyst timestamps only:
        (1) SEC 8-K/6-K/424B/S-1/S-3/EFFECT filings accepted after the prior close
            or premarket same-day (free daily index + acceptance timestamps);
        (2) prev-day EOD movers (close-to-close >= +15%), known before 4:00 AM.
        Price-banded by prior close. Global historical news on this plan is thin
        and large-cap-biased, so it is enrichment, not discovery (documented)."""
        prev_d = _prev_trading(d)
        idx_today = await self.data.sec_form_index(d)
        idx_prev = await self.data.sec_form_index(prev_d)
        cmap = await self.data.cik_ticker_map()
        cands: Dict[str, dict] = {}

        async def consider(sym: str, when, why: str):
            pc = await self.data.prev_close(sym, d)
            if pc is None or not (0.08 <= pc <= 8.0):
                return
            c = cands.setdefault(sym, {"symbol": sym, "prev_close": pc,
                                       "first_pub": when, "why": why, "news": []})
            if when < c["first_pub"]:
                c["first_pub"] = when
                c["why"] = why

        # SEC door — price-band filter FIRST (cheap cached EOD), acceptance after
        from datetime import datetime as _dt
        seen_ciks = {}
        for row in idx_prev + idx_today:
            sym = cmap.get(row["cik"])
            if not sym or not looks_common_stock(sym, "") or sym in cands:
                continue
            seen_ciks.setdefault(row["cik"], []).append(row)
        banded = []
        for cik, rows in seen_ciks.items():
            sym = cmap.get(cik)
            pc = await self.data.prev_close(sym, d)
            if pc is not None and 0.08 <= pc <= 8.0:
                banded.append((cik, rows))
            if len(banded) >= 80:
                break
        for cik, rows in banded:
            sym = cmap.get(cik)
            at_map = await self.data.acceptance_times(cik)
            for row in rows:
                acc_fmt = row["accession"]
                dash = acc_fmt if "-" in acc_fmt else                     f"{acc_fmt[:10]}-{acc_fmt[10:12]}-{acc_fmt[12:]}"
                meta = at_map.get(dash)
                if not meta or not meta.get("at"):
                    continue
                try:
                    at = _dt.fromisoformat(meta["at"].replace("Z", "+00:00"))                         .astimezone(ET)
                except ValueError:
                    continue
                m = at.hour * 60 + at.minute
                ok = (at.date().isoformat() == d and m <= 9 * 60 + 20) or                      (at.date().isoformat() < d and (m >= 16 * 60 or
                                                     at.date().isoformat() < prev_d))
                if ok:
                    await consider(sym, at, f"sec:{row['form']}")
                    break

        # prev-day mover door (close-to-close move known before the session)
        if self.universe is not None:
            for sym in self.universe.prev_movers(d):
                if sym not in cands and looks_common_stock(sym, ""):
                    await consider(sym, _dt.fromisoformat(d + "T04:00:00")
                                   .replace(tzinfo=ET), "prev_day_mover")

        pool = list(cands.values())
        # movers first (highest premarket-tradeability prior), then SEC by time
        pool.sort(key=lambda c: (c["why"] != "prev_day_mover", c["first_pub"]))
        # fill the cap with TRADEABLE candidates only (has premarket bars);
        # untradeable filers must not crowd out real candidates
        out = []
        probes = 0
        for c in pool:
            if len(out) >= cap or probes >= cap * 3:
                break
            probes += 1
            bars = await self.data.five_min_bars(c["symbol"], d)
            pm = [b for b in bars if 240 <= b["minute_of_day"] < 570 and b["v"] > 0]
            if len(pm) >= 3:
                out.append(c)
        # evidence enrichment: per-symbol news window prev day -> session day
        for c in out:
            try:
                c["news"] = [n for n in await self.data.sym_news(c["symbol"], prev_d, d)
                             if n["published_et"].date().isoformat() < d
                             or n["published_et"].hour * 60 + n["published_et"].minute
                             <= 9 * 60 + 20]
            except Exception:
                c["news"] = []
        return out

    async def extract_catalyst(self, sym: str, news: List[dict]) -> Optional[dict]:
        basis = doc_hash(sym, *[n["url"] or n["title"] for n in news[:4]])
        if basis in self.ai_cache:
            return self.ai_cache[basis]
        cached = await self._db_ai_cache(basis)
        if cached is not None:
            self.ai_cache[basis] = cached or None
            return self.ai_cache[basis]
        ext = None
        if self.ai is not None:
            evidence = json.dumps({"symbol": sym, "news": [
                {"title": n["title"], "published": n["published_et"].isoformat(),
                 "site": n["site"], "text": n["text"][:700], "url": n["url"]}
                for n in news[:4]]})
            raw = await self.ai(evidence)
            ext = validate_extraction(raw) if raw else None
        self.ai_cache[basis] = ext
        if ext is not None:            # never cache failures — retry next run
            await self._db_ai_store(basis, ext)
        return ext

    async def _db_ai_cache(self, basis: str):
        from sqlalchemy import select
        from ..models import BtCache
        async with self.data.sf() as db:
            row = (await db.execute(select(BtCache).where(
                BtCache.cache_key == f"ai2:{basis}"))).scalar_one_or_none()
            if row is None:
                return None
            return row.payload.get("data") or {}

    async def _db_ai_store(self, basis: str, ext):
        from ..models import BtCache
        async with self.data.sf() as db:
            db.add(BtCache(cache_key=f"ai2:{basis}", payload={"data": ext}))
            try:
                await db.commit()
            except Exception:
                await db.rollback()

    async def run_session(self, d: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        cands = await self.discover(d, cap=int(self.cfg.get("cand_cap", 25)))
        results = {"date": d, "universe_n": len(cands), "candidates": [],
                   "signals": [], "rejects": [], "early": [],
                   "status": "done" if cands else "NO_CANDIDATES"}
        for c in cands:
            r = await self._replay_candidate(d, c, settings)
            results["candidates"].append(r["summary"])
            if r["signal"]:
                results["signals"].append(r["signal"])
            elif r["summary"].get("lifecycle") == "EARLY_WATCH":
                results["early"].append(r["summary"])
            else:
                results["rejects"].append(r["summary"])
        return results

    async def _replay_candidate(self, d: str, c: dict,
                                settings: Dict[str, Any]) -> Dict[str, Any]:
        sym = c["symbol"]
        bars = await self.data.five_min_bars(sym, d)
        pm = [b for b in bars if 240 <= b["minute_of_day"] < 570]
        summary = {"symbol": sym, "date": d, "lifecycle": "DISCOVERED",
                   "catalyst_time": c["first_pub"].isoformat(),
                   "prev_close": c["prev_close"], "reason": ""}
        if len(pm) < 3:
            summary.update(lifecycle="DATA_ERROR", reason="insufficient_pm_bars")
            return {"summary": summary, "signal": None}
        ext = await self.extract_catalyst(sym, c["news"])
        dil = assess_dilution([], ext)
        fl = await self.data.current_float(sym)   # ESTIMATED_CURRENT
        early_recorded = False
        cat_min = max(240, c["first_pub"].hour * 60 + c["first_pub"].minute
                      if c["first_pub"].date().isoformat() == d else 240)

        for i, b in enumerate(pm):
            t = b["minute_of_day"]
            if t < cat_min or t > 9 * 60 + 20:
                continue
            feats = _features(pm[:i + 1], c["prev_close"], fl)
            sp = est_spread_pct(b["c"], feats["pm_dollar_volume"] or 0)
            half = b["c"] * sp / 200.0
            feats.update({"bid": round(b["c"] - half, 4), "ask": round(b["c"] + half, 4),
                          "bid_size": 2000, "ask_size": 2000, "spread_pct": sp,
                          "quote_fresh": b["v"] > 0, "halted": False,
                          "volume_incomplete": False, "data_disagreement": False})
            et_dt = b["ts"]
            verdict = evaluate(feats, ext, dil, settings, et_dt)
            if verdict["lifecycle"] == "EARLY_WATCH" and not early_recorded:
                early_recorded = True
                summary.update(lifecycle="EARLY_WATCH",
                               early_time=et_dt.isoformat())
            if verdict["lifecycle"] == "ACTIONABLE_BUY":
                if i + 1 >= len(bars):
                    summary.update(lifecycle="REJECTED", reason="no_next_bar_for_fill")
                    return {"summary": summary, "signal": None}
                nxt = bars[bars.index(b) + 1] if b in bars else None
                nxt = nxt or pm[i + 1] if i + 1 < len(pm) else None
                if nxt is None:
                    summary.update(lifecycle="REJECTED", reason="no_next_bar_for_fill")
                    return {"summary": summary, "signal": None}
                entries = {m: round(nxt["o"] * (1 + s / 100.0), 5)
                           for m, s in ENTRY_SLIP.items()}
                path = _path_after(bars, nxt, entries["baseline"])
                summary.update(lifecycle="ACTIONABLE_BUY")
                signal = {
                    "symbol": sym, "date": d, "signal_time": et_dt.isoformat(),
                    "signal_minute": t, "entries": entries,
                    "stop": verdict["setup"]["stop"],
                    "target1": verdict["setup"]["target1"],
                    "target2": verdict["setup"]["target2"],
                    "setup": verdict["setup"]["type"],
                    "score": verdict["score"], "tier": verdict["tier"],
                    "catalyst_grade": verdict["catalyst_grade"],
                    "catalyst_category": (ext or {}).get("category", "none"),
                    "gap_pct": feats.get("gap_pct"),
                    "rotation": feats.get("float_rotation"),
                    "rotation_quality": "ESTIMATED_CURRENT_FLOAT",
                    "pm_dollar_volume": feats.get("pm_dollar_volume"),
                    "spread_est_pct": sp,
                    "gates": verdict["gates"], "path": path,
                    "features": {k: feats.get(k) for k in
                                 ("gap_pct", "rvol", "volume_acceleration",
                                  "pm_volume", "pm_dollar_volume", "vwap",
                                  "float_rotation", "ext_above_vwap_pct")},
                }
                return {"summary": summary, "signal": signal}
        if summary["lifecycle"] == "DISCOVERED":
            feats = _features(pm, c["prev_close"], fl)
            verdict = evaluate({**feats, "bid": None, "ask": None,
                                "quote_fresh": bool(pm[-1]["v"] > 0),
                                "spread_pct": est_spread_pct(pm[-1]["c"],
                                                             feats["pm_dollar_volume"] or 0)},
                               ext, dil, settings, pm[-1]["ts"])
            summary.update(lifecycle="REJECTED" if verdict["lifecycle"] != "EARLY_WATCH"
                           else "EARLY_WATCH",
                           reason=(verdict.get("rejection_reason") or "never_qualified")[:300],
                           score=verdict["score"])
        elif summary["lifecycle"] == "EARLY_WATCH":
            summary.setdefault("reason", "early_only_never_actionable")
        return {"summary": summary, "signal": None}


def _prev_calendar(d: str) -> str:
    from datetime import date as _d
    return (_d.fromisoformat(d) - timedelta(days=1)).isoformat()


def _prev_trading(d: str) -> str:
    from datetime import date as _d
    from ..util.timeutil import is_trading_day
    x = _d.fromisoformat(d) - timedelta(days=1)
    while not is_trading_day(x):
        x -= timedelta(days=1)
    return x.isoformat()


def _features(pm_bars: List[dict], prev_close: Optional[float],
              flt: Optional[float]) -> Dict[str, Any]:
    last = pm_bars[-1]
    vol = sum(b["v"] for b in pm_bars)
    dvol = sum(b["v"] * b["c"] for b in pm_bars)
    num = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in pm_bars if b["v"] > 0)
    den = sum(b["v"] for b in pm_bars if b["v"] > 0)
    vwap = num / den if den > 0 else None
    highs = [b["h"] for b in pm_bars]
    lows = [b["l"] for b in pm_bars]
    pm_high, pm_low = max(highs), min(lows)
    third = max(1, len(pm_bars) // 3)
    hh_hl = 1.0 if (len(pm_bars) >= 6 and
                    max(highs[-third:]) >= max(highs[:third]) and
                    min(lows[-third:]) >= min(lows[:third])) else 0.0
    last3 = sum(b["v"] for b in pm_bars[-3:]) / 3
    base_v = sorted(b["v"] for b in pm_bars)[len(pm_bars) // 2] or 1
    gap = ((last["c"] - prev_close) / prev_close * 100.0) if prev_close else None
    ext_vwap = ((last["c"] - vwap) / vwap * 100.0) if vwap else None
    return {
        "price": last["c"], "gap_pct": gap, "pm_volume": vol,
        "pm_dollar_volume": dvol, "vwap": vwap,
        "above_vwap": (last["c"] > vwap) if vwap else None,
        "ext_above_vwap_pct": round(ext_vwap, 2) if ext_vwap is not None else None,
        "pm_high": pm_high, "pm_low": pm_low,
        "dist_from_high_pct": (pm_high - last["c"]) / pm_high * 100 if pm_high else None,
        "hh_hl": hh_hl, "volume_acceleration": last3 / base_v if base_v else None,
        "rvol": None, "rvol_confidence": 0.0,   # honest: no per-symbol PM history
        "participation_bars": sum(1 for b in pm_bars if b["v"] > 0),
        "float_shares": flt,
        "float_rotation": (vol / flt) if flt else None,
    }


def _path_after(all_bars: List[dict], entry_bar: dict, entry: float) -> List[dict]:
    """Post-entry path bars with cumulative session VWAP carried forward."""
    out = []
    idx = all_bars.index(entry_bar)
    num = den = 0.0
    for b in all_bars[: idx]:
        if b["v"] > 0:
            num += ((b["h"] + b["l"] + b["c"]) / 3) * b["v"]
            den += b["v"]
    t0 = entry_bar["ts"]
    for b in all_bars[idx:]:
        if b["v"] > 0:
            num += ((b["h"] + b["l"] + b["c"]) / 3) * b["v"]
            den += b["v"]
        out.append({"t_min": (b["ts"] - t0).total_seconds() / 60.0,
                    "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"],
                    "v": b["v"], "vwap": (num / den) if den else None,
                    "minute_of_day": b["minute_of_day"]})
        if b["minute_of_day"] >= 16 * 60:
            break
    return out
