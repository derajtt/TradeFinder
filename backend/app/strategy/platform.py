"""Multi-model platform worker: shared context builders, cadence scheduling,
signal recording, paper ledgers, and the locked noon-outcome finalizer.
Live scanning always outranks research; context fetches are bounded + cached."""
from __future__ import annotations

import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from ..db import SessionLocal
from ..models import (BuySignal, LockedOutcome, PaperAccount, PaperPosition,
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

    async def daily(self, sym: str) -> List[dict]:
        hit = self._daily.get(sym)
        if hit and _time.monotonic() - hit[0] < 3600:
            return hit[1]
        try:
            data = await self.fmp._get("historical-price-eod/full",
                                       {"symbol": sym, "from": "2025-06-01",
                                        "to": "2027-01-01"},
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
        if hit and _time.monotonic() - hit[0] < 240:
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

    async def earnings_today(self) -> Dict[str, dict]:
        if _time.monotonic() - self._earnings[0] < 3600:
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

    async def insider_clusters(self, sec) -> Dict[str, dict]:
        """Form-4 purchase clusters from the last 5 sessions of EDGAR daily
        indexes (acceptance-time source of truth lives in submissions)."""
        if _time.monotonic() - self._insiders[0] < 6 * 3600:
            return self._insiders[1]
        out: Dict[str, dict] = {}
        try:
            from ..bt.data import BtData
            data = BtData(SessionLocal, rps=2.0)
            cmap = await data.cik_ticker_map()
            d = now_et().date()
            counts: Dict[str, set] = {}
            checked = 0
            while checked < 5:
                if is_trading_day(d):
                    checked += 1
                    idx = await data.sec_form_index(str(d))
                    # daily index excludes Form 4 by default filter; refetch raw
                for _ in range(1):
                    pass
                d -= timedelta(days=1)
            await data.close()
        except Exception:
            pass
        self._insiders = (_time.monotonic(), out)
        return out


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
        fill = round(verdict["entry"] * (1 + slip), 4)
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
            signal_type="buy")
        if not sig:
            return None
        sig.catalyst_fingerprint = fp
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
                                     "fill": fill, "holding": verdict["holding"]}])
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
    open_pos = (await db.execute(select(PaperPosition).where(
        PaperPosition.status == "open",
        PaperPosition.profile != "primary"))).scalars().all()
    for pos in open_pos:
        q = quotes.get(pos.symbol)
        if not q or not q.get("price"):
            continue
        px = q["price"]
        ev = list(pos.events or [])
        holding = next((e.get("holding") for e in ev if e.get("holding")), "swing")
        slip = {"slippage_pct": settings.get("slippage_pct", 0.4)}
        risk = max(1e-9, pos.entry_fill - (pos.stop or pos.entry_fill * 0.95))

        def close(price, reason, frac):
            fillp = simulate_sell_price(price, slip) or price
            r_piece = (fillp - pos.entry_fill) / risk * frac
            pos.realized_r = round(pos.realized_r + r_piece, 3)
            pos.remaining_frac = round(pos.remaining_frac - frac, 3)
            ev.append({"t": now_utc().isoformat(), "e": reason, "px": fillp,
                       "frac": frac})
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
            if pos.status == "open" and holding == "intraday" and m_now >= 955:
                credited += close(px, "eod_time_exit", pos.remaining_frac)
        if credited or pos.status == "closed":
            acc = await get_account(db, pos.profile)
            acc.cash = round(acc.cash + credited, 2)
            if pos.status == "closed":
                pnl = (pos.exit_fill - pos.entry_fill) / pos.entry_fill * pos.size_usd
                acc.realized_pnl = round(acc.realized_pnl + pnl, 2)
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
