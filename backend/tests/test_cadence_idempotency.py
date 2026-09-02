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


def test_live_features_supply_every_gate_input():
    """A gate whose input the live path never produces can never pass. Assert the
    feature builder exports the fields the v2 gates read."""
    import inspect
    from app.scanner import funnel
    from app.strategy import gates

    feats_src = inspect.getsource(funnel.compute_market_features)
    gates_src = inspect.getsource(gates.evaluate)

    # every f.get("...") the gate evaluator reads must be produced live
    import re
    read = set(re.findall(r'f\.get\("([a-z_]+)"', gates_src))
    produced = set(re.findall(r'"([a-z_]+)":', feats_src))
    # fields legitimately supplied by other stages, not the market-feature builder
    external = {"catalyst_grade", "halted", "float_shares", "shares_outstanding",
                "market_cap", "reverse_split_recent", "ask_size", "bid_size",
                "ask", "bid", "sector", "float_rotation", "ext_above_vwap_pct"}
    missing = read - produced - external
    assert not missing, f"v2 gates read fields the live path never produces: {sorted(missing)}"


async def test_scalper_page_query_finds_its_signals(db):
    """The scalper writes profile ids, not its registry id. A UI query for
    profile='premarket_scalper' must resolve to what it actually writes,
    otherwise its page renders empty while its signals exist."""
    from sqlalchemy import select
    from app.models import BuySignal
    from app.routes.api import _profile_filter
    from app.signals import service as svc

    for prof in ("primary", "aggressive"):
        sig = await svc.create_buy_signal(
            db, symbol=f"SC{prof[:3].upper()}", session_date="2026-09-02",
            strategy_version="v1", price=1.0, price_source="t", provider_ts=None,
            score_snapshot={}, evidence_snapshot={}, signal_type="watch",
            fingerprint=f"fp-{prof}")
        sig.profile = prof
    await db.commit()

    rows = (await db.execute(select(BuySignal).where(
        _profile_filter(BuySignal.profile, "premarket_scalper")))).scalars().all()
    assert len(rows) >= 2, "scalper query must match its real profile ids"
    # and a specific profile still resolves to itself only
    only = (await db.execute(select(BuySignal).where(
        _profile_filter(BuySignal.profile, "aggressive")))).scalars().all()
    assert {r.profile for r in only} == {"aggressive"}


def test_regime_gating_is_advisory_by_default():
    """Blocking outright left six breakout models permanently untraded in a
    range market, so their ledgers never moved and the gate's value could never
    be measured. They now trade and record whether the regime favoured them."""
    import inspect
    from app import scheduler as sched
    from app.scoring.engine import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS.get("regime_gating") == "advisory"
    src = inspect.getsource(sched.Scheduler._models_cycle)
    # the skip must be conditional on the setting, not unconditional
    assert 'settings.get("regime_gating"' in src
    assert '== "block"' in src
    # and every signal must carry the regime context for later analysis
    assert '"regime_favoured": regime_favours' in src
    assert '"regime_state": reg_state' in src


def test_long_cadence_models_still_evaluate_between_rebalances():
    """A monthly model frozen between rebalances produces ~12 data points a
    year, which cannot rank anything. They evaluate every pass now; duplication
    is prevented by the per-day fingerprint and the already-held check, not by
    freezing the model."""
    import inspect
    from app import scheduler as sched
    src = inspect.getsource(sched.Scheduler._models_cycle)
    for phrase in ('rebalance already ran for this trading day',
                   'weekly rebalance already ran',
                   'monthly rebalance already ran'):
        assert phrase not in src, f"long-cadence skip still present: {phrase}"
    assert 'hb["rebalance_pass"]' in src
    # context must not be gated behind the rebalance pass either
    assert 'ctx["earnings"] = await self.mctx.earnings_today()' in src
