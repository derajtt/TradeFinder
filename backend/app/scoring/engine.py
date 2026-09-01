"""Deterministic, versioned scoring engine. AI output is one *input*;
it never decides BUY. Every returned dict stores all inputs for the snapshot."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STRATEGY_VERSION = "v1.0.0"

# ---- default thresholds (Settings can change them; changes apply prospectively) ----
DEFAULT_SETTINGS: Dict[str, Any] = {
    "price_min": 0.0,            # user default: $0 – $5
    "price_max": 5.0,
    "market_cap_min": 20_000_000,   # nullable: None => no limit
    "market_cap_max": 5_000_000_000,
    "float_min": None,
    "float_max": None,
    "shares_outstanding_min": None,
    "shares_outstanding_max": None,
    "min_pm_volume": 50_000,
    "min_pm_dollar_volume": 100_000,   # user default: $100k
    "max_spread_pct": 5.0,
    "preferred_spread_pct": 3.0,
    "min_rvol_for_buy": 3.0,
    "allow_estimated_rvol": True,
    "est_rvol_buy_multiplier": 1.5,
    "min_score_for_buy": 75,
    "min_catalyst_confidence": 0.6,
    "max_extension_from_pm_high_pct": 25.0,
    "quote_freshness_sec": 120,
    "scan_interval_sec": 60,
    "enrich_top_n": 20,
    "reentry_cooldown_min": 60,
    "include_otc": False,
    "momentum_only_mode": False,   # separately-tested strategy, off by default
    "openai_monthly_budget_usd": 25.0,
    "paused": False,
}

NULLABLE_KEYS = {"market_cap_min", "market_cap_max", "float_min", "float_max",
                 "shares_outstanding_min", "shares_outstanding_max"}


def _in_range(value: Optional[float], lo: Optional[float], hi: Optional[float]) -> bool:
    """None limit = unbounded. None value passes only if both limits are None."""
    if value is None:
        return lo is None and hi is None
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def universe_gates(f: Dict[str, Any], s: Dict[str, Any]) -> List[str]:
    """Returns list of failed gate names (empty = passes)."""
    failed = []
    if not _in_range(f.get("price"), s.get("price_min"), s.get("price_max")):
        failed.append("price_range")
    if not _in_range(f.get("market_cap"), s.get("market_cap_min"), s.get("market_cap_max")):
        failed.append("market_cap_range")
    if not _in_range(f.get("float_shares"), s.get("float_min"), s.get("float_max")):
        failed.append("float_range")
    if not _in_range(f.get("shares_outstanding"), s.get("shares_outstanding_min"),
                     s.get("shares_outstanding_max")):
        failed.append("shares_outstanding_range")
    pm_vol = f.get("pm_volume")
    if pm_vol is None or pm_vol < (s.get("min_pm_volume") or 0):
        failed.append("min_pm_volume")
    pm_dv = f.get("pm_dollar_volume")
    if pm_dv is None or pm_dv < (s.get("min_pm_dollar_volume") or 0):
        failed.append("min_pm_dollar_volume")
    return failed


def score_candidate(f: Dict[str, Any], s: Dict[str, Any]) -> Dict[str, Any]:
    """f: feature dict (market + catalyst + filing + quality inputs).
    Returns {score, components, penalties, hard_blocks, gates, buy}."""
    comp: Dict[str, float] = {}
    penalties: List[Dict[str, Any]] = []
    hard_blocks: List[str] = []

    # ---- Momentum & volume (30) ----
    pts = 0.0
    rvol = f.get("rvol")
    if rvol is not None:
        if rvol >= 10: pts += 12
        elif rvol >= 5: pts += 9
        elif rvol >= 3: pts += 6
        elif rvol >= 2: pts += 3
        if f.get("rvol_estimated"):
            pts *= 0.7  # lower-confidence estimated baseline
    accel = f.get("volume_acceleration")
    if accel is not None:
        if accel >= 3: pts += 6
        elif accel >= 1.5: pts += 3
    gap = f.get("gap_pct")
    if gap is not None:
        if 10 <= gap <= 80: pts += 8
        elif 5 <= gap < 10: pts += 5
        elif gap > 80: pts += 3   # extreme extension scores less
        elif gap >= 3: pts += 2
    if f.get("hh_hl") == 1.0:
        pts += 4
    comp["momentum_volume"] = min(30.0, pts)

    # ---- Catalyst quality (25) ----
    pts = 0.0
    cat = f.get("catalyst") or {}
    direction = cat.get("direction")
    materiality = cat.get("materiality") or 0
    novelty = cat.get("novelty")
    confidence = cat.get("confidence") or 0.0
    if direction == "positive" and novelty in ("new", "update"):
        pts += materiality * 0.18          # up to 18
        pts += confidence * 5              # up to 5
        if cat.get("has_original_source"):
            pts += 2
    elif direction == "mixed":
        pts += materiality * 0.06
    comp["catalyst_quality"] = min(25.0, pts)

    # ---- SEC filing (15) ----
    pts = 0.0
    filing = f.get("filing_context") or {}
    if filing.get("positive_8k"):
        pts += 10
    if filing.get("clean_context"):
        pts += 5
    if filing.get("insider_buying"):
        pts += 3
    comp["sec_filing"] = min(15.0, pts)

    # ---- Liquidity / execution (10) ----
    pts = 0.0
    dv = f.get("pm_dollar_volume") or 0
    if dv >= 5_000_000: pts += 5
    elif dv >= 1_000_000: pts += 4
    elif dv >= 250_000: pts += 2.5
    elif dv >= 100_000: pts += 1.5
    spread = f.get("spread_pct")
    if spread is not None:
        if spread <= (s.get("preferred_spread_pct") or 3): pts += 3
        elif spread <= (s.get("max_spread_pct") or 5): pts += 1.5
    else:
        pts += 1  # spread unknown: neutral-small credit, freshness gate still applies
    if f.get("quote_fresh"):
        pts += 2
    comp["liquidity_execution"] = min(10.0, pts)

    # ---- Price confirmation (10) ----
    pts = 0.0
    above_vwap = f.get("above_vwap")
    if above_vwap is True: pts += 5
    elif above_vwap is None: pts += 2
    dist = f.get("dist_from_high_pct")
    if dist is not None:
        if dist <= 5: pts += 5
        elif dist <= 12: pts += 3
        elif dist <= 20: pts += 1
    comp["price_confirmation"] = min(10.0, pts)

    # ---- Company quality (10) ----
    pts = 0.0
    mc = f.get("market_cap")
    if mc is not None and mc >= 50_000_000: pts += 3
    elif mc is not None and mc >= 20_000_000: pts += 2
    fl = f.get("float_shares")
    if fl is not None and fl > 0:
        pts += 3   # float known
        if fl < 20_000_000: pts += 2   # low float adds momentum quality
    if f.get("has_revenue"):
        pts += 2
    comp["company_quality"] = min(10.0, pts)

    # ---- Penalties & hard blocks ----
    if cat.get("dilution_detected") or filing.get("active_dilution"):
        sev = int(cat.get("dilution_severity") or filing.get("dilution_severity") or 50)
        pen = -30 if sev >= 70 else (-20 if sev >= 40 else -10)
        penalties.append({"type": "dilution", "points": pen, "severity": sev})
        if sev >= 85:
            hard_blocks.append("severe_actionable_dilution")
    if cat.get("going_concern_detected"):
        penalties.append({"type": "going_concern", "points": -15})
    if f.get("recent_reverse_split"):
        penalties.append({"type": "recent_reverse_split", "points": -10})
    has_catalyst = bool(direction == "positive" and materiality >= 30 and
                        novelty in ("new", "update"))
    if not has_catalyst and not s.get("momentum_only_mode"):
        penalties.append({"type": "no_identifiable_catalyst", "points": -15})
    if novelty == "recycled":
        penalties.append({"type": "recycled_news", "points": -10})
    if gap is not None and gap > 150:
        penalties.append({"type": "extreme_extension", "points": -10})
    if dv and dv < 250_000:
        penalties.append({"type": "low_dollar_volume_manipulation_risk", "points": -5})
    if direction == "mixed":
        penalties.append({"type": "conflicting_catalysts", "points": -5})

    if spread is not None and spread > (s.get("max_spread_pct") or 5):
        hard_blocks.append("spread_above_max")
    if not f.get("quote_fresh"):
        hard_blocks.append("stale_quote")
    if f.get("halted"):
        hard_blocks.append("unresolved_halt")
    if f.get("volume_incomplete"):
        hard_blocks.append("incomplete_live_volume")
    if f.get("data_disagreement"):
        hard_blocks.append("critical_data_disagreement")

    raw = sum(comp.values()) + sum(p["points"] for p in penalties)
    score = max(0.0, min(100.0, raw))

    # ---- BUY gates ----
    gates = {
        "score_gate": score >= (s.get("min_score_for_buy") or 75),
        "catalyst_gate": (has_catalyst and confidence >= (s.get("min_catalyst_confidence") or 0.6)
                          and bool(cat.get("source_url")))
                         or bool(s.get("momentum_only_mode") and rvol and rvol >= 5),
        "rvol_gate": bool(rvol is not None and (
            (not f.get("rvol_estimated") and rvol >= (s.get("min_rvol_for_buy") or 3.0)) or
            (f.get("rvol_estimated") and bool(s.get("allow_estimated_rvol", True)) and
             rvol >= (s.get("min_rvol_for_buy") or 3.0) * (s.get("est_rvol_buy_multiplier") or 1.5)))),
        "volume_gate": (f.get("pm_volume") or 0) >= (s.get("min_pm_volume") or 0)
                       and (f.get("pm_dollar_volume") or 0) >= (s.get("min_pm_dollar_volume") or 0),
        "freshness_gate": bool(f.get("quote_fresh")),
        "spread_gate": spread is None or spread <= (s.get("max_spread_pct") or 5),
        "no_hard_block": not hard_blocks,
        "price_confirmation_gate": (above_vwap is not False) and
            (dist is None or dist >= -(s.get("max_extension_from_pm_high_pct") or 25)),
    }
    buy = all(gates.values())

    min_rvol = (s.get("min_rvol_for_buy") or 3.0)
    rvol_req = min_rvol * ((s.get("est_rvol_buy_multiplier") or 1.5) if f.get("rvol_estimated") else 1.0)
    explain = [
        {"key": "score", "label": "Score", "pass": gates["score_gate"],
         "actual": round(score, 1), "required": f">= {s.get('min_score_for_buy') or 75}"},
        {"key": "catalyst", "label": "Verified catalyst", "pass": gates["catalyst_gate"],
         "actual": (f"{direction or 'none'}, {novelty or '-'}, conf {confidence:.2f}"
                    if cat else "none identified"),
         "required": f"positive + new/update, conf >= {s.get('min_catalyst_confidence') or 0.6}, source link"},
        {"key": "rvol", "label": "Premarket RVOL" + (" (est)" if f.get("rvol_estimated") else ""),
         "pass": gates["rvol_gate"],
         "actual": (f"{rvol:.1f}x" if rvol is not None else "no baseline yet"),
         "required": f">= {rvol_req:.1f}x"},
        {"key": "pm_volume", "label": "Premarket volume", "pass": (f.get("pm_volume") or 0) >= (s.get("min_pm_volume") or 0),
         "actual": f.get("pm_volume"), "required": f">= {int(s.get('min_pm_volume') or 0):,}"},
        {"key": "pm_dollar", "label": "Premarket $ volume", "pass": (f.get("pm_dollar_volume") or 0) >= (s.get("min_pm_dollar_volume") or 0),
         "actual": f.get("pm_dollar_volume"), "required": f">= ${int(s.get('min_pm_dollar_volume') or 0):,}"},
        {"key": "fresh", "label": "Fresh trade quote", "pass": gates["freshness_gate"],
         "actual": "fresh" if f.get("quote_fresh") else "no fresh trade print",
         "required": f"trade within {int(s.get('quote_freshness_sec') or 120)}s"},
        {"key": "spread", "label": "Spread", "pass": gates["spread_gate"],
         "actual": (f"{spread:.1f}%" if spread is not None else "unknown"),
         "required": f"<= {s.get('max_spread_pct') or 5}%"},
        {"key": "confirm", "label": "Price confirmation", "pass": gates["price_confirmation_gate"],
         "actual": ("above VWAP" if above_vwap else "below VWAP" if above_vwap is False else "VWAP n/a"),
         "required": "holding above VWAP, not overextended"},
        {"key": "blocks", "label": "No hard blocks", "pass": not hard_blocks,
         "actual": (", ".join(b.replace("_", " ") for b in hard_blocks) or "none"),
         "required": "none"},
    ]

    return {
        "strategy_version": STRATEGY_VERSION,
        "score": round(score, 2),
        "explain": explain,
        "components": {k: round(v, 2) for k, v in comp.items()},
        "penalties": penalties,
        "hard_blocks": hard_blocks,
        "gates": gates,
        "buy": buy,
        "inputs": {k: f.get(k) for k in (
            "price", "gap_pct", "rvol", "rvol_estimated", "rvol_coverage", "rvol_confidence",
            "volume_acceleration", "pm_volume", "pm_dollar_volume", "spread_pct",
            "above_vwap", "vwap", "dist_from_high_pct", "hh_hl", "market_cap",
            "float_shares", "shares_outstanding", "quote_fresh", "halted",
            "volume_incomplete", "data_disagreement", "recent_reverse_split")},
        "catalyst_input": cat,
        "filing_input": filing,
        "settings_used": {k: s.get(k) for k in DEFAULT_SETTINGS},
    }
