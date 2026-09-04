"""Regression tests for the risk layer and the settlement partition."""
import pytest

from app.risk.engine import (build_trade_plan, circuit_breaker_state,
                             dynamic_risk, portfolio_risk, size_position,
                             RISK_DEFAULTS)
from app.risk.qc import qc_check
from app.risk.roadmap import build_roadmap
from app.strategy.registry import MODELS


def test_position_size_derives_from_stop_distance():
    s = size_position(10000, 1.0, 50.0, 49.0, "long")
    assert s["valid"] and s["quantity"] == 100
    assert s["planned_loss"] == pytest.approx(100.0, abs=0.01)


def test_wider_stop_gives_smaller_position_same_risk():
    tight = size_position(10000, 1.0, 50.0, 49.5, "long")
    wide = size_position(10000, 1.0, 50.0, 48.0, "long")
    assert tight["quantity"] > wide["quantity"]
    assert tight["planned_loss"] == pytest.approx(wide["planned_loss"], abs=1.0)


def test_stop_on_wrong_side_is_rejected():
    assert size_position(10000, 1.0, 50.0, 51.0, "long")["valid"] is False
    assert size_position(10000, 1.0, 50.0, 49.0, "short")["valid"] is False


def test_risk_never_exceeds_configured_max():
    d = dynamic_risk(5.0, [], max_pct=2.0)
    assert d["adjusted_pct"] == 2.0


def test_modifiers_only_reduce():
    d = dynamic_risk(1.0, ["high_volatility", "wide_spread"], 2.0)
    assert d["adjusted_pct"] < 1.0 and d["reduced"]


def test_reward_gate_blocks_low_rr_and_explains():
    p = build_trade_plan({"symbol": "X", "direction": "long", "entry": 100,
                          "stop": 99, "targets": [{"name": "TP1", "price": 100.8}]},
                         {"account_equity": 10000})
    assert p["actionable"] is False
    assert p["status"] == "NO_TRADE_REWARD_TOO_LOW"
    assert p["available_r"] < p["required_r"]


def test_portfolio_ceiling_blocks_new_risk():
    open_pos = [{"symbol": "AAA", "open_risk_dollars": 250, "direction": "long"}]
    p = build_trade_plan({"symbol": "X", "direction": "long", "entry": 100,
                          "stop": 98, "targets": [{"name": "TP1", "price": 106}]},
                         {"account_equity": 10000, "max_total_open_risk_pct": 3.0},
                         open_positions=open_pos)
    assert p["actionable"] is True          # 2.5% used of a 3% ceiling
    assert p["risk"]["applied_risk_pct"] <= 0.5 + 1e-9   # trimmed to the headroom
    open_pos.append({"symbol": "BBB", "open_risk_dollars": 60, "direction": "long"})
    p2 = build_trade_plan({"symbol": "X", "direction": "long", "entry": 100,
                           "stop": 98, "targets": [{"name": "TP1", "price": 106}]},
                          {"account_equity": 10000, "max_total_open_risk_pct": 3.0},
                          open_positions=open_pos)
    assert p2["actionable"] is False and p2["status"] == "NO_TRADE_PORTFOLIO_RISK"


def test_correlated_exposure_trims_risk():
    """Two semis longs already open: a third correlated long is not a third
    independent risk, so the allowance is trimmed to what is left in the group."""
    open_pos = [{"symbol": "NVDA", "open_risk_dollars": 75, "direction": "long"},
                {"symbol": "AMD", "open_risk_dollars": 75, "direction": "long"}]
    p = build_trade_plan({"symbol": "SMH", "direction": "long", "entry": 100,
                          "stop": 98, "targets": [{"name": "TP1", "price": 106}]},
                         {"account_equity": 10000}, open_positions=open_pos)
    assert p["actionable"] is True
    assert p["risk"]["applied_risk_pct"] < 1.0
    assert p["risk"]["reduction_reasons"]


