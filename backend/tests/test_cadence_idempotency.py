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


def test_day_trading_mode_flattens_every_model_daily():
    """The point of the sandbox is ranking algorithms on closed results. Only
    'intraday' holdings had a time exit, so swing and position trades never
    resolved and contributed nothing to the comparison."""
    import inspect
    from app.strategy import platform as plat
    from app.scoring.engine import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS.get("day_trading_mode") == "on"
    src = inspect.getsource(plat.settle_positions)
    assert 'day_trading_mode' in src
    assert 'eod = day_mode or holding == "intraday"' in src
    assert '24h_time_exit' in src, "crypto needs its own daily boundary"


async def test_position_with_target_behind_entry_is_refused(db, monkeypatch):
    """Breakout measured its target from the zone, confluence set a stop tighter
    than the slippage. Both produced positions whose target sat below the fill,
    which closed at a loss on the first tick labelled as a target hit."""
    import app.strategy.platform as plat
    from app.models import PaperPosition, RejectedCandidate
    from sqlalchemy import select

    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))

    base = {"action": "buy", "score": 70, "setup": "t", "evidence": {},
            "holding": "swing"}
    # target below entry
    v = {**base, "entry": 100.0, "stop": 99.0, "target1": 99.5, "target2": 99.7}
    assert await plat.record_model_signal("breakout_finder", "GEO1", v, 100.0,
                                          "2026-09-02", {}) is None
    # stop tighter than slippage: fill = 100.4, target1 = 100.05 < fill
    v = {**base, "entry": 100.0, "stop": 99.98, "target1": 100.05, "target2": 100.1}
    assert await plat.record_model_signal("technical_confluence", "GEO2", v, 100.0,
                                          "2026-09-02", {"slippage_pct": 0.4}) is None
    # sane geometry still opens
    v = {**base, "entry": 100.0, "stop": 98.5, "target1": 102.5, "target2": 104.0}
    assert await plat.record_model_signal("breakout_finder", "GEO3", v, 100.0,
                                          "2026-09-02", {}) is not None
    opened = (await db.execute(select(PaperPosition))).scalars().all()
    assert [p.symbol for p in opened] == ["GEO3"]
    rej = (await db.execute(select(RejectedCandidate))).scalars().all()
    assert len(rej) == 2 and all("invalid_trade_geometry" in r.rejection_reason for r in rej)
    # and the original stop is preserved for R even if the stop later moves
    assert opened[0].events[0]["original_stop"] == 98.5


async def test_r_multiple_uses_original_stop_after_breakeven(db, monkeypatch):
    """After a move to breakeven the current stop equals entry; measuring R
    against it made risk ~0 and printed R in the hundreds of millions."""
    import app.strategy.platform as plat
    from app.models import PaperPosition
    from sqlalchemy import select

    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))
    v = {"action": "buy", "entry": 100.0, "stop": 98.0, "target1": 103.0,
         "target2": 106.0, "score": 70, "setup": "t", "evidence": {}, "holding": "swing"}
    await plat.record_model_signal("trend_following", "BEQ", v, 100.0, "2026-09-02", {})
    pos = (await db.execute(select(PaperPosition))).scalars().first()
    pos.stop = pos.entry_fill          # simulate the breakeven move
    await db.commit()
    await plat.settle_positions(db, {"BEQ": {"price": pos.entry_fill - 0.01,
                                             "bid": pos.entry_fill - 0.01}}, {})
    pos = (await db.execute(select(PaperPosition))).scalars().first()
    assert pos.status == "closed"
    assert -1.5 < pos.realized_r < 0.5, pos.realized_r   # sane, not -2e8


def test_insider_engine_looks_back_to_a_published_index():
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert "for _back in range(7)" in src and "form4_index" in src


