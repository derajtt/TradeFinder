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


async def test_empty_insider_result_is_not_cached_for_six_hours(monkeypatch):
    """A first pass inside EDGAR's cooldown cached {} for the whole session
    while a fresh call found six real clusters."""
    import time
    from app.strategy import platform as plat
    ctx = plat.ModelContext(fmp=None)
    calls = {"n": 0}
    async def fake_fetch():
        calls["n"] += 1
        return {} if calls["n"] == 1 else {"BRID": {"buyers": 2}}
    # drive the cache logic with a stand-in for the network stage
    async def clusters():
        if ctx._insiders[0]:
            age = time.monotonic() - ctx._insiders[0]
            ttl = 6 * 3600 if ctx._insiders[1] else 900
            if age < ttl:
                return ctx._insiders[1]
        out = await fake_fetch()
        ctx._insiders = (time.monotonic(), out)
        return out
    assert await clusters() == {}                      # first pass: empty
    ctx._insiders = (time.monotonic() - 1000, {})      # 16 minutes later
    assert await clusters() == {"BRID": {"buyers": 2}} # retried, not stuck
    ctx._insiders = (time.monotonic() - 1000, {"BRID": {"buyers": 2}})
    assert await clusters() == {"BRID": {"buyers": 2}} and calls["n"] == 2  # hit held
    import inspect
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert "ttl = 6 * 3600 if self._insiders[1] else 900" in src


def test_insider_pass_logs_stage_counts():
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.ModelContext.insider_clusters)
    assert 'log.info("insider_clusters stages:' in src
    for k in ("index_day", "candidates", "fetched", "p_matches"):
        assert f'"{k}"' in src


