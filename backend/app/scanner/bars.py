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

from datetime import timezone, datetime, timedelta
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
        self.last_obs[symbol] = (ts, volume, price)
        if prev is None:
            # First observation this process for the symbol. FMP's extended-session
            # counter is TODAY's cumulative, so carry it in as an opening bar —
            # otherwise volume traded before we started watching is undercounted.
            # Guard against double-count after restarts: only if no bars exist today.
            if volume and volume > 0:
                session_date = str(ts.date())
                minute = ts.hour * 60 + ts.minute
                from sqlalchemy import func as _func
                existing_n = (await db.execute(
                    select(_func.count(MarketBar.id)).where(
                        MarketBar.symbol == symbol,
                        MarketBar.session_date == session_date,
                        MarketBar.source == "derived"))).scalar() or 0
                if existing_n == 0:
                    db.add(MarketBar(symbol=symbol, ts_utc=ts.astimezone(now_utc().tzinfo),
                                     session_date=session_date, minute_of_day=minute,
                                     open=price, high=price, low=price, close=price,
                                     volume=float(volume), source="derived"))
                    await db.flush()
            return
        prev_ts, prev_vol, prev_price = prev
        if prev_ts.date() != ts.date():
            return  # session boundary; no delta across days
        gap_min = (ts - prev_ts).total_seconds() / 60.0
        if gap_min > 10:
            return  # stale/clock-skew observation
        if gap_min <= 0:
            if prev_price == price and (volume is None or volume == prev_vol):
                return  # book genuinely unchanged
            ts = now_utc().astimezone(ET)  # provider ts frozen but data moved
        if volume is None or prev_vol is None:
            dvol = 0.0  # volume unreported this interval: price-only bar, never fabricated
        elif volume >= prev_vol:
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
    # Cap: very early premarket the expected fraction is tiny, which makes the
    # estimate explode for a few thousand shares. 50x is plenty to clear any gate.
    return min(50.0, pm_volume / (avg_daily_volume * frac))


async def seed_pm_bars_from_provider(fmp, symbol: str, session_date: str) -> List[dict]:
    """Today's premarket (04:00–09:30 ET) from FMP's 5-minute extended series.

    Self-accumulated 1-minute prints start EMPTY for every symbol the moment it
    enters the shortlist, so participation, dollar volume, VWAP and the
    high/low structure were all computed from a handful of bars — three of the
    scalper's four binding gates failed >97% of the time for that one reason.
    The provider already has the whole session from 04:00; use it.
    """
    from datetime import datetime as _dt
    from ..util.timeutil import ET
    try:
        rows = await fmp._get("historical-chart/5min",
                              {"symbol": symbol, "from": session_date,
                               "to": session_date, "extended": "true"},
                              cache_ttl=120, endpoint_name="5min-pm-seed")
    except Exception:
        return []
    out = []
    for r in (rows if isinstance(rows, list) else []):
        try:
            ts = _dt.fromisoformat(r["date"]).replace(tzinfo=ET)
        except (KeyError, ValueError):
            continue
        mod = ts.hour * 60 + ts.minute
        if not (240 <= mod < 570):
            continue
        try:
            out.append({"ts_utc": ts.astimezone(timezone.utc), "minute_of_day": mod,
                        "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]),
                        "volume": float(r.get("volume") or 0), "source": "provider_5m"})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda b: b["minute_of_day"])
    return out


def merge_pm_bars(accumulated: List[dict], seeded: List[dict]) -> List[dict]:
    """Own 1-minute prints win for minutes we observed; provider 5-minute bars
    fill everything else. Result is sorted by minute."""
    have = {b["minute_of_day"] for b in accumulated}
    merged = list(accumulated) + [b for b in seeded
                                  if not any(m in have for m in range(b["minute_of_day"],
                                                                      b["minute_of_day"] + 5))]
    merged.sort(key=lambda b: b["minute_of_day"])
    return merged
