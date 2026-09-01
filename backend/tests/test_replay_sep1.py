"""September 1 replay: validates data integrity and decision logic on the real
recorded picks. These are NOT performance claims and the rules were not written
to flip historical winners/losers — they assert structural behavior only."""
from datetime import datetime, timezone

import pytest
from zoneinfo import ZoneInfo

from app.analytics import effective_lifecycle
from app.strategy.catalyst import grade_of, validate_extraction
from app.strategy.gates import evaluate
from app.strategy.tiers import tier_for
from app.strategy.versions import LIFECYCLE

ET = ZoneInfo("America/New_York")
pytestmark = pytest.mark.asyncio

# fixtures reconstructed from the Sep-1 permanent records (digest of stored
# evidence snapshots; times ET, prices as recorded at signal time)
SEP1 = {
    "GPRO": {"price": 1.4971, "gap_pct": 145.0, "float_rotation": 0.038,
             "pm_dollar_volume": 4_010_000, "spread_pct": 0.7,
             "ext_above_vwap_pct": 28.0, "vwap": 1.17, "pm_high": 1.4971,
             "pm_low": 0.95, "hh_hl": 1.0, "participation_bars": 30,
             "found_et": (5, 27)},
    "AMBR": {"price": 1.32, "gap_pct": 25.7, "float_rotation": 2.42,
             "pm_dollar_volume": 1_500_000, "spread_pct": 1.5,
             "ext_above_vwap_pct": -3.0, "vwap": 1.36, "pm_high": 1.52,
             "pm_low": 1.10, "hh_hl": 0.0, "participation_bars": 25,
             "found_et": (6, 12)},
    "AUUD": {"price": 1.1085, "gap_pct": 14.8, "float_rotation": 1.57,
             "pm_dollar_volume": 900_000, "spread_pct": 2.0,
             "ext_above_vwap_pct": 1.0, "vwap": 1.10, "pm_high": 1.20,
             "pm_low": 0.95, "hh_hl": 0.0, "participation_bars": 20,
             "found_et": (7, 11)},
    "NCPL": {"price": 0.8483, "gap_pct": 22.7, "float_rotation": 0.24,
             "pm_dollar_volume": 300_000, "spread_pct": 1.8,
             "ext_above_vwap_pct": 4.0, "vwap": 0.815, "pm_high": 0.86,
             "pm_low": 0.78, "hh_hl": 1.0, "participation_bars": 12,
             "found_et": (7, 28)},
    "SST": {"price": 2.43, "gap_pct": 28.6, "float_rotation": 0.019,
            "pm_dollar_volume": 90_000, "spread_pct": 1.2,
            "ext_above_vwap_pct": 5.0, "vwap": 2.31, "pm_high": 2.48,
            "pm_low": 2.20, "hh_hl": 1.0, "participation_bars": 4,
            "found_et": (7, 52)},
    "RITR": {"price": 0.1093, "gap_pct": 24.6, "float_rotation": 0.28,
             "pm_dollar_volume": 120_000, "spread_pct": 2.4,
             "ext_above_vwap_pct": 6.0, "vwap": 0.103, "pm_high": 0.112,
             "pm_low": 0.09, "hh_hl": 1.0, "participation_bars": 10,
             "found_et": (9, 4)},
    "RDHL": {"price": 0.7965, "gap_pct": 20.5, "float_rotation": 0.053,
             "pm_dollar_volume": 160_000, "spread_pct": 1.0,
             "ext_above_vwap_pct": 3.0, "vwap": 0.773, "pm_high": 0.80,
             "pm_low": 0.74, "hh_hl": 1.0, "participation_bars": 14,
             "found_et": (6, 51)},
    "CVKD": {"price": 1.4201, "gap_pct": -7.8, "float_rotation": 0.011,
             "pm_dollar_volume": 15_000, "spread_pct": 0.7,
             "ext_above_vwap_pct": 1.5, "vwap": 1.40, "pm_high": 1.46,
             "pm_low": 1.38, "hh_hl": 1.0, "participation_bars": 8,
             "found_et": (6, 41)},
}
POS_EXT = validate_extraction({
    "category": "clinical_result", "materiality": "major",
    "credibility": "primary_source", "freshness": "today",
    "counterparties": [], "quantified_value": "", "binding": True, "novel": True,
    "negative_terms": [], "dilution_detected": False,
    "going_concern_detected": False, "headline_appeal": "high",
    "evidence_summary": "x", "source_urls": ["u"], "confidence": 0.9})
NEUTRAL_EXT = validate_extraction({
    "category": "filing_housekeeping", "materiality": "immaterial",
    "credibility": "primary_source", "freshness": "today",
    "counterparties": [], "quantified_value": "", "binding": False, "novel": True,
    "negative_terms": [], "dilution_detected": False,
    "going_concern_detected": False, "headline_appeal": "low",
    "evidence_summary": "routine", "source_urls": ["u"], "confidence": 0.8})
NO_DIL = {"flags": [], "severity": 0, "hard_block": False, "penalty_pts": 0}


def feats(sym, **over):
    f = dict(SEP1[sym])
    f.pop("found_et")
    f.update({"quote_fresh": True, "halted": False, "volume_incomplete": False,
              "data_disagreement": False, "pm_volume": 100_000,
              "dist_from_high_pct": max(0.0, (f["pm_high"] - f["price"])
                                       / f["pm_high"] * 100),
              "bid": f["price"] * 0.995, "ask": f["price"] * 1.005,
              "bid_size": 4000, "ask_size": 4000, "rvol": None,
              "rvol_confidence": 0.0, "volume_acceleration": 2.0})
    f.update(over)
    return f


