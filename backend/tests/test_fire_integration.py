"""End-to-end BUY firing: scheduler fire path -> immutable signal in DB."""
import pytest
from sqlalchemy import select

from app.models import BuySignal
from app.scheduler import Scheduler
from app.scoring.engine import DEFAULT_SETTINGS, score_candidate

pytestmark = pytest.mark.asyncio

FEATS = {
    "price": 2.5, "gap_pct": 25, "rvol": 6.0, "rvol_estimated": False,
    "volume_acceleration": 4, "pm_volume": 900_000, "pm_dollar_volume": 2_000_000,
    "spread_pct": 1.5, "quote_fresh": True, "above_vwap": True, "vwap": 2.4,
    "dist_from_high_pct": 3, "hh_hl": 1.0, "market_cap": 80e6, "float_shares": 9e6,
    "has_revenue": True, "session_phase": "premarket", "et_minutes": 8 * 60,
    "provider_ts": "2026-09-01T12:00:00+00:00",
    "catalyst": {"direction": "positive", "materiality": 85, "novelty": "new",
                 "confidence": 0.9, "source_url": "https://example.com/pr",
                 "has_original_source": True, "catalyst_type": "contract",
                 "content_hash": "h1"},
    "filing_context": {"positive_8k": True, "clean_context": True},
}


class _Ctx:
    def __init__(self, session):
        self.s = session
    async def __aenter__(self):
        return self.s
    async def __aexit__(self, *a):
        return False


async def test_buy_fires_and_persists(db, monkeypatch):
    import app.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "SessionLocal", lambda: _Ctx(db))
    sched = Scheduler.__new__(Scheduler)
    sched.ctx = type("C", (), {"news_by_symbol": {}})()

    async def _noop_health(level, component, message):
        pass
    sched._health = _noop_health

    result = score_candidate(dict(FEATS), dict(DEFAULT_SETTINGS))
    assert result["buy"] is True
    await Scheduler._maybe_fire_buy(sched, "FIRE", dict(FEATS), result,
                                    FEATS["catalyst"], [], "2026-09-01")
    sig = (await db.execute(select(BuySignal).where(BuySignal.symbol == "FIRE"))).scalar_one()
    assert sig.buy_signal_price == 2.5
    assert sig.status == "active"
    await Scheduler._maybe_fire_buy(sched, "FIRE", dict(FEATS), result,
                                    FEATS["catalyst"], [], "2026-09-01")
    n = len((await db.execute(select(BuySignal))).scalars().all())
    assert n == 1


async def test_stale_quote_never_fires(db, monkeypatch):
    import app.scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "SessionLocal", lambda: _Ctx(db))
    sched = Scheduler.__new__(Scheduler)
    sched.ctx = type("C", (), {"news_by_symbol": {}})()

    async def _noop_health(level, component, message):
        pass
    sched._health = _noop_health
    feats = dict(FEATS, quote_fresh=False)
    result = score_candidate(feats, dict(DEFAULT_SETTINGS))
    await Scheduler._maybe_fire_buy(sched, "STALE", feats, result,
                                    FEATS["catalyst"], [], "2026-09-01")
    n = len((await db.execute(select(BuySignal))).scalars().all())
    assert n == 0


async def test_watch_filters_block_extended_and_catalystless(db, monkeypatch):
    """The loss-reduction rules: no extended spikes, no catalyst-less picks,
    no below-VWAP fades."""
    from app.scoring.engine import DEFAULT_SETTINGS
    s = dict(DEFAULT_SETTINGS)
    # replicate the watch-eligibility expression used by the scheduler
    def eligible(feats, result, catalyst):
        return (s.get("watch_enabled", True)
                and result["score"] >= (s.get("watch_score_min") or 50)
                and not result["hard_blocks"]
                and feats.get("quote_fresh")
                and (feats.get("pm_volume") or 0) > 0
                and (feats.get("gap_pct") is None or
                     feats["gap_pct"] <= (s.get("watch_max_gap_pct") or 100))
                and catalyst is not None
                and catalyst.get("direction") == "positive"
                and catalyst.get("novelty") in ("new", "update")
                and feats.get("above_vwap") is not False)
    from app.scoring.engine import score_candidate
    feats = dict(FEATS)
    result = score_candidate(feats, s)
    cat = FEATS["catalyst"]
    assert eligible(feats, result, cat) is True
    assert eligible(dict(feats, gap_pct=145.0), result, cat) is False       # extended
    assert eligible(feats, result, None) is False                            # no catalyst
    assert eligible(feats, result, dict(cat, novelty="recycled")) is False   # stale story
    assert eligible(dict(feats, above_vwap=False), result, cat) is False     # fading
