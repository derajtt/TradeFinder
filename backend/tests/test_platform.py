"""Platform tests: engines contract, ledgers, noon-outcome policy."""
import pytest
from datetime import datetime, timezone

from app.strategy.engines import ENGINES, REGIME_ALLOW
from app.strategy.registry import MODELS
from app.strategy import platform as mp
from app.models import BuySignal, LockedOutcome, PaperPosition

pytestmark = pytest.mark.asyncio


def test_registry_engine_coverage():
    """Every registry model must either have an engine the generic dispatcher
    can call, or declare its own worker. Anything else is a decorative card."""
    for mid, meta in MODELS.items():
        if meta["engine"] == "scalper" or meta.get("own_worker"):
            continue
        assert meta["engine"] in ENGINES, mid
        assert meta["engine"] in REGIME_ALLOW, mid
    assert sum(1 for m in MODELS.values() if m.get("experimental")) == 3


def test_own_worker_models_are_actually_dispatched():
    """A model claiming own_worker must have a scheduler cycle that runs it —
    otherwise it is exactly the decorative card this test exists to prevent."""
    import inspect
    from app import scheduler as sched
    src = inspect.getsource(sched)
    for mid, meta in MODELS.items():
        if not meta.get("own_worker"):
            continue
        if meta.get("custom"):
            # dispatched generically by the confluence pass via the `custom` flag
            assert 'meta.get("custom")' in src and "_custom_confluence_pass" in src
            continue
        assert f'"{mid}"' in src, f"{mid} declares own_worker but is not in the scheduler"
    assert "_reversion_cycle" in src
    # and it must be invoked, not merely defined
    assert src.count("await self._reversion_cycle(") >= 1


async def test_account_isolation(db):
    a1 = await mp.get_account(db, "trend_following")
    a2 = await mp.get_account(db, "mean_reversion")
    assert a1.id != a2.id and a1.cash == a2.cash == 10000.0
    again = await mp.get_account(db, "trend_following")
    assert again.id == a1.id                      # idempotent per season


async def test_record_model_signal_and_ledger(db, monkeypatch):
    import app.strategy.platform as plat
    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))
    v = {"action": "buy", "entry": 10.0, "stop": 9.5, "target1": 11.0,
         "target2": 12.0, "score": 70, "setup": "test", "evidence": {},
         "holding": "swing"}
    sig = await plat.record_model_signal("trend_following", "TREND", v, 10.0,
                                         "2026-09-01", {})
    assert sig is not None and sig.profile == "trend_following"
    assert sig.lifecycle == "ACTIONABLE_BUY" and sig.sim_fill_price > 10.0
    dup = await plat.record_model_signal("trend_following", "TREND", v, 10.0,
                                         "2026-09-01", {})
    assert dup is None                            # idempotent per model/day
    acc = await mp.get_account(db, "trend_following")
    assert acc.cash < 10000.0                     # ledger debited
    # stop-out settles the ledger and counts the loss
    out = await plat.settle_positions(db, {"TREND": {"price": 9.4, "bid": 9.4}}, {})
    assert any(o["status"] == "closed" for o in out)
    acc = await mp.get_account(db, "trend_following")
    assert acc.trades_closed == 1 and acc.wins == 0
    assert acc.realized_pnl < 0


async def test_noon_outcome_policy(db, monkeypatch):
    from app.signals import service as svc
    sig = await svc.create_buy_signal(
        db, symbol="NOON", session_date=__import__("datetime").datetime.now(
            __import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat(),
        strategy_version="v", price=2.00, price_source="t", provider_ts=None,
        score_snapshot={}, evidence_snapshot={}, signal_type="buy")
    sig.lifecycle = "ACTIONABLE_BUY"
    sig.profile = "primary"
    sig.post_window_high = 2.25                   # +12.5% touch
    await db.commit()
    import app.strategy.platform as plat
    import app.util.timeutil as tu
    class FakeNow:
        def __init__(self): pass
    monkeypatch.setattr(plat, "now_et", lambda: __import__("datetime").datetime.now(
        __import__("zoneinfo").ZoneInfo("America/New_York")).replace(hour=12, minute=5))
    monkeypatch.setattr(plat, "is_trading_day", lambda d: True)
    n = await plat.finalize_noon_outcomes(db, {"NOON": {"price": 1.90, "bid": 1.90}})
    assert n == 1
    from sqlalchemy import select
    lo = (await db.execute(select(LockedOutcome))).scalars().first()
    assert lo.outcome_class == "WIN_10_TOUCH"     # touch precedence over red noon
    # immutable: second run locks nothing new
    n2 = await plat.finalize_noon_outcomes(db, {"NOON": {"price": 1.90}})
    assert n2 == 0


def test_charting_detects_and_never_repaints():
    from app.strategy.charting import detect
    import random
    rng = random.Random(5)
    bars = []
    px = 10.0
    # build a range with a hard ceiling at ~11 touched 3 times, then break it
    for i in range(60):
        target = 11.0 if i % 12 in (5, 6) else 10.2 + rng.uniform(-0.2, 0.2)
        px = px * 0.6 + target * 0.4
        bars.append({"o": px * 0.995, "h": min(px * 1.01, 11.02),
                     "l": px * 0.985, "c": px, "v": 1000})
    for i in range(6):  # breakout on volume
        px *= 1.02
        bars.append({"o": px * 0.99, "h": px * 1.01, "l": px * 0.985,
                     "c": px, "v": 4000})
    det = detect(bars)
    assert any(z["kind"] == "resistance" for z in det["zones"])
    buys = [s for s in det["signals"] if s["kind"] == "buy_breakout"]
    assert buys, det["signals"]
    # no-repaint: rerunning on a truncated series never yields signals beyond it
    det_trunc = detect(bars[:58])
    assert all(s["i"] < 58 for s in det_trunc["signals"])
    from app.strategy.engines import ENGINES
    assert "chartpat" in ENGINES