def at(sym, h=None, m=None):
    hh, mm = SEP1[sym]["found_et"] if h is None else (h, m)
    return datetime(2026, 9, 1, hh, mm, tzinfo=ET)


def test_gpro_extension_and_crowding_recognized():
    v = evaluate(feats("GPRO"), POS_EXT, NO_DIL, {}, at("GPRO", 8, 0))
    failed = {g["gate"] for g in v["gates"] if not g["pass"]}
    assert "not_overextended" in failed          # 28% above VWAP, no base
    assert v["lifecycle"] == "REJECTED"


def test_ambr_auud_over_rotation_recognized():
    for sym in ("AMBR", "AUUD"):
        v = evaluate(feats(sym), POS_EXT, NO_DIL, {}, at(sym, 8, 0))
        failed = {g["gate"] for g in v["gates"] if not g["pass"]}
        assert "not_over_rotated" in failed, sym  # 242% / 157% float rotation
        assert v["lifecycle"] == "REJECTED"


def test_ncpl_filing_not_automatically_positive():
    assert grade_of(NEUTRAL_EXT) == "C"           # housekeeping never a hard catalyst
    v = evaluate(feats("NCPL"), NEUTRAL_EXT, NO_DIL, {}, at("NCPL", 8, 0))
    failed = {g["gate"] for g in v["gates"] if not g["pass"]}
    assert "hard_fresh_catalyst" in failed
    assert v["lifecycle"] == "REJECTED"


def test_soar_catalyst_quality_examined_not_price():
    # a SOAR-like mover with only a neutral filing must fail the catalyst gate
    v = evaluate(feats("RDHL"), NEUTRAL_EXT, NO_DIL, {}, at("RDHL", 8, 0))
    assert any(g["gate"] == "hard_fresh_catalyst" and not g["pass"]
               for g in v["gates"])


def test_sst_weak_participation_represented():
    v = evaluate(feats("SST", participation_bars=2), POS_EXT, NO_DIL, {},
                 at("SST", 8, 0))
    failed = {g["gate"] for g in v["gates"] if not g["pass"]}
    assert "sustained_participation" in failed or "dollar_volume" in failed


def test_ritr_sub_25_cent_tier_protections():
    t = tier_for(SEP1["RITR"]["price"])
    assert t["name"] == "T1_penny"
    assert t["require_grade"] == ("A",)
    # Grade B is not enough in the penny tier
    b_ext = validate_extraction(dict(POS_EXT, category="partnership"))
    v = evaluate(feats("RITR", pm_dollar_volume=120_000), b_ext, NO_DIL, {},
                 at("RITR", 8, 0))
    failed = {g["gate"] for g in v["gates"] if not g["pass"]}
    assert "hard_fresh_catalyst" in failed or "dollar_volume" in failed


def test_pre7_discoveries_become_early_watch_and_judged_after_7():
    # CVKD/RDHL found before 7:00 must be EARLY_WATCH, never actionable then
    for sym in ("CVKD", "RDHL"):
        good = feats(sym, float_rotation=0.06, pm_dollar_volume=300_000,
                     ext_above_vwap_pct=3.0, participation_bars=10)
        v = evaluate(good, POS_EXT, NO_DIL, {}, at(sym))          # pre-7:00
        assert v["lifecycle"] in ("EARLY_WATCH", "REJECTED"), (sym, v["rejection_reason"])
        v2 = evaluate(good, POS_EXT, NO_DIL, {}, at(sym, 7, 5))   # first actionable
        assert v2["gates"][0]["gate"] == "actionable_window"
        assert v2["gates"][0]["pass"] is True


def test_no_entry_after_920():
    good = feats("RDHL", float_rotation=0.06, pm_dollar_volume=300_000)
    v = evaluate(good, POS_EXT, NO_DIL, {}, at("RDHL", 9, 25))
    assert v["lifecycle"] == "EXPIRED"


class _Sig:
    def __init__(self, **kw):
        self.lifecycle = kw.get("lifecycle", "")
        self.status = kw.get("status", "active")
        self.signal_type = kw.get("signal_type", "watch")
        self.initiated_at = kw.get("initiated_at",
                                   datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc))


def test_legacy_mapping_post_open_ambr_excluded_and_dupes_invalidated():
    # post-open AMBR record (14:15 UTC = 10:15 ET) -> never premarket BUY perf
    post_open = _Sig(initiated_at=datetime(2026, 9, 1, 14, 15, tzinfo=timezone.utc))
    assert effective_lifecycle(post_open) == "QUALIFIED_WATCH"
    dup = _Sig(status="invalidated")
    assert effective_lifecycle(dup) == "INVALIDATED"
    early = _Sig(initiated_at=datetime(2026, 9, 1, 9, 27, tzinfo=timezone.utc))  # 5:27 ET
    assert effective_lifecycle(early) == "EARLY_WATCH"
    legacy_buy = _Sig(signal_type="buy")
    assert effective_lifecycle(legacy_buy) == "ACTIONABLE_BUY"
    assert set(LIFECYCLE) >= {"EARLY_WATCH", "QUALIFIED_WATCH", "ACTIONABLE_BUY",
                              "INVALIDATED"}


async def test_canonical_totals_reconcile(db):
    from app.analytics import canonical_report
    from app.scoring.engine import DEFAULT_SETTINGS
    rep = await canonical_report(db, dict(DEFAULT_SETTINGS))
    assert rep["reconciliation"]["equals_total"] is True
    assert rep["actionable_buy_performance"]["closed_trades"] == 0
    assert "No BUY-strategy win rate" in rep["actionable_buy_performance"]["note"]