def test_full_correlation_group_blocks_the_trade():
    open_pos = [{"symbol": "NVDA", "open_risk_dollars": 100, "direction": "long"},
                {"symbol": "AMD", "open_risk_dollars": 100, "direction": "long"}]
    p = build_trade_plan({"symbol": "SMH", "direction": "long", "entry": 100,
                          "stop": 98, "targets": [{"name": "TP1", "price": 106}]},
                         {"account_equity": 10000}, open_positions=open_pos)
    assert p["actionable"] is False and p["status"] == "NO_TRADE_CORRELATION"


def test_daily_loss_limit_pauses_entries_but_not_recording():
    cb = circuit_breaker_state(RISK_DEFAULTS, daily_pnl_pct=-3.5)
    assert cb["paused"] is True
    assert cb["paper_recording"] is True     # research data must keep flowing


def test_qc_rejects_inverted_plan():
    bad = {"direction": "long", "entry": 100, "stop": 101,
           "targets": [{"name": "TP1", "price": 99}], "risk": {"quantity": 5}}
    r = qc_check(bad)
    assert r["passed"] is False and len(r["errors"]) >= 2


def test_qc_rejects_stale_data():
    ok = {"direction": "long", "entry": 100, "stop": 99,
          "targets": [{"name": "TP1", "price": 103}], "risk": {"quantity": 5}}
    assert qc_check(ok, data_age_s=60)["passed"] is True
    assert qc_check(ok, data_age_s=5000)["passed"] is False


def test_roadmap_says_do_not_chase_above_no_chase_level():
    p = build_trade_plan({"symbol": "NVDA", "direction": "long", "entry": 139.40,
                          "stop": 137.90, "score": 87,
                          "entry_zone": {"ideal": (139.2, 139.55),
                                         "acceptable": (139.05, 139.7),
                                         "no_chase": 140.05},
                          "targets": [{"name": "TP1", "price": 141.65},
                                      {"name": "TP2", "price": 143.2}]},
                         {"account_equity": 10000})
    assert build_roadmap(p, current_price=140.9)["now"]["action"] == "AVOID"
    assert build_roadmap(p, current_price=139.3)["now"]["action"] == "ENTER"


def test_settlement_engines_partition_open_positions():
    """Every profile must be settled by exactly one engine — never both, never
    neither. The two engines are defined as set complements over the registry."""
    from app.strategy import paper, platform
    import inspect
    plat_src = inspect.getsource(platform.settle_positions)
    paper_src = inspect.getsource(paper.update_positions)
    assert "MODELS.keys()" in plat_src and ".in_(" in plat_src
    assert "MODELS.keys()" in paper_src and ".notin_(" in paper_src
    for pid in ["primary", "accuracy", "aggressive", "penny", "insight_t45"]:
        assert pid not in MODELS, f"{pid} must not be a registry id"


def test_portfolio_risk_uses_fleet_equity_and_worst_account():
    """Open risk from 21 separate $10k ledgers was divided by one $10k account,
    so the page read "26.12% of a 3.0% ceiling" while no single strategy was
    anywhere near its own ceiling."""
    import inspect
    from app.routes import risk_api
    src = inspect.getsource(risk_api.risk_portfolio)
    assert "fleet_equity" in src and "portfolio_risk(pos, fleet_equity)" in src
    assert "by_account" in src and "worst_account" not in src.split("return")[0].split("by_account")[0]
    # headroom is measured against the account closest to its own ceiling
    assert 'headroom = round(ceiling - (worst["open_risk_pct"] if worst else 0.0), 3)' in src


