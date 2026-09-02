"""Multi-model platform worker: shared context builders, cadence scheduling,
signal recording, paper ledgers, and the locked noon-outcome finalizer.
Live scanning always outranks research; context fetches are bounded + cached."""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from ..db import SessionLocal
import asyncio
import logging
import re

from ..models import (BuySignal, LockedOutcome, PaperAccount, PaperPosition,
                      RejectedCandidate,
                      SignalEvent)
from ..signals import service as sigsvc
from ..util.timeutil import ET, is_trading_day, now_et, now_utc
from .engines import ENGINES, REGIME_ALLOW, regime as regime_fn
from .execution import simulate_sell_price
from .registry import (CRYPTO_UNIVERSE, ETF_UNIVERSE, MODELS, PAIRS,
                       STARTING_CASH)
from .versions import VERSIONS

RISK_PCT_PER_TRADE = 1.0          # % of account equity risked per position
NOON_POLICY = "PREMARKET_SCALPER_OUTCOME_V1"


class ModelContext:
    """Bounded, TTL-cached shared market context for all engines."""

    def __init__(self, fmp):
        self.fmp = fmp
        self._daily: Dict[str, tuple] = {}
        self._m5: Dict[str, tuple] = {}
        self._earnings: tuple = (0.0, {})
        self._insiders: tuple = (0.0, {})
        self._movers: tuple = (0.0, [])

    async def daily(self, sym: str) -> List[dict]:
        hit = self._daily.get(sym)
        if hit and _time.monotonic() - hit[0] < 3600:
            return hit[1]
        try:
            # Rolling window: a fixed end-date literal would silently start
            # serving frozen bars once that date passed.
            _today = now_et().date()
            data = await self.fmp._get("historical-price-eod/full",
                                       {"symbol": sym,
                                        "from": str(_today - timedelta(days=420)),
                                        "to": str(_today + timedelta(days=1))},
                                       cache_ttl=1800, endpoint_name="eod-full")
        except Exception:
            data = []
        bars = [{"o": r.get("open"), "h": r.get("high"), "l": r.get("low"),
                 "c": r.get("close"), "v": r.get("volume") or 0,
                 "date": r.get("date")}
                for r in (data if isinstance(data, list) else [])
                if r.get("close")]
        bars.sort(key=lambda b: b["date"] or "")
        self._daily[sym] = (_time.monotonic(), bars)
        return bars

    async def m5(self, sym: str) -> List[dict]:
        hit = self._m5.get(sym)
        # Per-symbol jitter on the TTL. With a flat 240s every symbol in the
        # universe expired on the same tick and refetched as one burst, which
        # drew 429s and tripped the circuit breaker each cycle.
        ttl = 240 + (hash(sym) % 121) - 60          # 180..300s, stable per symbol
        if hit and _time.monotonic() - hit[0] < ttl:
            return hit[1]
        d = str(now_et().date())
        try:
            data = await self.fmp._get("historical-chart/5min",
                                       {"symbol": sym, "from": d, "to": d,
                                        "extended": "true"},
                                       cache_ttl=240,
                                       endpoint_name="5min-live")
        except Exception:
            data = []
        bars = []
        for r in (data if isinstance(data, list) else []):
            try:
                ts = datetime.fromisoformat(r["date"]).replace(tzinfo=ET)
            except (KeyError, ValueError):
                continue
            bars.append({"o": r["open"], "h": r["high"], "l": r["low"],
                         "c": r["close"], "v": r.get("volume") or 0,
                         "minute_of_day": ts.hour * 60 + ts.minute, "ts": ts})
        bars.sort(key=lambda b: b["minute_of_day"])
        self._m5[sym] = (_time.monotonic(), bars)
        return bars

    async def movers(self, cap: int = 50) -> List[str]:
        """Today's actual movers: most-actives plus biggest-gainers.

        The intraday engines were scanning a fixed list of 26 calm large caps
        and ETFs, where a breakout or pattern setup on a 1%-range day is
        genuinely rare, so they reported 'no setup' all session. A day-trading
        fleet has to look where the day's movement is. Cached 10 minutes;
        two calls per refresh.
        """
        if self._movers[0] and _time.monotonic() - self._movers[0] < 600:
            return self._movers[1]
        seen, out = set(), []
        for path in ("most-actives", "biggest-gainers"):
            try:
                rows = await self.fmp._get(path, {}, cache_ttl=300,
                                           endpoint_name=path)
            except Exception:
                rows = []
            for r in (rows if isinstance(rows, list) else []):
                sym = str(r.get("symbol") or "").upper()
                px = r.get("price")
                try:
                    px = float(px) if px is not None else None
                except (TypeError, ValueError):
                    px = None
                # tradeable, US-listed, not a crypto pair, not a warrant/unit
                if (not sym or sym in seen or sym.endswith("USD")
                        or "." in sym or "-" in sym or len(sym) > 5
                        or (px is not None and px < 1.0)):
                    continue
                seen.add(sym)
                out.append(sym)
                if len(out) >= cap:
                    break
            if len(out) >= cap:
                break
        self._movers = (_time.monotonic(), out)
        return out

    async def earnings_today(self) -> Dict[str, dict]:
        if self._earnings[0]:
            # same rule as insiders: never hold an empty result for the full TTL
            if _time.monotonic() - self._earnings[0] < (3600 if self._earnings[1] else 600):
                return self._earnings[1]
        d0 = str((now_et() - timedelta(days=1)).date())
        d1 = str(now_et().date())
        try:
            data = await self.fmp._get("earnings-calendar",
                                       {"from": d0, "to": d1},
                                       cache_ttl=3600,
                                       endpoint_name="earnings-calendar")
        except Exception:
            data = []
        out = {}
        for r in (data if isinstance(data, list) else []):
            a, e = r.get("epsActual"), r.get("epsEstimated")
            if a is None or e in (None, 0):
                continue
            try:
                surp = (float(a) - float(e)) / abs(float(e)) * 100
            except (TypeError, ZeroDivisionError, ValueError):
                continue
            out[str(r.get("symbol", "")).upper()] = {
                "surprise_pct": surp, "date": r.get("date"),
                "quality": "ESTIMATED_CURRENT_CONSENSUS"}
        self._earnings = (_time.monotonic(), out)
        return out

    async def insider_clusters(self, sec=None) -> Dict[str, dict]:
        """Form-4 PURCHASE clusters, verified from the filing documents:
        last 5 sessions of the free EDGAR daily index -> issuers with 2+ Form 4s
        -> fetch each filing (bounded) and count distinct owners with real
        open-market purchases (transaction code P, acquired). Sales, grants and
        exercises are excluded — a Form 4 is never assumed to be a buy."""
        # An EMPTY result is not cached for six hours. The worker's first pass
        # after a restart landed inside EDGAR's cooldown after an earlier 429
        # storm, cached {} and served it all session while a fresh call found
        # six real clusters. Empty retries in 15 minutes; a hit holds 6 hours.
        if self._insiders[0]:
            age = _time.monotonic() - self._insiders[0]
            ttl = 6 * 3600 if self._insiders[1] else 900
            if age < ttl:
                return self._insiders[1]
        out: Dict[str, dict] = {}
        try:
            import httpx
            from ..bt.data import BtData
            from ..config import get_config
            data = BtData(SessionLocal, rps=1.5)
            cmap = await data.cik_ticker_map()
            counts: Dict[str, list] = {}
            # EDGAR publishes the daily form index after the close, so "today"
            # is empty for the whole trading session and "yesterday" often is
            # too. Walk back to the most recent day that actually has filings;
            # without this the model never had a universe during market hours.
            d = now_et().date()
            for _back in range(7):
                probe = await data.form4_index(str(d - timedelta(days=_back)))
                if probe:
                    d = d - timedelta(days=_back)
                    break
            checked = 0
            while checked < 5:
                if is_trading_day(d):
                    checked += 1
                    idx = await data.form4_index(str(d))
                    for row in idx:
                        sym = cmap.get(row["cik"])
                        if sym:
                            counts.setdefault(sym, []).append(
                                (row["cik"], row["accession"]))
                d -= timedelta(days=1)
            cands = {s: rows for s, rows in counts.items() if len(rows) >= 2}
            _stage = {"index_day": str(d), "index_rows": sum(len(v) for v in counts.values()),
                      "mapped_issuers": len(counts), "candidates": len(cands),
                      "fetched": 0, "p_matches": 0}
            ua = get_config().sec_user_agent
            fetched = 0
            async with httpx.AsyncClient(timeout=20,
                                         headers={"User-Agent": ua}) as client:
                # Small filers FIRST. Sorting by count descending filled the
                # budget with heavy grant-batch filers (16 Form 4s of code A)
                # while the classic two-insider purchase clusters — exactly
                # two filings — sorted to the bottom and were cut off. Three
                # docs per issuer is enough to find two distinct buyers.
                for sym, rows in sorted(cands.items(),
                                        key=lambda kv: len(kv[1]))[
                                            :max(12, INSIDER_DOC_FETCH_CAP // 3)]:
                    owners: set = set()
                    officer = False
                    accs = []
                    for cik, acc in rows[:3]:
                        # 25 fetches across ~100 candidate issuers meant only
                        # a handful were ever verified and no cluster was ever
                        # found. 120 docs once per 6h is ~12s at SEC's 10/s.
                        if fetched >= INSIDER_DOC_FETCH_CAP:
                            break
                        fetched += 1
                        nod = acc.replace("-", "")
                        # The complete submission lives INSIDE the accession
                        # folder: /data/{cik}/{nod}/{acc}.txt. The previous URL
                        # omitted the folder, 404'd on every filing, and the
                        # bare `except: pass` below hid it — so a model with
                        # real purchase clusters in its data never once fired.
                        url = (f"https://www.sec.gov/Archives/edgar/data/"
                               f"{int(cik)}/{nod}/{acc}.txt")
                        txt = ""
                        # EDGAR allows 10 req/s and answers 429 with an HTML
                        # rate-limit page — which is exactly what every fetch
                        # was getting, because this loop had no pacing at all.
                        for _try in range(2):
                            try:
                                await asyncio.sleep(SEC_FETCH_GAP_S)
                                r = await client.get(url)
                            except httpx.HTTPError:
                                break
                            if r.status_code == 200:
                                txt = r.text[:200000]
                                break
                            if r.status_code == 429:
                                await asyncio.sleep(3.0 * (_try + 1))
                                continue
                            break
                        _stage["fetched"] += 1
                        if ("<transactionCode>P</transactionCode>" in txt and
                                _ACQUIRED_RE.search(txt)):
                            _stage["p_matches"] += 1
                            # a cluster is DISTINCT people buying, not one
                            # person filing twice
                            m_ = _OWNER_RE.search(txt)
                            owners.add((m_.group(1).strip().lower() if m_
                                        else acc))
                            accs.append(acc)
                            if "officerTitle" in txt or "isOfficer>1" in txt:
                                officer = True
                    buyers = len(owners)
                    if buyers >= 2:
                        out[sym] = {"buyers": buyers, "officer": officer,
                                    "accessions": accs,
                                    "verified": "transaction_code_P"}
            await data.close()
            # One line per pass with every stage count, so an empty result can
            # be attributed to a stage instead of guessed at.
            log.info("insider_clusters stages: %s clusters=%d head=%s",
                     _stage, len(out), list(out)[:6])
        except Exception as e:
            # never silent: a broken pipeline must show up in the health log
            log.warning("insider_clusters failed: %s: %s", type(e).__name__, e)
        self._insiders = (_time.monotonic(), out)
        return out

    async def fundamentals(self, symbols) -> Dict[str, dict]:
        """Current-value TTM ratios (labeled snapshot; point-in-time not in plan)."""
        out = {}
        for s in symbols:
            try:
                data = await self.fmp._get("ratios-ttm", {"symbol": s},
                                           cache_ttl=24 * 3600,
                                           endpoint_name="ratios-ttm")
                row = data[0] if isinstance(data, list) and data else None
                if row:
                    out[s] = {"pe": row.get("priceToEarningsRatioTTM")
                              or row.get("peRatioTTM"),
                              "quality": "CURRENT_TTM_SNAPSHOT"}
            except Exception:
                continue
        return out


# Smallest stop distance a model position may carry, as a fraction of the
# fill. Below this, slippage alone can put the fill past the target.
log = logging.getLogger(__name__)

MIN_RISK_FRAC = 0.0015
INSIDER_DOC_FETCH_CAP = 120
# pacing between EDGAR document fetches: ~8/s, under SEC's 10/s ceiling
SEC_FETCH_GAP_S = 0.13
# Form 4 acquired/disposed flag, matched on the actual tag rather than a bare ">A<"
_ACQUIRED_RE = re.compile(r"<transactionAcquiredDisposedCode>\s*<value>A</value>")
_OWNER_RE = re.compile(r"<rptOwnerName>([^<]+)</rptOwnerName>")


async def _reject_geometry(db, model_id, symbol, session_date, verdict, fill, why):
    """Record a geometry rejection so a model that keeps producing untradeable
    levels is visible on its page instead of silently emitting nothing."""
    # Build the row first so a bad field fails loudly instead of being
    # swallowed. Never roll back the caller's session from inside a helper —
    # that discards work the caller has not committed yet.
    row = RejectedCandidate(
        symbol=symbol, session_date=session_date, profile=model_id,
        lifecycle="REJECTED", price_at_reject=fill,
        rejection_reason=f"invalid_trade_geometry: {why}",
        failed_gates=["trade_geometry"],
        snapshot={"verdict_levels": {k: verdict.get(k) for k in
                                     ("entry", "stop", "target1", "target2")},
                  "fill": fill})
    try:
        db.add(row)
        await db.commit()
    except Exception as e:                       # logging must not block trading
        db.expunge(row)
        log.warning("geometry rejection not recorded for %s/%s: %s",
                    model_id, symbol, e)


async def get_account(db, model_id: str, season: int = 1) -> PaperAccount:
    acc = (await db.execute(select(PaperAccount).where(
        PaperAccount.model_id == model_id,
        PaperAccount.season == season))).scalar_one_or_none()
    if acc is None:
        acc = PaperAccount(model_id=model_id, season=season,
                           starting_cash=STARTING_CASH, cash=STARTING_CASH,
                           equity=STARTING_CASH, max_equity=STARTING_CASH)
        db.add(acc)
        await db.flush()
    return acc


async def record_model_signal(model_id: str, symbol: str, verdict: Dict[str, Any],
                              quote_price: float, session_date: str,
                              settings: Dict[str, Any]) -> Optional[BuySignal]:
    """Idempotent BUY recording + paper fill + ledger debit for any model."""
    fp = f"m:{model_id}"
    slip = settings.get("slippage_pct", 0.4) / 100.0
    async with SessionLocal() as db:
        if await sigsvc.recent_signal_exists(db, symbol, session_date,
                                             VERSIONS["strategy_version"], fp):
            return None
        # A model must never open a second position in something it already
        # holds. The per-day fingerprint above keys on session_date, so a rerun
        # on a NEW date — which is what a process restart triggers for the
        # daily/weekly/monthly lanes — would otherwise re-buy the whole book.
        held = (await db.execute(select(PaperPosition.id).where(
            PaperPosition.status == "open",
            PaperPosition.profile == model_id,
            PaperPosition.symbol == symbol))).first()
        if held:
            return None
        fill = round(verdict["entry"] * (1 + slip), 4)
        # Geometry check AGAINST THE ACTUAL FILL, not the engine's raw entry.
        # Two engines shipped positions whose targets sat behind the entry:
        # breakout measured its target from the zone rather than the entry,
        # and confluence set a stop so tight that the 0.4% slippage carried the
        # fill past target1. Either way `px >= target` was true on the first
        # tick, the position closed at a loss labelled "target2", and the win
        # counter recorded it as a target hit. _mk() only ever checked
        # entry > stop. A position must satisfy stop < fill < t1 <= t2 with a
        # real risk distance, or it is not a trade.
        stop_, t1_, t2_ = verdict["stop"], verdict["target1"], verdict["target2"]
        min_risk = fill * MIN_RISK_FRAC
        bad = None
        if stop_ is None or stop_ >= fill - min_risk:
            bad = f"stop {stop_} not at least {MIN_RISK_FRAC:.2%} below fill {fill}"
        elif t1_ is None or t1_ <= fill:
            bad = f"target1 {t1_} is not above fill {fill}"
        elif t2_ is not None and t2_ < t1_:
            bad = f"target2 {t2_} is below target1 {t1_}"
        if bad:
            await _reject_geometry(db, model_id, symbol, session_date, verdict,
                                   fill, bad)
            return None
        sig = await sigsvc.create_buy_signal(
            db, symbol=symbol, session_date=session_date,
            strategy_version=VERSIONS["strategy_version"],
            price=verdict["entry"], price_source=f"model:{model_id}",
            provider_ts=None,
            score_snapshot={"score": verdict["score"], "setup": verdict["setup"],
                            "model": model_id, "holding": verdict["holding"],
                            "components": {}, "strategy_version":
                            VERSIONS["strategy_version"]},
            evidence_snapshot={"model": model_id, "engine_evidence":
                               verdict["evidence"],
                               "features": {"quote_price": quote_price}},
            signal_type="buy", fingerprint=fp)
        if not sig:
            return None
        sig.profile = model_id
        sig.lifecycle = "ACTIONABLE_BUY"
        sig.versions = VERSIONS
        sig.executable = True
        sig.proposed_entry = verdict["entry"]
        sig.sim_fill_price = fill
        sig.detected_at = now_utc()
        acc = await get_account(db, model_id)
        risk_per_share = max(1e-6, fill - verdict["stop"])
        risk_usd = acc.equity * RISK_PCT_PER_TRADE / 100.0
        size_usd = min(acc.cash * 0.5, max(100.0, risk_usd / risk_per_share * fill))
        pos = PaperPosition(signal_id=sig.id, symbol=symbol, profile=model_id,
                            strategy_version=VERSIONS["strategy_version"],
                            entry_fill=fill, stop=verdict["stop"],
                            target1=verdict["target1"],
                            target2=verdict["target2"], size_usd=round(size_usd, 2),
                            events=[{"t": now_utc().isoformat(), "e": "opened",
                                     "fill": fill, "holding": verdict["holding"],
                                     # kept so R is always measured against the
                                     # risk actually taken, even after the stop
                                     # is moved to breakeven
                                     "original_stop": verdict["stop"]}])
        db.add(pos)
        acc.cash = round(acc.cash - size_usd, 2)
        await db.commit()
        return sig


async def settle_positions(db, quotes: Dict[str, dict],
                           settings: Dict[str, Any]) -> List[dict]:
    """Generic exit engine for model positions: stop, T1 partial + breakeven,
    T2 close, and holding-based time exits. Credits the model's ledger."""
    out = []
    m_now = now_et().hour * 60 + now_et().minute
    # Model-fleet profiles ONLY, keyed off the registry. strategy.paper owns
    # everything NOT in the registry (the scalper profiles), so the two engines
    # partition open positions exactly. The previous "profile != primary" test
    # overlapped accuracy/aggressive/penny: they settled twice, double-counting
    # partials and crediting cash that was never debited.
    open_pos = (await db.execute(select(PaperPosition).where(
        PaperPosition.status == "open",
        PaperPosition.profile.in_(list(MODELS.keys()))))).scalars().all()
    for pos in open_pos:
        q = quotes.get(pos.symbol)
        if not q or not q.get("price"):
            continue
        px = q["price"]
        ev = list(pos.events or [])
        holding = next((e.get("holding") for e in ev if e.get("holding")), "swing")
        slip = {"slippage_pct": settings.get("slippage_pct", 0.4)}
        # R is measured against the ORIGINAL stop. Using the current stop
        # meant that after a move to breakeven (stop == entry) risk collapsed
        # to 1e-9 and a stop-out printed R = -201,000,000.
        orig_stop = next((e.get("original_stop") for e in ev
                          if e.get("e") == "opened" and e.get("original_stop")),
                         None)
        risk = max(pos.entry_fill * MIN_RISK_FRAC,
                   pos.entry_fill - (orig_stop or pos.stop or pos.entry_fill * 0.95))

        pnl_delta = 0.0

        def close(price, reason, frac):
            """Close `frac` of the position. Realised P&L is booked per slice at
            the price that slice actually filled — computing it once at the end
            from exit_fill would price earlier partials at the final fill."""
            nonlocal pnl_delta
            fillp = simulate_sell_price(price, slip) or price
            r_piece = (fillp - pos.entry_fill) / risk * frac
            pos.realized_r = round(pos.realized_r + r_piece, 3)
            pos.remaining_frac = round(pos.remaining_frac - frac, 3)
            pnl_delta += (fillp - pos.entry_fill) / pos.entry_fill * pos.size_usd * frac
            ev.append({"t": now_utc().isoformat(), "e": reason, "px": fillp,
                       "frac": frac,
                       "pnl": round((fillp - pos.entry_fill) / pos.entry_fill
                                    * pos.size_usd * frac, 2)})
            if pos.remaining_frac <= 0.001:
                pos.status = "closed"
                pos.exit_reason = reason
                pos.closed_at = now_utc()
                pos.exit_fill = fillp
            return fillp * frac * (pos.size_usd / pos.entry_fill)

        credited = 0.0
        if pos.stop and px <= pos.stop:
            credited += close(pos.stop, "stop", pos.remaining_frac)
        else:
            took1 = any(e.get("e") == "target1_partial" for e in ev)
            if pos.target1 and px >= pos.target1 and not took1:
                credited += close(pos.target1, "target1_partial",
                                  0.5 * pos.remaining_frac)
                pos.stop = pos.entry_fill
                ev.append({"t": now_utc().isoformat(), "e": "stop_to_breakeven"})
            if pos.status == "open" and pos.target2 and px >= pos.target2:
                credited += close(pos.target2, "target2", pos.remaining_frac)
            # Day-trading sandbox: every model flattens before the close so
            # each trade resolves the same session and its percentage is locked
            # in. Previously only positions tagged "intraday" had a time exit,
            # so swing and position holdings stayed open indefinitely and their
            # results never landed — the competition had nothing to rank.
            day_mode = str(settings.get("day_trading_mode", "on")).lower() != "off"
            eod = day_mode or holding == "intraday"
            if pos.status == "open" and eod and m_now >= 955:
                credited += close(px, "eod_time_exit", pos.remaining_frac)
            # Crypto trades through the equity close, so it needs its own daily
            # boundary or those positions would never resolve.
            if pos.status == "open" and day_mode and pos.symbol.endswith("USD"):
                opened = pos.opened_at
                if opened is not None:
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    if (now_utc() - opened).total_seconds() >= 24 * 3600:
                        credited += close(px, "24h_time_exit", pos.remaining_frac)
        if credited or pos.status == "closed":
            acc = await get_account(db, pos.profile)
            acc.cash = round(acc.cash + credited, 2)
            acc.realized_pnl = round(acc.realized_pnl + pnl_delta, 2)
            if pos.status == "closed":
                acc.trades_closed += 1
                if pos.realized_r > 0.05:
                    acc.wins += 1
        pos.events = ev
        out.append({"model": pos.profile, "symbol": pos.symbol,
                    "status": pos.status, "r": pos.realized_r})
    # mark equity = cash + open position marks
    accs = (await db.execute(select(PaperAccount))).scalars().all()
    for acc in accs:
        mv = 0.0
        for pos in open_pos:
            if pos.profile == acc.model_id and pos.status == "open":
                q = quotes.get(pos.symbol)
                mark = (q or {}).get("price") or pos.entry_fill
                mv += pos.size_usd * (mark / pos.entry_fill) * pos.remaining_frac \
                    + pos.size_usd * (1 - pos.remaining_frac) * 0  # closed frac already in cash
        acc.equity = round(acc.cash + mv, 2)
        acc.max_equity = max(acc.max_equity, acc.equity)
        if acc.max_equity > 0:
            acc.max_drawdown_pct = min(acc.max_drawdown_pct,
                                       round((acc.equity - acc.max_equity)
                                             / acc.max_equity * 100, 2))
    await db.flush()
    return out


async def finalize_noon_outcomes(db, quotes: Dict[str, dict]) -> int:
    """PREMARKET_SCALPER_OUTCOME_V1: +10% touch wins immediately; otherwise the
    noon executable-bid mark decides. Locked rows are immutable."""
    t = now_et()
    if t.hour < 12 or not is_trading_day(t.date()):
        return 0
    today = str(t.date())
    sigs = (await db.execute(select(BuySignal).where(
        BuySignal.session_date == today,
        BuySignal.is_demo == False,  # noqa: E712
        BuySignal.profile.in_(("primary", "", None)) if False else
        BuySignal.lifecycle.in_(("ACTIONABLE_BUY", "EARLY_WATCH",
                                 "QUALIFIED_WATCH"))))).scalars().all()
    n = 0
    for s in sigs:
        if (s.profile or "primary") not in ("primary", "accuracy", "aggressive",
                                            "penny", "premarket_scalper"):
            continue
        done = (await db.execute(select(LockedOutcome.id).where(
            LockedOutcome.signal_id == s.id,
            LockedOutcome.policy == NOON_POLICY))).first()
        if done:
            continue
        call = s.buy_signal_price
        hi = s.post_window_high or s.since_signal_high
        q = quotes.get(s.symbol) or {}
        ref = q.get("bid") or q.get("price") or s.current_live_price
        quality = "LIVE" if q.get("price") else "ESTIMATED"
        if not call:
            cls, ref = "INCOMPLETE", None
        elif hi and hi >= call * 1.10:
            cls = "WIN_10_TOUCH"
        elif ref is None:
            cls, quality = "INCOMPLETE", "ESTIMATED"
        elif ref > call:
            cls = "WIN_NOON_GREEN"
        elif ref < call:
            cls = "LOSS_NOON_RED"
        else:
            cls = "FLAT"
        db.add(LockedOutcome(signal_id=s.id, policy=NOON_POLICY,
                             outcome_class=cls, call_price=call or 0,
                             reference_price=ref, reference_quality=quality))
        db.add(SignalEvent(signal_id=s.id, event_type="outcome_locked",
                           detail={"policy": NOON_POLICY, "class": cls,
                                   "reference": ref}))
        n += 1
    await db.flush()
    return n
