import copy

from app.scoring.engine import (DEFAULT_SETTINGS, STRATEGY_VERSION, score_candidate,
                                universe_gates)

GOOD = {
    "price": 2.5, "gap_pct": 25, "rvol": 6, "rvol_coverage": 10, "rvol_confidence": 1.0,
    "volume_acceleration": 4, "pm_volume": 900_000, "pm_dollar_volume": 2_000_000,
    "spread_pct": 1.5, "quote_fresh": True, "above_vwap": True, "vwap": 2.4,
    "dist_from_high_pct": 3, "hh_hl": 1.0, "market_cap": 80e6, "float_shares": 9e6,
    "shares_outstanding": 12e6, "has_revenue": True,
    "catalyst": {"direction": "positive", "materiality": 85, "novelty": "new",
                 "confidence": 0.9, "source_url": "https://example.com/pr",
                 "has_original_source": True},
    "filing_context": {"positive_8k": True, "clean_context": True},
}


def s():
    return dict(DEFAULT_SETTINGS)


def test_strong_candidate_buys():
    r = score_candidate(copy.deepcopy(GOOD), s())
    assert r["score"] >= 75
    assert r["buy"] is True
    assert r["strategy_version"] == STRATEGY_VERSION
    assert not r["hard_blocks"]


def test_component_caps_respected():
    r = score_candidate(copy.deepcopy(GOOD), s())
    caps = {"momentum_volume": 30, "catalyst_quality": 25, "sec_filing": 15,
            "liquidity_execution": 10, "price_confirmation": 10, "company_quality": 10}
    for k, cap in caps.items():
        assert 0 <= r["components"][k] <= cap


def test_wide_spread_hard_blocks():
    f = copy.deepcopy(GOOD)
    f["spread_pct"] = 9.0
    r = score_candidate(f, s())
    assert "spread_above_max" in r["hard_blocks"]
    assert r["buy"] is False


def test_stale_quote_blocks():
    f = copy.deepcopy(GOOD)
    f["quote_fresh"] = False
    r = score_candidate(f, s())
    assert "stale_quote" in r["hard_blocks"]
    assert r["buy"] is False


def test_incomplete_volume_blocks():
    f = copy.deepcopy(GOOD)
    f["volume_incomplete"] = True
    r = score_candidate(f, s())
    assert "incomplete_live_volume" in r["hard_blocks"]
    assert r["buy"] is False


def test_no_catalyst_penalty_and_no_buy():
    f = copy.deepcopy(GOOD)
    f["catalyst"] = None
    r = score_candidate(f, s())
    assert any(p["type"] == "no_identifiable_catalyst" for p in r["penalties"])
    assert r["buy"] is False   # catalyst gate fails


def test_low_rvol_fails_gate():
    f = copy.deepcopy(GOOD)
    f["rvol"] = 2.0
    r = score_candidate(f, s())
    assert r["gates"]["rvol_gate"] is False
    assert r["buy"] is False


def test_missing_rvol_fails_gate():
    f = copy.deepcopy(GOOD)
    f["rvol"] = None
    r = score_candidate(f, s())
    assert r["gates"]["rvol_gate"] is False


def test_severe_dilution_hard_blocks():
    f = copy.deepcopy(GOOD)
    f["catalyst"] = dict(f["catalyst"], dilution_detected=True, dilution_severity=90)
    r = score_candidate(f, s())
    assert "severe_actionable_dilution" in r["hard_blocks"]
    assert r["buy"] is False


def test_going_concern_penalty():
    f = copy.deepcopy(GOOD)
    f["catalyst"] = dict(f["catalyst"], going_concern_detected=True)
    r = score_candidate(f, s())
    assert any(p["type"] == "going_concern" and p["points"] == -15 for p in r["penalties"])


def test_reverse_split_penalty():
    f = copy.deepcopy(GOOD)
    f["recent_reverse_split"] = True
    r = score_candidate(f, s())
    assert any(p["type"] == "recent_reverse_split" for p in r["penalties"])


def test_recycled_news_penalty_and_gate():
    f = copy.deepcopy(GOOD)
    f["catalyst"] = dict(f["catalyst"], novelty="recycled")
    r = score_candidate(f, s())
    assert any(p["type"] == "recycled_news" for p in r["penalties"])
    assert r["gates"]["catalyst_gate"] is False


def test_universe_gates_price_range():
    st = s()   # default 0-5 per user config
    assert "price_range" in universe_gates({"price": 12.0, "pm_volume": 1e6,
                                            "pm_dollar_volume": 1e6}, st)
    assert "price_range" not in universe_gates({"price": 3.0, "pm_volume": 1e6,
                                                "pm_dollar_volume": 1e6}, st)


def test_universe_gates_nullable_marketcap():
    st = s()
    st["market_cap_min"] = None
    st["market_cap_max"] = None
    g = universe_gates({"price": 3, "market_cap": None, "pm_volume": 1e6,
                        "pm_dollar_volume": 1e6}, st)
    assert "market_cap_range" not in g   # blank limits => unbounded
    st["market_cap_max"] = 50_000_000    # micro-cap targeting
    g = universe_gates({"price": 3, "market_cap": 80_000_000, "pm_volume": 1e6,
                        "pm_dollar_volume": 1e6}, st)
    assert "market_cap_range" in g


def test_universe_gates_float_and_outstanding():
    st = s()
    st["float_max"] = 20_000_000
    g = universe_gates({"price": 3, "float_shares": 50_000_000, "pm_volume": 1e6,
                        "pm_dollar_volume": 1e6}, st)
    assert "float_range" in g
    st2 = s()
    st2["shares_outstanding_min"] = 1_000_000
    g2 = universe_gates({"price": 3, "shares_outstanding": 500_000, "pm_volume": 1e6,
                         "pm_dollar_volume": 1e6}, st2)
    assert "shares_outstanding_range" in g2


def test_dollar_volume_default_100k():
    st = s()
    assert st["min_pm_dollar_volume"] == 100_000
    g = universe_gates({"price": 3, "pm_volume": 1e6, "pm_dollar_volume": 90_000}, st)
    assert "min_pm_dollar_volume" in g


def test_estimated_rvol_needs_higher_threshold():
    f = copy.deepcopy(GOOD)
    f["rvol"] = 3.5
    f["rvol_estimated"] = True
    r = score_candidate(f, s())          # 3.5 < 3.0*1.5 => gate fails
    assert r["gates"]["rvol_gate"] is False
    f["rvol"] = 5.0                      # >= 4.5 => passes
    r = score_candidate(f, s())
    assert r["gates"]["rvol_gate"] is True


def test_estimated_rvol_disallowed_by_setting():
    f = copy.deepcopy(GOOD)
    f["rvol"] = 9.0
    f["rvol_estimated"] = True
    st = s()
    st["allow_estimated_rvol"] = False
    r = score_candidate(f, st)
    assert r["gates"]["rvol_gate"] is False


def test_explain_present_and_accurate():
    r = score_candidate(copy.deepcopy(GOOD), s())
    ex = {e["key"]: e for e in r["explain"]}
    assert set(ex) >= {"score", "catalyst", "rvol", "pm_volume", "pm_dollar",
                       "fresh", "spread", "confirm", "blocks"}
    assert all(e["pass"] for e in r["explain"])           # strong candidate: all green
    f = copy.deepcopy(GOOD)
    f["rvol"] = 1.0
    ex2 = {e["key"]: e for e in score_candidate(f, s())["explain"]}
    assert ex2["rvol"]["pass"] is False
    assert "1.0x" in ex2["rvol"]["actual"]
