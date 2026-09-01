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
    return out
