"""Seed clearly-labeled DEMO records for UI testing.

Demo signals carry is_demo=True end to end: excluded from default signal lists,
performance aggregates, and tracking; badged DEMO in the UI. Never mixed with live.
Usage:  ../.venv/bin/python scripts/seed_demo.py [--clear]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import delete, select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import BuySignal, SignalEvent, SignalPriceCheckpoint  # noqa: E402
from app.signals import service as svc  # noqa: E402

DEMO = [
    dict(symbol="DEMO1", price=2.50, current=3.10, hi=3.25, lo=2.45,
         cat="fda_approval", score=88),
    dict(symbol="DEMO2", price=1.20, current=1.08, hi=1.44, lo=1.05,
         cat="contract", score=79),
    dict(symbol="DEMO3", price=4.10, current=4.10, hi=4.60, lo=3.90,
         cat="earnings_beat", score=91),
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        if "--clear" in sys.argv:
            ids = [s.id for s in (await db.execute(
                select(BuySignal).where(BuySignal.is_demo == True))).scalars()]  # noqa: E712
            if ids:
                await db.execute(delete(SignalPriceCheckpoint)
                                 .where(SignalPriceCheckpoint.signal_id.in_(ids)))
                await db.execute(delete(SignalEvent).where(SignalEvent.signal_id.in_(ids)))
                await db.execute(delete(BuySignal).where(BuySignal.id.in_(ids)))
                await db.commit()
            print(f"cleared {len(ids)} demo signals")
            return
        made = 0
        for d in DEMO:
            sig = await svc.create_buy_signal(
                db, symbol=d["symbol"], session_date="2026-01-02",
                strategy_version="demo", price=d["price"],
                price_source="demo:fixture", provider_ts=None,
                score_snapshot={"score": d["score"], "components": {"momentum_volume": 25},
                                "gates": {}, "penalties": [], "strategy_version": "demo"},
                evidence_snapshot={"catalyst": {"catalyst_type": d["cat"],
                                                "content_hash": f"demo-{d['symbol']}",
                                                "source_url": "https://example.com/demo"}},
                is_demo=True)
            if sig:
                await svc.update_tracking(db, sig, price=d["hi"], provider_ts=None,
                                          day_high=d["hi"], day_low=d["lo"])
                await svc.update_tracking(db, sig, price=d["lo"], provider_ts=None)
                await svc.update_tracking(db, sig, price=d["current"], provider_ts=None)
                await db.commit()
                made += 1
        print(f"seeded {made} demo signals (is_demo=True, badged DEMO in UI)")


asyncio.run(main())
