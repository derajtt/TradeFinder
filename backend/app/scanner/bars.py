"""Self-accumulated minute-bar history.

The current FMP plan does not include historical 1-minute bars (HTTP 402), so the
application builds its own history from live quote observations, exactly as the spec's
fallback requires: store observations, derive per-minute bars from volume deltas,
and never fabricate historical premarket bars.

RVOL confidence model:
  - "measured": per-symbol baselines from >=5 prior sessions of our own stored bars.
  - "estimated": cross-symbol default premarket volume curve vs the symbol's average
    daily volume. Clearly labeled, lower confidence, conservative BUY multiplier.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LiveQuote, MarketBar
from ..util.timeutil import ET, now_utc

# Documented conservative default: cumulative fraction of a full average day's volume
# typically traded by minute-of-day in premarket (minute after midnight ET).
# Used ONLY for the labeled "estimated" RVOL fallback.
_CURVE_POINTS: List[Tuple[int, float]] = [
    (240, 0.000), (300, 0.002), (360, 0.005), (420, 0.010),
    (480, 0.020), (510, 0.030), (540, 0.045), (569, 0.065),
]


def expected_pm_fraction(minute_of_day: int) -> float:
    pts = _CURVE_POINTS
    if minute_of_day <= pts[0][0]:
        return pts[0][1]
    for (m1, f1), (m2, f2) in zip(pts, pts[1:]):
        if m1 <= minute_of_day <= m2:
            if m2 == m1:
                return f2
            return f1 + (f2 - f1) * (minute_of_day - m1) / (m2 - m1)
    return pts[-1][1]


class Accumulator:
    """Per-process observation cache: symbol -> last (ts_et, volume, price)."""

    def __init__(self):
        self.last_obs: Dict[str, Tuple[datetime, float, float]] = {}

    async def record(self, db: AsyncSession, symbol: str, price: Optional[float],
                     volume: Optional[float], provider_ts: Optional[datetime]) -> None:
        """Store the raw observation and derive a minute bar from the volume delta."""
        if price is None or price <= 0:
            return
        ts = (provider_ts or now_utc()).astimezone(ET)
        db.add(LiveQuote(symbol=symbol, provider_ts=provider_ts, price=price,
                         volume=volume, source="fmp_quote"))
        prev = self.last_obs.get(symbol)
        self.last_obs[symbol] = (ts, volume if volume is not None else 0.0, price)
        if prev is None or volume is None:
            return
        prev_ts, prev_vol, prev_price = prev
        if prev_ts.date() != ts.date():
            return  # session boundary; no delta across days
        gap_min = (ts - prev_ts).total_seconds() / 60.0
        if gap_min <= 0 or gap_min > 10:
            return  # stale/duplicate/clock-skew observation
        if volume >= prev_vol:
            dvol = volume - prev_vol
        else:
            dvol = volume  # provider counter reset (e.g., new session)
        minute = ts.hour * 60 + ts.minute
        session_date = str(ts.date())
        existing = (await db.execute(select(MarketBar).where(
            MarketBar.symbol == symbol, MarketBar.session_date == session_date,
            MarketBar.minute_of_day == minute, MarketBar.source == "derived",
        ))).scalar_one_or_none()
        if existing:
            existing.high = max(existing.high, price)
            existing.low = min(existing.low, price)
            existing.close = price
            existing.volume = (existing.volume or 0) + dvol
        else:
            db.add(MarketBar(symbol=symbol, ts_utc=ts.astimezone(now_utc().tzinfo),
                             session_date=session_date, minute_of_day=minute,
                             open=prev_price, high=max(prev_price, price),
                             low=min(prev_price, price), close=price,
                             volume=dvol, source="derived"))
        await db.flush()


async def today_pm_bars(db: AsyncSession, symbol: str, session_date: str) -> List[dict]:
    rows = (await db.execute(select(MarketBar).where(
        MarketBar.symbol == symbol, MarketBar.session_date == session_date,
        MarketBar.minute_of_day >= 240, MarketBar.minute_of_day < 570,
    ).order_by(MarketBar.minute_of_day))).scalars().all()
    return [{"ts_utc": r.ts_utc, "minute_of_day": r.minute_of_day, "open": r.open,
             "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for r in rows]


async def baseline_pm_cum_volumes(db: AsyncSession, symbol: str, session_date: str,
                                  through_minute: int, max_sessions: int = 10) -> List[float]:
    """Cumulative premarket volume through the same minute for prior stored sessions."""
    rows = (await db.execute(select(MarketBar.session_date, MarketBar.volume).where(
        MarketBar.symbol == symbol, MarketBar.session_date != session_date,
        MarketBar.minute_of_day >= 240, MarketBar.minute_of_day <= through_minute,
    ))).all()
    per: Dict[str, float] = {}
    for d, v in rows:
        per[d] = per.get(d, 0.0) + (v or 0.0)
    return [per[d] for d in sorted(per, reverse=True)[:max_sessions]]


async def all_day_bars(db: AsyncSession, symbol: str, days: int = 5) -> List[dict]:
    rows = (await db.execute(select(MarketBar).where(MarketBar.symbol == symbol)
                             .order_by(MarketBar.session_date.desc(),
                                       MarketBar.minute_of_day.desc()).limit(days * 960)
                             )).scalars().all()
    rows = list(reversed(rows))
    return [{"time": int(r.ts_utc.timestamp()) if r.ts_utc else 0, "open": r.open,
             "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}
            for r in rows]


def estimated_rvol(pm_volume: float, avg_daily_volume: Optional[float],
                   minute_of_day: int) -> Optional[float]:
    """Labeled low-confidence estimate vs the cross-symbol default curve."""
    if not avg_daily_volume or avg_daily_volume <= 0 or pm_volume <= 0:
        return None
    frac = expected_pm_fraction(minute_of_day)
    if frac <= 0:
        return None
    return pm_volume / (avg_daily_volume * frac)
