"""Immutable BUY-signal accounting. Creation is one transaction; initiation fields
are never rewritten; corrections are append-only events."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BuySignal, SignalEvent, SignalPriceCheckpoint
from ..util.timeutil import ET, now_utc

CHECKPOINTS = [("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60)]


def catalyst_fingerprint(catalyst: Optional[Dict[str, Any]]) -> str:
    if not catalyst:
        return "none"
    basis = f"{catalyst.get('catalyst_type','')}|{catalyst.get('content_hash','')}|{catalyst.get('source_url','')}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


async def create_buy_signal(session: AsyncSession, *, symbol: str, session_date: str,
                            strategy_version: str, price: float, price_source: str,
                            provider_ts: Optional[datetime],
                            score_snapshot: Dict[str, Any],
                            evidence_snapshot: Dict[str, Any],
                            is_demo: bool = False,
                            signal_type: str = "buy") -> Optional[BuySignal]:
    """Returns the new signal, or None if idempotency blocked a duplicate."""
    fp = catalyst_fingerprint(evidence_snapshot.get("catalyst"))
    sig = BuySignal(
        signal_uid=uuid.uuid4().hex,
        symbol=symbol.upper(),
        session_date=session_date,
        strategy_version=strategy_version,
        catalyst_fingerprint=fp,
        initiated_at=now_utc(),
        buy_signal_price=float(price),
        price_source=price_source[:96],
        provider_ts=provider_ts,
        score_snapshot=score_snapshot,
        evidence_snapshot=evidence_snapshot,
        current_live_price=float(price),
        current_price_ts=now_utc(),
        day_high=float(price), day_low=float(price),
        since_signal_high=float(price), since_signal_low=float(price),
        status="active", signal_type=signal_type, is_demo=is_demo,
    )
    session.add(sig)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return None
    session.add(SignalEvent(signal_id=sig.id, event_type="created",
                            detail={"price": price, "source": price_source,
                                    "strategy_version": strategy_version}))
    await session.commit()
    return sig


async def recent_signal_exists(session: AsyncSession, symbol: str, session_date: str,
                               strategy_version: str, fp: str) -> bool:
    q = select(BuySignal.id).where(
        BuySignal.symbol == symbol.upper(),
        BuySignal.session_date == session_date,
        BuySignal.strategy_version == strategy_version,
        BuySignal.catalyst_fingerprint == fp,
    )
    return (await session.execute(q)).first() is not None


async def update_tracking(session: AsyncSession, sig: BuySignal, *,
                          price: Optional[float], provider_ts: Optional[datetime],
                          day_high: Optional[float] = None,
                          day_low: Optional[float] = None) -> None:
    """Updates live-tracking columns only. Never touches initiation fields."""
    if price is not None and price > 0:
        sig.current_live_price = float(price)
        sig.current_price_ts = provider_ts or now_utc()
        sig.since_signal_high = max(sig.since_signal_high or price, price)
        sig.since_signal_low = min(sig.since_signal_low or price, price)
    if day_high is not None and day_high > 0:
        sig.day_high = max(sig.day_high or day_high, day_high)
    if day_low is not None and day_low > 0:
        sig.day_low = min(sig.day_low or day_low, day_low)
    await session.flush()


async def record_due_checkpoints(session: AsyncSession, sig: BuySignal,
                                 price: Optional[float]) -> None:
    if price is None or price <= 0:
        return
    age_min = (now_utc() - sig.initiated_at.replace(tzinfo=timezone.utc)
               if sig.initiated_at.tzinfo is None else now_utc() - sig.initiated_at
               ).total_seconds() / 60.0
    existing = {c.label for c in (await session.execute(
        select(SignalPriceCheckpoint).where(SignalPriceCheckpoint.signal_id == sig.id)
    )).scalars()}
    for label, minutes in CHECKPOINTS:
        if age_min >= minutes and label not in existing:
            pct = (price - sig.buy_signal_price) / sig.buy_signal_price * 100.0
            session.add(SignalPriceCheckpoint(signal_id=sig.id, label=label,
                                              price=price, pct_from_signal=round(pct, 3)))
    await session.flush()


async def record_close_checkpoint(session: AsyncSession, sig: BuySignal,
                                  price: Optional[float]) -> None:
    if price is None or price <= 0:
        return
    existing = (await session.execute(
        select(SignalPriceCheckpoint).where(SignalPriceCheckpoint.signal_id == sig.id,
                                            SignalPriceCheckpoint.label == "close")
    )).first()
    if existing:
        return
    pct = (price - sig.buy_signal_price) / sig.buy_signal_price * 100.0
    session.add(SignalPriceCheckpoint(signal_id=sig.id, label="close",
                                      price=price, pct_from_signal=round(pct, 3)))
    session.add(SignalEvent(signal_id=sig.id, event_type="day_finalized",
                            detail={"close_price": price}))
    await session.flush()


def outcome_window(sig: BuySignal, settings: Dict[str, Any]):
    """The judgment window: starts when the pick becomes tradable (broker
    premarket open, or signal time if later) and lasts early_window_min minutes.
    Returns (start_utc, end_utc)."""
    from datetime import date as _date
    from datetime import datetime as _dt

    from ..util.timeutil import ET
    start = sig.initiated_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    confirm = str(settings.get("buy_confirm_after_et") or "").strip()
    if confirm:
        try:
            hh, mm = (int(x) for x in confirm.split(":"))
            d = _date.fromisoformat(sig.session_date)
            open_utc = _dt(d.year, d.month, d.day, hh, mm, tzinfo=ET).astimezone(timezone.utc)
            if open_utc > start:
                start = open_utc
        except (ValueError, TypeError):
            pass
    minutes = int(settings.get("early_window_min") or 30)
    return start, start + timedelta(minutes=minutes)


def classify_outcome(sig: BuySignal, thr_pct: float = 2.0,
                     window_closed: bool = False) -> str:
    """User rule: judged ONLY on the early tradability window. Up >= thr inside
    it = WIN (locked forever); down >= thr without the up-move first = LOSS;
    window over without either = NEUTRAL. Never re-judged hours later."""
    p0 = sig.buy_signal_price
    if not p0:
        return "pending"
    wh, wl = sig.post_window_high, sig.post_window_low
    if wh is None and wl is None:
        return "pending"           # window not reached / no data collected yet
    thr = max(0.0, float(thr_pct)) / 100.0
    if wh is not None and wh >= p0 * (1 + thr) and (thr > 0 or wh > p0):
        return "win"
    if wl is not None and wl <= p0 * (1 - thr) and (thr > 0 or wl < p0):
        return "loss"
    return "neutral" if window_closed else "pending"


def update_post_window(sig: BuySignal, price: float) -> None:
    """Record extremes for the early judgment window only (caller checks time)."""
    if price and price > 0:
        sig.post_window_high = max(sig.post_window_high or price, price)
        sig.post_window_low = min(sig.post_window_low or price, price)


def signal_metrics(sig: BuySignal) -> Dict[str, Any]:
    """Derived, display-only metrics; initiation price untouched."""
    p0 = sig.buy_signal_price
    cur = sig.current_live_price
    out: Dict[str, Any] = {"change_abs": None, "change_pct": None,
                           "max_gain_pct": None, "max_drawdown_pct": None}
    if p0 and cur:
        out["change_abs"] = round(cur - p0, 4)
        out["change_pct"] = round((cur - p0) / p0 * 100.0, 3)
    if p0 and sig.since_signal_high:
        out["max_gain_pct"] = round((sig.since_signal_high - p0) / p0 * 100.0, 3)
    if p0 and sig.since_signal_low:
        out["max_drawdown_pct"] = round((sig.since_signal_low - p0) / p0 * 100.0, 3)
    out["post7_high"] = sig.post_window_high
    out["post7_low"] = sig.post_window_low
    return out


def metrics_with_outcome(sig: BuySignal, settings: Dict[str, Any]) -> Dict[str, Any]:
    from ..util.timeutil import now_utc as _now
    out = signal_metrics(sig)
    _, end = outcome_window(sig, settings)
    out["outcome"] = classify_outcome(
        sig, thr_pct=float(settings.get("early_win_gain_pct") or 2.0),
        window_closed=_now() > end)
    out["window_end"] = end.isoformat()
    return out
