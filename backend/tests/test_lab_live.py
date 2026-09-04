"""Quant Lab live worker: registry discovery, dynamic fleet registration,
causal context construction from 5-minute bars, scheduler wiring, and one
end-to-end paper cycle against an in-memory ledger."""
import inspect
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.lab import live, registry
from app.lab.base import FAMILIES, Signal, StrategyMeta
from app.strategy.registry import MODELS
from app.util.timeutil import ET, now_et


@pytest.fixture
def clean_models():
    """Runtime registrations must not leak into the other suites, which
    assert things about every entry in MODELS."""
    before = set(MODELS)
    yield
    for k in list(MODELS):
        if k not in before:
            MODELS.pop(k, None)


def _meta(sid="s99_test_strategy", **kw):
    base = dict(id=sid, name="Test Strategy", family="momentum", category="test",
                hypothesis="Buyers who are forced in push price for 2.5 bars. "
                           "Falsified if they do not.",
                markets=["stocks", "etf"], timeframes=["5min", "15min", "1hour", "1day"],
                hold="intraday", stop_method="atr", params={"k": 1})
    base.update(kw)
    return StrategyMeta(**base)


def _m5_day(day, start_min=570, n=60, base=10.0):
    """n five-minute bars on `day` from start_min, in ModelContext shape."""
    out = []
    for i in range(n):
        m = start_min + 5 * i
        ts = datetime.combine(day, time(m // 60, m % 60), tzinfo=ET)
        px = base + 0.01 * i
        out.append({"o": px, "h": px + 0.05 + 0.001 * i, "l": px - 0.05, "c": px + 0.01,
                    "v": 1000 + i, "minute_of_day": m, "ts": ts})
    return out


def _daily(day, n=30, include_today=True):
    """n prior-weekday dailies (ascending) plus, optionally, a row for `day`
    itself -- the forming session the EOD endpoint serves intraday."""
    rows, d = [], day - timedelta(days=1)
    while len(rows) < n:
        if d.weekday() < 5:
            rows.append({"o": 9.0, "h": 9.5, "l": 8.5, "c": 9.2, "v": 5000, "date": str(d)})
        d -= timedelta(days=1)
    rows.reverse()
    if include_today:
        rows.append({"o": 10.0, "h": 10.9, "l": 9.9, "c": 10.5, "v": 100, "date": str(day)})
    return rows


# ── registry ─────────────────────────────────────────────────────────────────

def test_registry_discovers_every_module_and_loaded_ones_honour_the_contract():
    names = registry.iter_module_names()
    assert names and names == sorted(names)
    loaded, skipped = registry.load_report()
    # every file on disk is either loaded or reported with a reason -- never silently lost
    assert {s.id for s in loaded} | set(skipped) == set(names), skipped
    assert len(loaded) >= 1
    for s in loaded:
        assert s.meta.id == s.module.rsplit(".", 1)[-1]
        assert callable(s.signal) and s.meta.family in FAMILIES
        assert set(s.meta.markets) <= set(registry.MARKETS) and "options" not in s.meta.markets
        assert set(s.meta.timeframes) <= set(registry.TIMEFRAMES)
        assert s.meta.hold in registry.HOLDS
    assert [s.id for s in registry.load_all()] == sorted(s.id for s in loaded)
    assert set(registry.by_id()) == {s.id for s in loaded}
    # the contract check itself rejects what it should
    bad = _meta(markets=["stocks", "options"], hold="forever")
    errs = registry.contract_errors(bad, None, "s98_other_stem")
    assert any("filename stem" in e for e in errs) and any("markets" in e for e in errs)
    assert any("hold" in e for e in errs) and any("signal" in e for e in errs)
    assert registry.contract_errors(_meta(), lambda c, k: None, "s99_test_strategy") == []


# ── dynamic fleet registration ───────────────────────────────────────────────

def test_fleet_registration_is_idempotent_and_shaped_like_a_model(clean_models):
    meta = _meta()
    mid = live.ensure_fleet_model(meta)
    assert mid == "lab_s99_test_strategy" and mid in MODELS
    spec = MODELS[mid]
    assert spec["name"] == "Test Strategy" and spec["engine"] == "lab"
    assert spec["own_worker"] is True and spec["build"] is True
    assert spec["color"] == "#a3e635" and spec["cadence"] == "intraday"
    assert spec["asset_classes"] == ["stocks", "etf"]
    assert spec["edge"] == "Buyers who are forced in push price for 2.5 bars."
    assert spec["lab_strategy_id"] == "s99_test_strategy"
    n = len(MODELS)
    assert live.ensure_fleet_model(meta) == mid
    assert len(MODELS) == n and MODELS[mid] is spec       # no replacement, no duplicate
    # the fleet's settlement filter is MODELS.keys(); registration is what
    # makes lab positions settle, and the standard risk model applies
    from app.strategy.registry import RISK_MODELS
    assert RISK_MODELS.get(mid, "standard") == "standard"
    assert live.first_sentence("Hypothesis: a 2.6% move sticks. Then more.") == "a 2.6% move sticks."


# ── causal context ───────────────────────────────────────────────────────────

def test_ctx_is_built_causally_from_m5_and_dailies_stop_at_the_prior_session():
    day = date(2026, 9, 3)
    today = str(day)
    m5 = _m5_day(day)
    daily = _daily(day)
    snapshot = ([dict(b) for b in m5], [dict(b) for b in daily])
    kw = dict(symbol="TEST", market="stocks", daily=daily, spy_daily=daily,
              regime="trend_up", today=today)

    full = live.build_ctx(timeframe="5min", m5=m5, **kw)
    assert full["symbol"] == "TEST" and full["market"] == "stocks"
    assert full["timeframe"] == "5min" and full["regime"] == "trend_up"
    assert full["catalyst"] is None and full["session"] == "midday"      # last bar 14:25
    bars = full["bars"]
    assert len(bars) == 60 and set(bars[0]) == {"o", "h", "l", "c", "v", "time", "minute_of_day"}
    assert bars[-1]["time"] == int(m5[-1]["ts"].timestamp()) and bars[-1]["c"] == m5[-1]["c"]
    # every prefix reproduces the full series up to its own last bar, nothing after
    for i in range(0, 60, 7):
        part = live.build_ctx(timeframe="5min", m5=m5[:i + 1], **kw)
        assert part["bars"] == bars[:i + 1]
        assert part["bars"][-1]["time"] == int(m5[i]["ts"].timestamp())
    # dailies: prior sessions only, no forming bar, no minute_of_day
    midnight = int(datetime.combine(day, time(0), tzinfo=ET).timestamp())
    assert len(full["daily"]) == 30 and all(b["time"] < midnight for b in full["daily"])
    assert "minute_of_day" not in full["daily"][0] and full["spy_daily"] == full["daily"]
    assert full["daily"][-1]["time"] < full["daily"][-1]["time"] + 1  # ascending sanity
    assert [b["time"] for b in full["daily"]] == sorted(b["time"] for b in full["daily"])

    # 15-minute resample: three 5-minute bars per bucket, session aligned
    b15 = live.build_ctx(timeframe="15min", m5=m5, **kw)["bars"]
    assert len(b15) == 20 and b15[0]["minute_of_day"] == 570 and b15[1]["minute_of_day"] == 585
    assert b15[0]["o"] == m5[0]["o"] and b15[0]["c"] == m5[2]["c"]
    assert b15[0]["h"] == max(b["h"] for b in m5[:3]) and b15[0]["l"] == min(b["l"] for b in m5[:3])
    assert b15[0]["v"] == sum(b["v"] for b in m5[:3]) and b15[0]["time"] == int(m5[0]["ts"].timestamp())
    for i in range(2, 60, 3):                    # prefixes ending on a bucket boundary
        part = live.build_ctx(timeframe="15min", m5=m5[:i + 1], **kw)["bars"]
        assert part == b15[:(i + 1) // 3]
    forming = live.build_ctx(timeframe="15min", m5=m5[:4], **kw)["bars"]
    assert len(forming) == 2 and forming[-1]["c"] == m5[3]["c"] and forming[-1]["v"] == m5[3]["v"]
    assert forming[0] == b15[0]
    # 1-hour bars anchor to the 09:30 open, not the clock
    b60 = live.build_ctx(timeframe="1hour", m5=m5, **kw)["bars"]
    assert [b["minute_of_day"] for b in b60] == [570, 630, 690, 750, 810]
    assert b60[0]["c"] == m5[11]["c"] and b60[-1]["c"] == m5[-1]["c"]
    # crypto is clock aligned
    c60 = live.build_ctx(timeframe="1hour", m5=m5, symbol="BTCUSD", market="crypto",
                         daily=daily, spy_daily=daily, regime="range", today=today)
    assert c60["session"] == "crypto" and c60["bars"][0]["minute_of_day"] == 540
    # 1day: prior dailies plus a synthetic today bar from regular-session prints
    d1 = live.build_ctx(timeframe="1day", m5=m5, **kw)
    assert len(d1["bars"]) == 31 and d1["bars"][:-1] == d1["daily"]
    tb = d1["bars"][-1]
    assert tb["o"] == m5[0]["o"] and tb["c"] == m5[-1]["c"]
    assert tb["h"] == max(b["h"] for b in m5) and tb["v"] == sum(b["v"] for b in m5)
    assert tb["time"] == midnight and tb["minute_of_day"] == 0
    # premarket-only prints cannot make a daily bar for an equity
    pm = _m5_day(day, start_min=480, n=10)
    assert live.build_ctx(timeframe="1day", m5=pm, **kw) is None
    assert live.build_ctx(timeframe="5min", m5=pm, **kw)["session"] == "premarket"
    assert live.build_ctx(timeframe="4hour", m5=m5, **kw) is None       # not evaluated live
    assert live.build_ctx(timeframe="5min", m5=[], **kw) is None
    # inputs were never mutated
    assert (m5, daily) == snapshot


def test_session_and_regime_vocabulary():
    s = live.session_of
    assert s(300, "stocks") == "premarket" and s(569, "etf") == "premarket"
    assert s(570, "stocks") == "open" and s(629, "stocks") == "open"
    assert s(630, "stocks") == "midday" and s(899, "stocks") == "midday"
    assert s(900, "stocks") == "power_hour" and s(959, "stocks") == "power_hour"
    assert s(960, "stocks") == "afterhours" and s(600, "crypto") == "crypto"
    r = live.lab_regime
    assert r({"state": "trend", "dir": "up"}, []) == "trend_up"
    assert r({"state": "trend", "dir": "down"}, []) == "trend_down"
    falling = [{"c": 300.0 - i} for i in range(250)]           # close far under the 200-day mean
    assert r({"state": "trend", "dir": "down"}, falling) == "bear"
    assert r({"state": "high_risk"}, []) == "high_vol"
    assert r({"state": "range", "atr_pct": 1.2}, []) == "range"
    assert r({"state": "range", "atr_pct": 0.5}, []) == "low_vol"
    assert r({"state": "uncertain"}, []) == "range" and r(None, []) == "range"
    for reg in ({"state": "trend", "dir": "up"}, {"state": "trend", "dir": "down"},
                {"state": "range"}, {"state": "high_risk"}, {"state": "uncertain"}, None):
        assert r(reg, []) in live.LAB_REGIMES
    assert live.timeframes_for(_meta(), "1hour") == ["1hour", "5min", "15min", "1day"]
    assert live.timeframes_for(_meta(timeframes=["4hour"])) == []
    uni = {"stocks": ["AAA", "SPY"], "etf": ["SPY", "QQQ"], "crypto": ["BTCUSD"], "index": []}
    assert live.symbols_for(["etf", "stocks"], uni) == [("AAA", "stocks"), ("SPY", "stocks"),
                                                       ("QQQ", "etf")]


# ── scheduler wiring ─────────────────────────────────────────────────────────

def test_scheduler_runs_the_lab_on_the_reversion_cadence_and_setting_exists():
    from app import scheduler as sched
    from app.scoring.engine import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["lab_live"] == "on"
    assert inspect.iscoroutinefunction(sched.Scheduler._lab_cycle)
    src = inspect.getsource(sched.Scheduler._loop)
    assert src.count("await self._lab_cycle(settings, phase)") == 4
    assert src.count("await self._reversion_cycle(settings, phase)") == 4
    # the lab call sits directly under the reversion call at every site,
    # inside the same modulo gate
    for chunk in src.split("await self._reversion_cycle(settings, phase)")[1:]:
        assert chunk.lstrip().startswith("await self._lab_cycle(settings, phase)")
    body = inspect.getsource(sched.Scheduler._lab_cycle)
    assert "lab_live.run_cycle(self, settings, phase)" in body and "except Exception" in body


# ── end-to-end paper cycle ───────────────────────────────────────────────────

async def test_live_cycle_opens_ledger_position_and_lab_trade_once(db, monkeypatch, clean_models):
    import app.strategy.platform as plat
    from app.models import LabStrategy, LabTrade, PaperAccount, PaperPosition

    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))
    monkeypatch.setattr(live, "SessionLocal", lambda: _Ctx(db))

    meta = _meta("s98_always_fires", markets=["stocks"], timeframes=["5min"], params={"pad": 0.03})
    calls = []

    def sig_fn(ctx, cfg):
        calls.append((ctx["symbol"], ctx["timeframe"], len(ctx["bars"]), ctx["session"],
                      ctx["regime"], cfg["pad"]))
        c = ctx["bars"][-1]["c"]
        pad = cfg["pad"]
        return Signal(direction="long", entry=c, stop=c * (1 - pad), target1=c * (1 + pad),
                      target2=c * (1 + 2 * pad), confidence=61.0,
                      reasons=[f"close {c:.2f} is the session high"],
                      invalidation="a close below the stop", expected_bars=12,
                      features={"close": c})
    fake = [registry.LoadedStrategy(meta=meta, signal=sig_fn, module="tests.fake")]
    monkeypatch.setattr(registry, "load_all", lambda **kw: fake)

    row = LabStrategy(strategy_id=meta.id, name=meta.name, family=meta.family,
                      markets=["stocks"], timeframes=["5min"], stage="PAPER_TRADING",
                      params={"pad": 0.05, "junk": 1})        # optimised override + unknown key
    db.add(row)
    await db.commit()

    today = now_et().date()
    m5 = _m5_day(today)

    class FakeMctx:
        async def m5(self, sym): return m5 if sym == "TEST" else []
        async def daily(self, sym): return _daily(today)
        async def movers(self, cap=50): return ["TEST"]

    class Sched:
        def __init__(self):
            self.mctx = FakeMctx()
            self.ctx = type("C", (), {"fmp": None, "radar_live": []})()
            self.model_health = {}
            self.last_regime = {"state": "trend", "dir": "up"}
            self.health = []
        async def _health(self, level, comp, msg): self.health.append((level, comp, msg))
    sched = Sched()
    settings = {"lab_live": "on", "movers_cap": 10, "model_entry_cutoff_et": "11:30",
                "slippage_pct": 0.4, "day_trading_mode": "on"}

    summary = await live.run_cycle(sched, settings, "premarket")
    mid = "lab_s98_always_fires"
    assert summary["strategies_live"] == [meta.id] and summary["signals"] == 1
    assert mid in MODELS and MODELS[mid]["engine"] == "lab"
    # the strategy saw the contract ctx with the DB override applied and junk dropped
    assert calls and calls[0][:2] == ("TEST", "5min") and calls[0][2] == 60
    assert calls[0][3] == "midday" and calls[0][4] == "trend_up" and calls[0][5] == 0.05
    hb = sched.model_health[mid]
    assert hb["status"] == "LIVE" and hb["signals_this_pass"] == 1 and hb["signals_today"] == 1
    assert hb["stage"] == "PAPER_TRADING" and hb["symbols_with_data"] >= 1
    assert sched.model_health[live.WORKER_HB]["status"] == "LIVE"
    pos = (await db.execute(select(PaperPosition).where(PaperPosition.profile == mid))).scalars().all()
    assert len(pos) == 1 and pos[0].symbol == "TEST" and pos[0].status == "open"
    acc = (await db.execute(select(PaperAccount).where(PaperAccount.model_id == mid))).scalar_one()
    assert acc.starting_cash == 10000.0 and acc.cash < 10000.0          # $10k ledger, debited
    trades = (await db.execute(select(LabTrade).where(LabTrade.strategy_id == meta.id))).scalars().all()
    assert len(trades) == 1
    t = trades[0]
    assert t.cohort == "paper" and t.result == "" and t.direction == "long"
    assert t.symbol == "TEST" and t.timeframe == "5min" and t.regime == "trend_up"
    assert t.session_bucket == "midday" and t.confidence == 61.0
    assert t.features["fleet_model_id"] == mid and t.features["fill"] == pos[0].entry_fill
    assert t.features["fleet_position"]["stop"] == pos[0].stop and t.params == {"pad": 0.05}
    assert t.reasons == ["close %.2f is the session high" % m5[-1]["c"]]

    # second pass, same day: the signal persists but nothing is duplicated
    await live.run_cycle(sched, settings, "premarket")
    assert len((await db.execute(select(PaperPosition).where(
        PaperPosition.profile == mid))).scalars().all()) == 1
    assert len((await db.execute(select(LabTrade).where(
        LabTrade.strategy_id == meta.id))).scalars().all()) == 1
    hb = sched.model_health[mid]
    assert hb["signals_this_pass"] == 0 and hb["signals_today"] == 1 and hb["not_opened"] == 1

    # demoted: no longer evaluated, but stays registered while its book is open
    row.stage = "FAILED"
    await db.commit()
    n_calls = len(calls)
    summary = await live.run_cycle(sched, settings, "premarket")
    assert summary["strategies_live"] == [] and len(calls) == n_calls
    assert mid in MODELS and sched.model_health[mid]["status"] == "WAITING"
    assert "FAILED" in sched.model_health[mid]["skip_reason"]
    assert sched.model_health[live.WORKER_HB]["status"] == "WAITING"

    # the off switch: nothing evaluated, said plainly
    row.stage = "PROMISING"
    await db.commit()
    await live.run_cycle(sched, {**settings, "lab_live": "off"}, "premarket")
    assert len(calls) == n_calls
    assert sched.model_health[live.WORKER_HB]["status"] == "DISABLED"