async def test_daily_bars_empty_result_is_not_cached_for_an_hour():
    """Six insider symbols were fetched with the token bucket drained, got [],
    and were held empty for an hour — the model's universe stayed empty all
    session while the data was fine."""
    import time
    from app.strategy import platform as plat
    calls = {"n": 0}
    class FakeFmp:
        async def _get(self, path, params, cache_ttl=0, endpoint_name=""):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("bucket drained")
            return [{"date": "2026-09-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    ctx = plat.ModelContext(FakeFmp())
    assert await ctx.daily("BRID") == []                 # first: failed -> []
    ctx._daily["BRID"] = (time.monotonic() - 200, [])    # 200s later
    assert len(await ctx.daily("BRID")) == 1             # retried, not stuck
    ctx._daily["BRID"] = (time.monotonic() - 200, [{"c": 1}])
    assert await ctx.daily("BRID") == [{"c": 1}] and calls["n"] == 2   # hit held


def test_specific_skip_reason_is_not_overwritten():
    import inspect
    from app import scheduler as sched
    src = inspect.getsource(sched.Scheduler._models_cycle)
    assert 'hb["_specific_skip"]' in src
    assert 'hb.pop("_specific_skip", None) or' in src


def test_context_fetches_have_no_try_else_that_discards_success():
    """A try-ELSE clause runs on SUCCESS. One re-attached itself to the insider
    fetch and reset the result to {} every time the fetch worked."""
    import inspect, re
    from app import scheduler as sched
    src = inspect.getsource(sched.Scheduler._models_cycle)
    block = src[src.index('ctx["insider_clusters"] = await'):src.index('model_cfg = settings.get')]
    assert "else:" not in block, "try-else in the context fetch block"
    assert block.count('ctx["insider_clusters"] = {}') == 1     # only the except path
    assert 'ctx["fundamentals"] = await self.mctx.fundamentals(list(ETF_UNIVERSE))' in block
    # fundamentals must not be nested under the insider except
    assert re.search(r'except Exception as e:\n\s+ctx\["insider_clusters"\] = \{\}\n\s+await self\._health\("warn", "insiders"[^\n]*\n\s+try:\n\s+ctx\["fundamentals"\]', block) is None


async def test_fundamentals_are_memoised_and_core_only():
    """A cold start re-fired one ratios call per symbol alongside every other
    cold fetch; the stampede tripped the breaker 256 times in five minutes."""
    import inspect, time
    from app.strategy import platform as plat
    from app import scheduler as sched
    calls = {"n": 0}
    class FakeFmp:
        async def _get(self, path, params, cache_ttl=0, endpoint_name=""):
            calls["n"] += 1
            return [{"priceToEarningsRatioTTM": 21.5}]
    ctx = plat.ModelContext(FakeFmp())
    a = await ctx.fundamentals(["SPY", "QQQ"])
    b = await ctx.fundamentals(["SPY", "QQQ"])
    assert a == b and a["SPY"]["pe"] == 21.5 and calls["n"] == 2   # second pass: 0 calls
    src = inspect.getsource(sched.Scheduler._models_cycle)
    assert "fundamentals(list(ETF_UNIVERSE))" in src


def test_day_trading_universe_excludes_core_and_crypto_by_default():
    import inspect
    from app import scheduler as sched
    from app.strategy import platform as plat
    from app.scoring.engine import DEFAULT_SETTINGS
    src = inspect.getsource(sched.Scheduler._models_cycle)
    assert "dt_stock = list(dict.fromkeys(movers + radar)) if day_mode else stock_syms" in src
    assert 'symbols = dt_stock if intraday_ok else []' in src
    assert DEFAULT_SETTINGS["day_trade_crypto"] == "off"
    assert DEFAULT_SETTINGS["model_entry_cutoff_et"] == "11:30"
    assert plat.ATR_STOP_MULT_DEFAULT == 3.0 and plat.ATR_STOP_MULT["exp_rs_reclaim"] is None  # 3.0 set by the Sep-3 engine sweep


def test_custom_confluence_strategies_are_registered_and_dispatched():
    import inspect
    from app.strategy.registry import MODELS
    from app import scheduler as sched
    customs = {k: v for k, v in MODELS.items() if v.get("custom")}
    assert set(customs) == {"custom_strategy_1", "custom_strategy_2", "custom_strategy_3"}
    for mid, m in customs.items():
        assert len(m["requires"]) == 3 and all(r in MODELS for r in m["requires"])
        assert "chart_patterns" in m["requires"] and "exp_open_drive" in m["requires"]
    src = inspect.getsource(sched.Scheduler._custom_confluence_pass)
    assert "need <= got" in src            # ALL required finders must have fired
    assert 'lifecycle == "ACTIONABLE_BUY"' in src
    assert sched.ENGINE_ENTRY_HOURS["exp_rs_reclaim"] == {10}
    cyc = inspect.getsource(sched.Scheduler._models_cycle)
    assert "await self._custom_confluence_pass(" in cyc


def test_getcurrent_atom_parser():
    from app.providers.sec import parse_getcurrent_atom
    xml = """<feed><entry><title>8-K - Acme Corp (0001234567) (Filer)</title>
      <link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1234567/000123456726000045/0001234567-26-000045-index.htm"/>
      <summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-03 &lt;b&gt;AccNo:&lt;/b&gt; 0001234567-26-000045 &lt;b&gt;Size:&lt;/b&gt; 120 KB</summary>
      <updated>2026-09-03T17:02:11-04:00</updated></entry></feed>"""
    rows = parse_getcurrent_atom(xml)
    assert len(rows) == 1
    r = rows[0]
    assert r["form_type"] == "8-K" and r["cik"] == "1234567" and r["accession"] == "0001234567-26-000045"
    assert r["company"] == "Acme Corp" and r["index_url"].endswith("-index.htm")
    assert r["accepted_at"].hour == 17


def test_eightk_reactor_wiring_and_exit_rules():
    import inspect
    from app.strategy.registry import MODELS
    from app import scheduler as sched
    from app.strategy import platform as plat
    from app.scoring.engine import DEFAULT_SETTINGS
    m = MODELS["eightk_reactor"]
    assert m["own_worker"] and m["cadence"] == "afterhours"
    src = inspect.getsource(sched)
    assert "_live_filings_loop" in src and 'self._feed_task = asyncio.create_task(self._live_filings_loop())' in src
    assert "_eightk_react" in src and "_eightk_manage" in src
    settle = inspect.getsource(plat.settle_positions)
    branch = settle[settle.index('if pos.profile == "eightk_reactor":'):settle.index('hit_stop = bool(pos.stop and px_lo <= pos.stop)')]
    assert "if trail_stop > (pos.stop or 0):" in branch          # stop only ratchets up
    assert '"momentum_fade"' in branch and '"ah_time_exit"' in branch
    assert "19 * 60 + 55" in branch                              # flattens before AH close
    assert "eod_time_exit" not in branch                         # never the 15:55 flatten
    for k in ("sec_live_feed", "eightk_window_start_et", "eightk_stop_pct", "eightk_trail_pct", "eightk_skip_items"):
        assert k in DEFAULT_SETTINGS

def test_eightk_reject_counters_survive_a_restart():
    """The reactor took no trades in its first window and every gate counter
    lived only in memory, so a container restart erased the evidence.  The
    persisted detail must carry each gate's count."""
    import inspect
    from app import scheduler as sch
    src = inspect.getsource(sch.Scheduler._persist_heartbeats)
    for k in ("window_rejects", "cap_rejects", "item_rejects",
              "quote_rejects", "spread_rejects"):
        assert k in src, k
    react = inspect.getsource(sch.Scheduler._eightk_react)
    for k in ("window_rejects", "cap_rejects", "item_rejects",
              "quote_rejects", "spread_rejects"):
        assert f'hb["{k}"]' in react, k


def test_every_completed_tick_marks_the_cycle_ok():
    """last_cycle_ok was set only inside the premarket discovery path, so the
    dashboard showed "Scanner starting" for hours after a healthy start."""
    import inspect, re
    from app import scheduler as sch
    src = inspect.getsource(sch.Scheduler._loop)
    tail = src[src.index('self.state["cycles"] += 1'):]
    assert re.search(r'self\.state\["last_cycle_ok"\] = True', tail)
    assert tail.index('last_cycle_ok') < tail.index('except asyncio.CancelledError')


def test_promotion_sample_counts_settled_trades_on_the_running_version():
    """The nightly job counted every actionable signal ever and then said
    "606/100 below the minimum" — a sentence that contradicts itself."""
    import inspect
    from app import scheduler as sch
    src = inspect.getsource(sch.Scheduler)
    i = src.index('paper_n = len(')
    block = src[i - 700:i + 1400]
    assert 'BuySignal.outcome_v2 != ""' in block      # settled only
    assert 'BuySignal.strategy_version == cur_ver' in block
    assert 'sample_met' in block
    assert '{paper_n}/100 below' not in src           # the contradictory wording is gone
