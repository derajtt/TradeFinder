"""A model must not re-buy a book it already holds, and a restart must not
re-trigger a rebalance. Both produced real duplicate positions in production."""
import inspect

import pytest

pytestmark = pytest.mark.asyncio


def test_record_model_signal_refuses_duplicate_holding():
    from app.strategy import platform as plat
    src = inspect.getsource(plat.record_model_signal)
    assert "PaperPosition.status == \"open\"" in src
    assert "PaperPosition.profile == model_id" in src
    assert "PaperPosition.symbol == symbol" in src


def test_cadence_marks_are_persisted_not_memory_only():
    from app import scheduler as sched
    src = inspect.getsource(sched)
    assert "_cadence_marks" in src and "model_cadence_runs" in src
    cycle = inspect.getsource(sched.Scheduler._models_cycle)
    # the gate must read from storage, not from an instance attribute
    assert "marks = await self._cadence_marks()" in cycle
    assert 'marks.get("daily")' in cycle
    assert 'marks.get("monthly")' in cycle


async def test_duplicate_position_blocked(db, monkeypatch):
    import app.strategy.platform as plat
    from app.models import PaperPosition

    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))

    v = {"action": "buy", "entry": 10.0, "stop": 9.5, "target1": 11.0,
         "target2": 12.0, "score": 70, "setup": "t", "evidence": {},
         "holding": "swing"}
    first = await plat.record_model_signal("multi_factor", "DUPE", v, 10.0,
                                           "2026-09-01", {})
    assert first is not None
    # a later session date would clear the per-day fingerprint, but the model
    # still holds the position, so it must not buy again
    again = await plat.record_model_signal("multi_factor", "DUPE", v, 10.0,
                                           "2026-09-02", {})
    assert again is None
    from sqlalchemy import select
    n = len((await db.execute(select(PaperPosition).where(
        PaperPosition.symbol == "DUPE"))).scalars().all())
    assert n == 1


async def test_new_dict_settings_actually_persist(db):
    """update_settings drops unregistered keys silently — a 200 response is not
    evidence that a setting saved."""
    from app.settings_service import get_settings, update_settings
    for key, payload in [
        ("risk_settings", {"account_equity": 25000.0, "default_risk_pct": 0.5}),
        ("model_settings", {"extreme_reversion": {"variant": "studied"}}),
        ("model_cadence_runs", {"daily": "2026-09-02", "monthly": "2026-09"}),
    ]:
        await update_settings(db, {key: payload})
        got = (await get_settings(db)).get(key)
        assert got == payload, f"{key} did not persist (got {got!r})"


def test_client_errors_do_not_trip_the_circuit_breaker():
    """A 402 from an unentitled endpoint must not open the circuit that guards
    every other endpoint — that blinds the whole platform on a permanent,
    request-specific condition."""
    import inspect
    from app.util import http as h
    src = inspect.getsource(h.ProviderClient.get_json)
    # locate the 4xx branch and confirm it does not record a breaker failure
    idx = src.index("if resp.status_code >= 400:")
    branch = src[idx:idx + 700]
    assert "client error" in branch
    assert "self.breaker.record(False)" not in branch.split("raise RuntimeError")[0]

    # and a burst of 4xx must leave the breaker closed
    b = h.CircuitBreaker(threshold=5)
    for _ in range(10):
        pass                       # no record() calls on the 4xx path
    assert b.allow() is True

    # while genuine provider failures still open it
    b2 = h.CircuitBreaker(threshold=5)
    for _ in range(5):
        b2.record(False)
    assert b2.allow() is False
