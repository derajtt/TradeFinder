"""One-time correction: tracking briefly wrote stale /quote prints into
current/since/post fields. Found-prices were never affected. Per the
append-only policy, each fix is recorded as a correction event."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import BuySignal, SignalEvent  # noqa: E402
from app.providers.fmp import FmpProvider  # noqa: E402
from app.util.timeutil import now_utc  # noqa: E402


async def main():
    fmp = FmpProvider()
    async with SessionLocal() as db:
        sigs = (await db.execute(select(BuySignal).where(
            BuySignal.is_demo == False))).scalars().all()  # noqa: E712
        # keep only the earliest record per symbol/day (watch dedup, retroactive)
        seen = {}
        for s in sorted(sigs, key=lambda x: x.initiated_at):
            key = (s.symbol, s.session_date, s.signal_type)
            if key in seen:
                s.status = "invalidated"
                db.add(SignalEvent(signal_id=s.id, event_type="invalidated",
                                   detail={"reason": "duplicate watch record (pre-dedup)"}))
                continue
            seen[key] = s
        for s in seen.values():
            amt = await fmp.aftermarket_trade(s.symbol)
            q = await fmp.quote_one(s.symbol)
            price, ts = None, None
            for cand in (amt, q):
                if cand and cand.get("price") and cand.get("provider_ts"):
                    if ts is None or cand["provider_ts"] > ts:
                        price, ts = cand["price"], cand["provider_ts"]
            if price is None or (now_utc() - ts).total_seconds() > 900:
                continue
            p0 = s.buy_signal_price
            s.current_live_price = price
            s.current_price_ts = ts
            s.since_signal_high = max(p0, price)
            s.since_signal_low = min(p0, price)
            s.post_window_high = max(p0, price)
            s.post_window_low = min(p0, price)
            db.add(SignalEvent(signal_id=s.id, event_type="correction", detail={
                "reason": "stale /quote prints polluted tracking before the "
                          "extended-hours tape fix; extremes reset to span "
                          "(found, verified current)",
                "verified_current": price, "verified_ts": ts.isoformat()}))
            print(f"corrected {s.signal_type} {s.symbol}: current={price} "
                  f"(found {p0})")
        await db.commit()
    await fmp.close()
    print("done")


asyncio.run(main())
