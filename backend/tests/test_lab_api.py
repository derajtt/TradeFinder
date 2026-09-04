"""Quant Lab API: leaderboard sort keys, sample-size labels, ensemble family
de-duplication, portfolio family constraint, stage changes, router mount."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import LabRun, LabStrategy, LabTrade
from app.routes import lab_api as api

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


def _strat(sid, family, composite, **kw):
    base = dict(strategy_id=sid, name=sid.upper(), family=family, category="test",
                hypothesis=f"{sid} hypothesis", markets=["stocks"], timeframes=["5min"],
                hold="intraday", stop_method="atr", stage="BACKTESTING",
                composite_score=composite)
    base.update(kw)
    return LabStrategy(**base)


def _run(sid, market, tf, split, metrics, created_at=None, **kw):
    return LabRun(strategy_id=sid, market=market, timeframe=tf, kind="backtest",
                  split=split, metrics=metrics, created_at=created_at or T0,
                  period_start="2025-01-01", period_end="2026-05-31", **kw)


def _trade(sid, symbol, when, r, cohort="backtest", market="stocks", tf="5min",
           direction="long", regime=""):
    return LabTrade(strategy_id=sid, cohort=cohort, split="oos", market=market,
                    symbol=symbol, timeframe=tf, direction=direction, signal_time=when,
                    entry_time=when, exit_time=when + timedelta(minutes=90),
                    entry_price=10.0, stop_price=9.5, target_1=11.0, target_2=12.0,
                    exit_price=10.0 + r * 0.5, r_multiple=r,
                    result="win" if r > 0 else "loss", regime=regime,
                    reasons=["RSI(2) = 4.1 below 10", "close above SMA200 by 3.2%"],
                    invalidation="close below the signal bar low")


async def seed(db):
    db.add_all([
        _strat("a_mom", "momentum", 0.8, best_market="stocks", best_timeframe="5min",
               best_regime="trend_up", worst_regime="bear"),
        _strat("b_mr", "mean_reversion", 0.7, markets=["crypto", "stocks"],
               timeframes=["1hour", "5min"], best_market="crypto", best_timeframe="1hour",
               best_regime="range"),
        _strat("c_mom2", "momentum", 0.6, markets=["etf"], timeframes=["15min"]),
        _strat("d_trend", "trend", 0.5),
    ])
    old = T0 - timedelta(days=30)
    db.add_all([
        # a: an older oos run that must NOT be the headline, then the latest
        _run("a_mom", "stocks", "5min", "oos",
             {"trades": 500, "wins": 250, "expectancy": 0.05, "profit_factor": 1.1,
              "max_drawdown": -0.3, "sharpe": 0.2, "sortino": 0.3, "consistency": 0.4},
             created_at=old),
        _run("a_mom", "stocks", "5min", "oos",
             {"trades": 600, "wins": 360, "expectancy": 0.35, "profit_factor": 1.8,
              "max_drawdown": -0.08, "sharpe": 1.4, "sortino": 2.0, "consistency": 0.7,
              "avg_hold_bars": 9},
             equity_curve=[{"t": "2026-01", "equity": 0.0}, {"t": "2026-02", "equity": 10.0},
                           {"t": "2026-03", "equity": 6.0}, {"t": "2026-04", "equity": 14.0}],
             monthly={"2026-01": 0.0, "2026-02": 10.0, "2026-03": -4.0, "2026-04": 8.0},
             by_regime={"trend_up": {"trades": 400, "expectancy": 0.5},
                        "bear": {"trades": 50, "expectancy": -0.4}}),
        _run("a_mom", "stocks", "5min", "train",
             {"trades": 900, "wins": 540, "expectancy": 0.4, "profit_factor": 1.9,
              "max_drawdown": -0.07, "sharpe": 1.5, "sortino": 2.1, "consistency": 0.75}),
        # b: headline is crypto/1hour (stored best), plus a stocks oos run
        _run("b_mr", "crypto", "1hour", "oos",
             {"trades": 45, "wins": 20, "expectancy": 0.5, "profit_factor": 2.1,
              "max_drawdown": -0.15, "sharpe": 1.1, "sortino": 1.5, "consistency": 0.6},
             equity_curve=[{"t": "2026-01", "equity": 0.0}, {"t": "2026-02", "equity": -4.0},
                           {"t": "2026-03", "equity": 2.0}, {"t": "2026-04", "equity": 6.0}]),
        _run("b_mr", "stocks", "5min", "oos",
             {"trades": 120, "wins": 55, "expectancy": 0.1, "profit_factor": 1.2,
              "max_drawdown": -0.12, "sharpe": 0.6, "sortino": 0.8, "consistency": 0.5}),
        # c: only a validation run, tiny sample
        _run("c_mom2", "etf", "15min", "validation",
             {"trades": 12, "wins": 9, "expectancy": 0.9, "profit_factor": 3.0,
              "max_drawdown": -0.02, "sharpe": 0.5, "sortino": 0.7, "consistency": 0.9}),
        # d: no runs at all
    ])
    await db.commit()


# ── pure helpers ─────────────────────────────────────────────────────────────

async def test_confidence_labels_and_wilson():
    assert api.confidence_label(None) == "VERY LOW"
    assert api.confidence_label(0) == "VERY LOW"
    assert api.confidence_label(29) == "VERY LOW"
    assert api.confidence_label(30) == "LOW"
    assert api.confidence_label(99) == "LOW"
    assert api.confidence_label(100) == "MODERATE"
    assert api.confidence_label(499) == "MODERATE"
    assert api.confidence_label(500) == "HIGH"
    assert api.wilson_lb(0, 0) is None
    wl = api.wilson_lb(360, 600)
    assert 0.55 < wl < 0.57 and wl < 0.6          # below the raw 60% win rate
    assert api.wilson_lb(9, 12) < api.wilson_lb(450, 600)   # same 75%, tiny n penalised


async def test_run_summary_never_invents_numbers():
    r = LabRun(strategy_id="x", market="stocks", timeframe="5min", kind="backtest",
               split="oos", metrics={"trades": 10, "win_rate": 0.6})
    s = api.run_summary(r)
    assert s["n"] == 10 and s["wins"] == 6            # derived from stored win_rate * n
    assert s["expectancy"] is None and s["sharpe"] is None and s["composite"] is None
    assert s["small_sample"] is True and "small sample" in s["warning"]
    empty = api.run_summary(LabRun(strategy_id="x", market="stocks", timeframe="5min",
                                   kind="backtest", split="oos", metrics={}))
    assert empty["n"] is None and empty["wilson_lb"] is None
    assert empty["confidence"] == "VERY LOW" and "no trade count" in empty["warning"]


# ── leaderboard ──────────────────────────────────────────────────────────────

async def test_leaderboard_sort_keys(db):
    await seed(db)

    async def order(sort):
        res = await api.leaderboard(sort=sort, db=db)
        assert res["sort"] == sort
        assert [r["rank"] for r in res["rows"]] == [1, 2, 3, 4]
        return [r["strategy_id"] for r in res["rows"]]

    assert await order("composite") == ["a_mom", "b_mr", "c_mom2", "d_trend"]
    assert await order("expectancy") == ["c_mom2", "b_mr", "a_mom", "d_trend"]
    assert await order("profit_factor") == ["c_mom2", "b_mr", "a_mom", "d_trend"]
    assert await order("max_drawdown") == ["c_mom2", "a_mom", "b_mr", "d_trend"]
    assert await order("sharpe") == ["a_mom", "b_mr", "c_mom2", "d_trend"]
    assert await order("sortino") == ["a_mom", "b_mr", "c_mom2", "d_trend"]
    assert await order("trades") == ["a_mom", "b_mr", "c_mom2", "d_trend"]
    assert await order("consistency") == ["c_mom2", "a_mom", "b_mr", "d_trend"]
    # market sorts use that market's own best-split expectancy; strategies with
    # no run in the market sink to the bottom
    assert (await order("stocks"))[:2] == ["a_mom", "b_mr"]
    assert (await order("crypto"))[0] == "b_mr"
    assert (await order("etf"))[0] == "c_mom2"

    default = await api.leaderboard(db=db)
    assert default["sort"] == "composite"
    with pytest.raises(HTTPException) as e:
        await api.leaderboard(sort="win_rate", db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        await api.leaderboard(sort="bogus", db=db)


async def test_leaderboard_uses_latest_oos_run_and_labels_samples(db):
    await seed(db)
    rows = {r["strategy_id"]: r for r in (await api.leaderboard(db=db))["rows"]}
    a, b, c, d = rows["a_mom"], rows["b_mr"], rows["c_mom2"], rows["d_trend"]
    assert a["expectancy"] == 0.35 and a["trades"] == 600      # newest run, not the old 0.05
    assert a["split"] == "oos" and a["confidence"] == "HIGH" and a["small_sample"] is False
    assert 0.55 < a["wilson_lb"] < 0.57
    assert b["market"] == "crypto" and b["trades"] == 45 and b["confidence"] == "LOW"
    assert c["split"] == "validation" and c["confidence"] == "VERY LOW"
    assert c["small_sample"] is True and "small sample" in c["warning"]
    assert d["trades"] is None and d["confidence"] == "VERY LOW"
    assert d["warning"] == "no result runs stored"
    assert d["wilson_lb"] is None and d["expectancy"] is None


# ── strategies list / detail ────────────────────────────────────────────────

async def test_strategies_list_breakdown_and_options(db):
    await seed(db)
    res = await api.list_strategies(db=db)
    assert res["count"] == 4 and res["options"] == {"status": "not available on data plan"}
    by_id = {s["strategy_id"]: s for s in res["strategies"]}
    a = by_id["a_mom"]
    assert a["by_market"]["options"] == {"status": "not available on data plan"}
    cell = a["by_market"]["stocks"]["timeframes"]["5min"]
    assert cell["oos"]["n"] == 600 and cell["train"]["n"] == 900
    assert cell["validation"] is None and cell["forward"] is None
    b_cell = by_id["b_mr"]["by_market"]["stocks"]["timeframes"]["5min"]
    assert b_cell["oos"]["confidence"] == "MODERATE"                 # 120 trades
    c = by_id["c_mom2"]
    assert any("small sample" in w for w in c["warnings"])
    assert by_id["d_trend"]["headline"] is None and by_id["d_trend"]["runs_count"] == 0


async def test_strategy_detail_and_forward_from_trades(db):
    await seed(db)
    db.add(_trade("a_mom", "XYZ", T0, 1.5, cohort="paper"))
    db.add(_trade("a_mom", "QQQ", T0 + timedelta(days=1), -1.0, cohort="paper"))
    await db.commit()
    d = await api.strategy_detail("a_mom", db=db)
    assert d["hypothesis"] == "a_mom hypothesis" and d["param_grid"] is None
    assert d["headline"]["n"] == 600 and d["monthly"]["2026-02"] == 10.0
    assert d["drawdown_curve"] == [0.0, 0.0, -4.0, 0.0] and d["max_drawdown_from_curve"] == -4.0
    assert d["by_regime"]["trend_up"]["trades"] == 400
    assert len(d["recent_trades"]) == 2
    assert d["recent_trades"][0]["reasons"][0].startswith("RSI(2)")
    assert d["recent_trades"][0]["invalidation"]
    fwd = d["by_market"]["stocks"]["timeframes"]["5min"]["forward"]
    assert fwd["source"] == "lab_trades" and fwd["n"] == 2 and fwd["expectancy"] == 0.25
    assert fwd["avg_hold_minutes"] == 90.0 and fwd["confidence"] == "VERY LOW"
    with pytest.raises(HTTPException) as e:
        await api.strategy_detail("nope", db=db)
    assert e.value.status_code == 404


# ── compare ──────────────────────────────────────────────────────────────────

async def test_compare(db):
    await seed(db)
    res = await api.compare(ids="a_mom,b_mr,ghost", db=db)
    assert [c["strategy_id"] for c in res["strategies"]] == ["a_mom", "b_mr"]
    assert res["missing"] == ["ghost"]
    mc = res["market_compatibility"]
    assert mc["common_markets"] == ["stocks"] and mc["distinct_families"] == 2
    assert mc["same_family_pairs"] == []
    a = res["strategies"][0]
    assert a["n"] == 600 and a["profit_factor"] == 1.8 and a["max_drawdown_from_curve"] == -4.0
    same = await api.compare(ids="a_mom,c_mom2", db=db)
    assert same["market_compatibility"]["same_family_pairs"] == [["a_mom", "c_mom2"]]
    assert same["market_compatibility"]["all_share_a_market"] is False
    with pytest.raises(HTTPException):
        await api.compare(ids="", db=db)
    with pytest.raises(HTTPException) as e:
        await api.compare(ids="ghost", db=db)
    assert e.value.status_code == 404


# ── ensemble ─────────────────────────────────────────────────────────────────

async def test_ensemble_counts_families_not_strategies(db):
    await seed(db)
    day1, day2, day3 = T0, T0 + timedelta(days=1), T0 + timedelta(days=2)
    db.add_all([
        # day1 XYZ: two momentum strategies + one mean-reversion => 2 families, not 3
        _trade("a_mom", "XYZ", day1, 1.0), _trade("c_mom2", "XYZ", day1, 2.0),
        _trade("b_mr", "XYZ", day1 + timedelta(hours=1), 0.5),
        # day2 XYZ: momentum only => single
        _trade("a_mom", "XYZ", day2, -1.0),
        # day3 ABC: paper cohort counts too, live does not
        _trade("b_mr", "ABC", day3, 0.2, cohort="paper"),
        _trade("d_trend", "ABC", day3, 0.4, cohort="live"),
    ])
    await db.commit()
    res = await api.ensemble(db=db)
    assert res["symbol_days_total"] == 3 and res["symbol_days_with_agreement"] == 1
    agree = res["recent_agreements"][0]
    assert agree["families_agreeing"] == 2 and agree["families"] == ["mean_reversion", "momentum"]
    assert set(agree["strategies"]) == {"a_mom", "b_mr", "c_mom2"}
    # momentum's observation for the day is mean(1.0, 2.0) = 1.5; mean_reversion is 0.5
    assert agree["tracked_outcome"] == {"n_families_closed": 2, "mean_r": 1.0}
    assert res["by_agreement"]["2"]["n"] == 2 and res["by_agreement"]["2"]["expectancy"] == 1.0
    assert res["by_agreement"]["1"]["n"] == 2     # day2 momentum (-1.0) and day3 mean_reversion (0.2)
    assert res["by_agreement"]["1"]["expectancy"] == -0.4
    assert res["agreement_improves_expectancy"] is True
    assert res["expectancy_delta_vs_singles"] == 1.4
    assert res["by_agreement"]["3+"]["n"] == 0 and res["by_agreement"]["3+"]["expectancy"] is None


# ── portfolio ────────────────────────────────────────────────────────────────

async def test_portfolio_family_constraint_and_combination(db):
    await seed(db)
    db.add_all([_strat("e_mom3", "momentum", 0.55), _strat("f_mom4", "momentum", 0.52)])
    await db.commit()
    res = await api.portfolio(db=db)
    ids = [l["strategy_id"] for l in res["legs"]]
    assert ids == ["a_mom", "b_mr", "c_mom2", "d_trend"]     # e/f excluded: 2 momentum max
    assert res["families"] == {"momentum": 2, "mean_reversion": 1, "trend": 1}
    weights = {l["strategy_id"]: l["weight"] for l in res["legs"]}
    assert weights["a_mom"] == 0.5 and weights["b_mr"] == 0.5 and weights["c_mom2"] is None
    comb = res["combined"]
    assert comb["aligned_on"] == "time"
    # mean of a=[0,10,6,14] and b=[0,-4,2,6] => [0,3,4,10]; worst drawdown 0
    assert [p["value"] for p in comb["points"]] == [0.0, 3.0, 4.0, 10.0]
    assert comb["max_drawdown"] == 0.0 and comb["final"] == 10.0
    assert res["best_single"]["strategy_id"] == "a_mom"
    assert res["best_single"]["max_drawdown_from_curve"] == -4.0
    assert res["diversification_benefit"] == 4.0
    assert res["note"] is None


async def test_combine_curves_index_alignment():
    comb = api.combine_curves({"x": [(None, 100.0), (None, 110.0), (None, 105.0)],
                               "y": [(None, 50.0), (None, 45.0)]})
    assert comb["aligned_on"] == "index"
    assert [p["value"] for p in comb["points"]] == [0.0, 2.5, 0.0]
    assert comb["max_drawdown"] == -2.5


# ── regimes ──────────────────────────────────────────────────────────────────

def _req(shared):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(shared=shared)))


async def test_regimes_current_from_scheduler_or_trades(db):
    await seed(db)
    sched = SimpleNamespace(last_regime={"state": "trend", "dir": "up", "why": "20>50"})
    res = await api.regimes(request=_req({"scheduler": sched}), db=db)
    assert res["current"]["label"] == "trend_up" and res["current"]["source"] == "scheduler"
    a = next(r for r in res["strategies"] if r["strategy_id"] == "a_mom")
    assert a["favoured_now"] is True and a["avoid_now"] is False
    assert a["in_current_regime"] == {"trades": 400, "expectancy": 0.5}
    b = next(r for r in res["strategies"] if r["strategy_id"] == "b_mr")
    assert b["favoured_now"] is False and b["avoid_now"] is None      # no worst_regime stored

    high = SimpleNamespace(last_regime={"state": "high_risk"})
    assert (await api.regimes(request=_req({"scheduler": high}), db=db))["current"]["label"] == "high_vol"

    # no scheduler and an 'uncertain' controller both fall back to the latest lab trade's regime
    db.add(_trade("a_mom", "XYZ", T0, 1.0, regime="range"))
    db.add(_trade("a_mom", "XYZ", T0 + timedelta(days=1), 1.0, regime="bear"))
    await db.commit()
    res = await api.regimes(request=_req({}), db=db)
    assert res["current"]["label"] == "bear" and res["current"]["source"] == "latest_lab_trade"
    unsure = SimpleNamespace(last_regime={"state": "uncertain"})
    assert (await api.regimes(request=_req({"scheduler": unsure}), db=db))["current"]["label"] == "bear"


# ── stage change ─────────────────────────────────────────────────────────────

async def test_stage_change_appends_reason(db):
    await seed(db)
    with pytest.raises(HTTPException) as e:
        await api.set_stage("a_mom", payload={"stage": "WINNING", "reason": "x"}, db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        await api.set_stage("a_mom", payload={"stage": "VALIDATION"}, db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        await api.set_stage("ghost", payload={"stage": "VALIDATION", "reason": "x"}, db=db)
    assert e.value.status_code == 404

    r1 = await api.set_stage("a_mom", payload={"stage": "validation",
                                               "reason": "oos PF 1.8 over 600 trades"}, db=db)
    assert r1["previous_stage"] == "BACKTESTING" and r1["stage"] == "VALIDATION"
    assert "BACKTESTING -> VALIDATION" in r1["stage_reason"]
    assert "oos PF 1.8 over 600 trades" in r1["stage_reason"]
    r2 = await api.set_stage("a_mom", payload={"stage": "PAPER_TRADING", "reason": "walk-forward held",
                                               "actor": "derajtt"}, db=db)
    lines = r2["stage_reason"].split("\n")
    assert len(lines) == 2 and "VALIDATION -> PAPER_TRADING (derajtt)" in lines[1]
    assert "oos PF 1.8" in lines[0]                       # history preserved
    detail = await api.strategy_detail("a_mom", db=db)
    assert detail["stage"] == "PAPER_TRADING"


# ── router mount ─────────────────────────────────────────────────────────────

async def test_router_mounted_under_api_lab(db):
    import httpx
    from app.db import get_session
    from app.main import app

    async def _override():
        yield db

    await seed(db)
    app.dependency_overrides[get_session] = _override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/lab/leaderboard", params={"sort": "expectancy"})
            assert r.status_code == 200
            body = r.json()
            assert body["rows"][0]["strategy_id"] == "c_mom2"
            assert body["rows"][0]["confidence"] == "VERY LOW"
            r = await c.get("/api/lab/leaderboard", params={"sort": "win_rate"})
            assert r.status_code == 400
            r = await c.get("/api/lab/strategies")
            assert r.status_code == 200 and r.json()["count"] == 4
            r = await c.get("/api/lab/regimes")
            assert r.status_code == 200 and r.json()["current"]["source"] == "none"
            r = await c.post("/api/lab/strategies/d_trend/stage",
                             json={"stage": "FAILED", "reason": "no runs after 60 days"})
            assert r.status_code == 200 and r.json()["stage"] == "FAILED"
    finally:
        app.dependency_overrides.pop(get_session, None)
