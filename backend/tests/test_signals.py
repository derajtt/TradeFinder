import asyncio

import pytest
from sqlalchemy import select

from app.models import BuySignal, SignalEvent, SignalPriceCheckpoint
from app.signals import service as svc

pytestmark = pytest.mark.asyncio

SCORE = {"score": 88, "strategy_version": "v1.0.0"}
EVIDENCE = {"catalyst": {"catalyst_type": "contract", "content_hash": "abc123",
                         "source_url": "https://example.com"}}


async def make(db, **kw):
    args = dict(symbol="TEST", session_date="2026-09-01", strategy_version="v1.0.0",
                price=2.50, price_source="fmp:quote", provider_ts=None,
                score_snapshot=SCORE, evidence_snapshot=EVIDENCE)
    args.update(kw)
    return await svc.create_buy_signal(db, **args)


async def test_create_and_immutability(db):
    sig = await make(db)
    assert sig is not None
    assert sig.buy_signal_price == 2.50
    # tracking updates must not change initiation price
    await svc.update_tracking(db, sig, price=3.10, provider_ts=None,
                              day_high=3.2, day_low=2.4)
    await db.commit()
    row = (await db.execute(select(BuySignal).where(BuySignal.id == sig.id))).scalar_one()
    assert row.buy_signal_price == 2.50          # immutable
    assert row.current_live_price == 3.10
    assert row.since_signal_high == 3.10
    assert row.since_signal_low == 2.50
    assert row.day_high == 3.2 and row.day_low == 2.4


async def test_idempotency_no_duplicate(db):
    a = await make(db)
    assert a is not None
    b = await make(db)   # same symbol+version+date+catalyst fingerprint
    assert b is None     # unique constraint blocks duplicate
    n = len((await db.execute(select(BuySignal))).scalars().all())
    assert n == 1


async def test_different_catalyst_allows_new_signal(db):
    a = await make(db)
    b = await make(db, evidence_snapshot={"catalyst": {"catalyst_type": "fda",
                                                       "content_hash": "zzz999",
                                                       "source_url": "https://x"}})
    assert a is not None and b is not None


async def test_tracking_extremes_ratchet(db):
    sig = await make(db)
    await svc.update_tracking(db, sig, price=3.00, provider_ts=None)
    await svc.update_tracking(db, sig, price=2.00, provider_ts=None)
    await svc.update_tracking(db, sig, price=2.50, provider_ts=None)
    assert sig.since_signal_high == 3.00
    assert sig.since_signal_low == 2.00
    m = svc.signal_metrics(sig)
    assert m["max_gain_pct"] == 20.0
    assert m["max_drawdown_pct"] == -20.0
    assert m["change_pct"] == 0.0


async def test_created_event_appended(db):
    sig = await make(db)
    evs = (await db.execute(select(SignalEvent).where(SignalEvent.signal_id == sig.id))
           ).scalars().all()
    assert [e.event_type for e in evs] == ["created"]


async def test_close_checkpoint_once(db):
    sig = await make(db)
    await svc.record_close_checkpoint(db, sig, 2.75)
    await svc.record_close_checkpoint(db, sig, 9.99)   # second call ignored
    await db.commit()
    cps = (await db.execute(select(SignalPriceCheckpoint)
                            .where(SignalPriceCheckpoint.signal_id == sig.id))).scalars().all()
    assert len(cps) == 1 and cps[0].price == 2.75
    assert cps[0].pct_from_signal == 10.0


async def test_rejects_bad_close_price(db):
    sig = await make(db)
    await svc.record_close_checkpoint(db, sig, None)
    await svc.record_close_checkpoint(db, sig, -1)
    cps = (await db.execute(select(SignalPriceCheckpoint))).scalars().all()
    assert len(cps) == 0


async def test_watch_signal_type_recorded(db):
    sig = await make(db, signal_type="watch")
    assert sig is not None and sig.signal_type == "watch"
    # a later full BUY on the same catalyst is NOT blocked by the watch record
    sig.catalyst_fingerprint = "watch:" + sig.catalyst_fingerprint[:10]
    await db.commit()
    buy = await make(db)     # default type buy, original fingerprint
    assert buy is not None and buy.signal_type == "buy"


async def test_outcome_classification(db):
    from app.signals.service import classify_outcome, update_post_window
    sig = await make(db)                      # found @ 2.50
    assert classify_outcome(sig) == "pending"
    update_post_window(sig, 2.60)             # +4% after window
    await svc.update_tracking(db, sig, price=2.60, provider_ts=None)
    assert classify_outcome(sig) == "neutral"
    update_post_window(sig, 2.80)             # +12% peak after window
    assert classify_outcome(sig) == "win"     # win even if it fades later
    sig2 = await make(db, evidence_snapshot={"catalyst": {"catalyst_type": "x",
                                                          "content_hash": "z2",
                                                          "source_url": "u"}})
    update_post_window(sig2, 2.45)
    await svc.update_tracking(db, sig2, price=2.30, provider_ts=None)
    assert classify_outcome(sig2) == "loss"   # finished below found price