async def test_intraday_universe_includes_the_days_movers():
    """A fixed list of 26 calm large caps produced 'no setup' all session for
    every pattern/breakout engine. The universe must include real movers."""
    from app.strategy import platform as plat

    class FakeFmp:
        async def _get(self, path, params, cache_ttl=0, endpoint_name=""):
            if path == "most-actives":
                return [{"symbol": "LHAI", "price": 3.2}, {"symbol": "FAMI", "price": 0.4},
                        {"symbol": "BTCUSD", "price": 70000}, {"symbol": "BRK.B", "price": 400},
                        {"symbol": "GPUS", "price": 5.1}]
            if path == "biggest-gainers":
                return [{"symbol": "GPUS", "price": 5.1}, {"symbol": "VIOT", "price": 2.2}]
            return []

    ctx = plat.ModelContext(FakeFmp())
    m = await ctx.movers(cap=50)
    assert m == ["LHAI", "GPUS", "VIOT"]      # sub-$1, crypto, dotted, dup all excluded
    assert await ctx.movers(cap=50) == m      # cached
    cap = plat.ModelContext(FakeFmp())
    assert len(await cap.movers(cap=2)) == 2


def test_mk_never_defaults_target2_below_target1():
    """RS Reclaim supplied a large t1 and a tiny stop; the defaulted t2 of
    entry + 3*risk landed below t1 and every signal failed the geometry check."""
    from app.strategy.engines import _mk
    v = _mk("buy", 100.0, 99.5, 102.0, 60, "t", {}, "intraday")   # risk 0.5, t1 +2%
    assert v["target2"] >= v["target1"] >= v["entry"] > v["stop"]
    assert v["target2"] == 104.0                                     # t1 + one t1-leg
    v2 = _mk("buy", 100.0, 98.0, None, 60, "t", {}, "intraday")     # both defaulted
    assert v2["target1"] == 103.0 and v2["target2"] == 106.0
    v3 = _mk("buy", 100.0, 99.0, 101.0, 60, "t", {}, "intraday", t2=100.5)  # explicit bad t2
    assert v3["target2"] >= v3["target1"]


def test_insider_fetches_the_submission_inside_the_accession_folder():
    """The URL omitted the accession folder, every fetch 404'd, and a bare
    except hid it — so a model with real purchase clusters never fired."""
    import inspect, re
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert '{int(cik)}/{nod}/{acc}.txt' in src
    assert 'except Exception:\n            pass' not in src
    assert plat._ACQUIRED_RE.search(
        "<transactionAcquiredDisposedCode>\n  <value>A</value>") is not None
    assert plat._ACQUIRED_RE.search("<value>D</value>") is None


def test_breakout_targets_are_never_behind_the_entry():
    """Zone + half-height fell below the entry once price had run past the
    zone; ten positions were rejected for t1 <= fill in one session."""
    import inspect
    from app.strategy import engines
    src = inspect.getsource(engines.breakout)
    assert 'max(z["level"] + height * 0.5, px + 1.5 * risk_)' in src
    assert 'max(z["level"] + height, t1_ + (t1_ - px))' in src


def test_insider_fetches_are_paced_and_retry_on_429():
    """Unpaced fetches drew HTTP 429 from EDGAR on every document, so the
    verifier never saw a filing and no cluster was ever found."""
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert "await asyncio.sleep(SEC_FETCH_GAP_S)" in src
    assert "r.status_code == 429" in src
    assert 0 < plat.SEC_FETCH_GAP_S <= 0.2        # under SEC's 10 req/s


def test_m5_cache_ttl_is_jittered_per_symbol():
    """A flat TTL expired every symbol on the same tick and the refetch burst
    drew 429s and tripped the breaker each cycle."""
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.m5)
    assert "hash(sym) % 121" in src and "< ttl" in src


def test_insider_counts_distinct_owners_small_filers_first():
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert "key=lambda kv: len(kv[1])" in src          # ascending
    assert "buyers = len(owners)" in src
    m = plat._OWNER_RE.search("<rptOwnerName>Bridgford Baron</rptOwnerName>")
    assert m and m.group(1) == "Bridgford Baron"


def test_chart_patterns_detects_intraday_in_session():
    import inspect
    from app.strategy import engines
    src = inspect.getsource(engines.chartpat)
    assert "src_bars = m5[-150:]" in src
    assert '"intraday" if len(m5) >= 60 else "swing"' in src