def test_open_risk_ceiling_is_checked_per_account_before_a_fill():
    """Nothing consulted the 3% open-risk ceiling when a fleet model opened a
    position, and four accounts drifted to 5-8% of open risk."""
    import inspect
    from app.strategy import platform as plat
    from app.scoring.engine import DEFAULT_SETTINGS
    src = inspect.getsource(plat.record_model_signal)
    assert "PaperPosition.profile == model_id" in src        # this ledger only
    assert "max_total_open_risk_pct" in src
    assert 'settings.get("portfolio_risk_gating", "advisory")' in src
    assert "_reject_risk(" in src                            # blocks are recorded
    assert "portfolio_risk_warning" in src                   # advisory is visible
    # The field must exist on the model: writing sig.evidence raised
    # AttributeError inside the models cycle and stopped the whole fleet.
    from app.models import BuySignal
    assert hasattr(BuySignal, "evidence_snapshot")
    assert "sig.evidence_snapshot" in src and "sig.evidence " not in src
    assert DEFAULT_SETTINGS["portfolio_risk_gating"] == "advisory"


def test_day_stop_floor_applies_even_without_a_five_minute_atr():
    """The geometry block was gated on atr5 being truthy, so a model firing on
    a symbol with no intraday bars kept the engine's own stop: four positions
    opened at 1.09-2.08% under a 4% floor on Sep 4."""
    import inspect
    from app.strategy import platform as plat
    src = inspect.getsource(plat.record_model_signal)
    i = src.index("day_mode = ")
    block = src[i:i + 3200]
    assert "if day_mode and ATR_STOP_MULT.get(" in block      # atr5 no longer gates it
    assert "atr_width = mult * atr5 if atr5 else 0.0" in block
    assert 'max(atr_width, fill * floor_)' in block
    assert '"day_floor"' in block                              # labelled when ATR is absent


@pytest.mark.asyncio
async def test_daily_loss_breaker_stops_new_entries_and_logs_them(db, monkeypatch):
    """circuit_breaker_state could always pause on a daily loss, but every
    caller passed daily_pnl_pct=0 so it never fired: one model lost 9.93% of
    its account in a session against a 3% limit and kept opening trades."""
    import app.strategy.platform as plat
    from app.models import PaperAccount, PaperPosition, RejectedCandidate
    from sqlalchemy import select
    from datetime import datetime, timezone

    class _Ctx:
        def __init__(self, s): self.s = s
        async def __aenter__(self): return self.s
        async def __aexit__(self, *a): return False
    monkeypatch.setattr(plat, "SessionLocal", lambda: _Ctx(db))

    today = str(plat.now_et().date())
    from app.models import BuySignal
    sig = BuySignal(symbol="LOSS", session_date=today, strategy_version="v2.0.0",
                    initiated_at=datetime.now(timezone.utc), signal_uid="uid-loss",
                    buy_signal_price=100.0, profile="chart_patterns")
    db.add(sig)
    await db.flush()
    acc = PaperAccount(model_id="chart_patterns", season=1, starting_cash=10000.0,
                       cash=10000.0, equity=10000.0, max_equity=10000.0)
    db.add(acc)
    # a closed loser worth -5% of the account, today
    db.add(PaperPosition(signal_id=sig.id, symbol="LOSS", profile="chart_patterns", status="closed",
                         strategy_version="v2.0.0", entry_fill=100.0, stop=96.0,
                         target1=110.0, target2=120.0, size_usd=5000.0,
                         exit_fill=90.0, closed_at=datetime.now(timezone.utc),
                         opened_at=datetime.now(timezone.utc), events=[]))
    await db.commit()

    pct = await plat._daily_realized_pct(db, "chart_patterns", today)
    assert pct < -3.0

    v = {"action": "buy", "entry": 100.0, "stop": 95.0, "target1": 110.0,
         "target2": 120.0, "score": 70, "setup": "t", "evidence": {},
         "holding": "intraday"}
    assert await plat.record_model_signal("chart_patterns", "NEW1", v, 100.0,
                                          today, {}) is None
    rej = (await db.execute(select(RejectedCandidate))).scalars().all()
    assert any("daily loss" in r.rejection_reason for r in rej)

    # turning it off restores the old behaviour
    assert await plat.record_model_signal("chart_patterns", "NEW2", v, 100.0,
                                          today, {"daily_loss_breaker": "off"}) is not None
