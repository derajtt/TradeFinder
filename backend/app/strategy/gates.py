"""Decision engine v2: hard gates first (any failure => REJECTED with the exact
reason), ranking score only for gate-passers. No SPY/sector confirmation by design
— these are catalyst-driven microcaps."""
from typing import Any, Dict, List, Optional, Tuple

from .catalyst import GRADE_OK_FOR_BUY, catalyst_points
from .execution import simulate_buy_fill
from .tiers import rotation_zone, tier_for
from .versions import FILTER_VERSION, SCORING_VERSION, VERSIONS

ACTIONABLE_START_MIN = 7 * 60          # 07:00 ET
ACTIONABLE_END_MIN = 9 * 60 + 20       # 09:20 ET — no new premarket entries after
PM_END_MIN = 9 * 60 + 30

DEFAULT_SETUP_CFG = {
    "max_ext_above_vwap_pct": 20.0,    # farther than this without a base = chasing
    "min_r_multiple": 1.5,             # reward to first objective vs risk to stop
    "max_risk_pct_hard": 10.0,
}


def _minutes(et_dt) -> int:
    return et_dt.hour * 60 + et_dt.minute


def detect_setup(f: Dict[str, Any]) -> Dict[str, Any]:
    """Defined entry setups. Returns {type, valid, entry, stop, target1, target2,
    r_multiple, reason}. 'Above VWAP' alone is deliberately insufficient."""
    price = f.get("price")
    vwap = f.get("vwap")
    pm_high = f.get("pm_high")
    pm_low = f.get("pm_low")
    out = {"type": None, "valid": False, "entry": price, "stop": None,
           "target1": None, "target2": None, "r_multiple": None, "reason": ""}
    if not price or price <= 0:
        out["reason"] = "no_price"
        return out
    ask = f.get("ask") or price
    hh_hl = f.get("hh_hl") == 1.0
    accel = f.get("volume_acceleration")
    dist_high = f.get("dist_from_high_pct")

    # candidate invalidation level: max(VWAP, last higher-low proxy = pm_low ratchet)
    stop_candidates = [x for x in (vwap, pm_low) if x and 0 < x < price]
    if not stop_candidates:
        out["reason"] = "no_defined_invalidation_below_price"
        return out
    stop = max(stop_candidates)
    risk = ask - stop
    if risk <= 0:
        out["reason"] = "invalidation_above_entry"
        return out
    risk_pct = risk / ask * 100.0
    if risk_pct > DEFAULT_SETUP_CFG["max_risk_pct_hard"]:
        out["reason"] = f"risk_to_invalidation_{risk_pct:.1f}pct_too_wide"
        return out

    # classify the setup shape
    if pm_high and price >= pm_high * 0.985 and hh_hl and (accel or 0) >= 1.5:
        stype = "breakout_hold_above_base"
        target1 = ask + risk           # 1R
    elif vwap and price >= vwap and hh_hl:
        stype = "higher_low_with_volume" if (accel or 0) >= 1.5 else "pullback_hold_vwap"
        target1 = pm_high if (pm_high and pm_high > ask + risk) else ask + risk
    elif vwap and price >= vwap and dist_high is not None and dist_high > 12:
        stype = "vwap_reclaim_confirmation"
        target1 = ask + risk
    else:
        out["reason"] = "no_defined_setup(above_vwap_alone_is_insufficient)"
        return out

    r1 = (target1 - ask) / risk if risk > 0 else 0
    if r1 < DEFAULT_SETUP_CFG["min_r_multiple"] and stype != "breakout_hold_above_base":
        out.update({"type": stype, "reason": f"r_multiple_{r1:.2f}_below_min"})
        return out
    out.update({"type": stype, "valid": True, "entry": round(ask, 4),
                "stop": round(stop, 4), "target1": round(target1, 4),
                "target2": round(ask + 2 * risk, 4),
                "r_multiple": round(r1, 2), "reason": f"{stype} ok"})
    return out


def evaluate(f: Dict[str, Any], catalyst_ext: Optional[Dict[str, Any]],
             dilution: Dict[str, Any], settings: Dict[str, Any],
             et_dt) -> Dict[str, Any]:
    """Full decision: returns {lifecycle, gates:[{gate,pass,detail}], rejection_reason,
    score, score_components, setup, fill, versions...}."""
    mins = _minutes(et_dt)
    gates: List[Dict[str, Any]] = []
    price = f.get("price")
    tier = tier_for(price)
    grade_pts = catalyst_points(catalyst_ext)
    grade = grade_pts["grade"]
    rot = f.get("float_rotation")
    zone = rotation_zone(rot)

    def gate(name, ok, detail=""):
        gates.append({"gate": name, "pass": bool(ok), "detail": str(detail)[:200]})
        return ok

    in_window = ACTIONABLE_START_MIN <= mins <= ACTIONABLE_END_MIN
    early = mins < ACTIONABLE_START_MIN
    gate("actionable_window", in_window,
         f"{et_dt.strftime('%H:%M')} ET (window 07:00-09:20)")
    gate("price_tier_allowed", tier is not None,
         f"price {price} -> {(tier or {}).get('name', 'outside $0.10-$5.00')}")
    grade_needed = (tier or {}).get("require_grade", GRADE_OK_FOR_BUY)
    gate("hard_fresh_catalyst", grade in grade_needed,
         f"grade {grade} (tier requires {'/'.join(grade_needed)})")
    gate("catalyst_primary_source",
         bool(catalyst_ext) and catalyst_ext.get("credibility") in
         ("primary_source", "reputable_media"),
         (catalyst_ext or {}).get("credibility", "none"))
    gate("quotes_fresh", bool(f.get("quote_fresh")),
         "live trade print" if f.get("quote_fresh") else "no fresh print")
    sp = f.get("spread_pct")
    max_sp = (tier or {}).get("max_spread_pct", 5.0)
    gate("spread_within_tier", sp is not None and sp <= max_sp,
         f"{sp}% vs max {max_sp}%")
    fill = simulate_buy_fill(f.get("bid"), f.get("ask"), f.get("bid_size"),
                             f.get("ask_size"), tier,
                             {"slippage_pct": settings.get("slippage_pct", 0.4)})
    gate("quoted_liquidity", fill["filled"] or fill["no_fill_reason"] is None or
         "liquidity" not in (fill["no_fill_reason"] or ""),
         fill.get("no_fill_reason") or f"ask size ${fill.get('quote_size_usd')}")
    dv = f.get("pm_dollar_volume") or 0
    need_dv = (tier or {}).get("min_pm_dollar_vol",
                               settings.get("min_pm_dollar_volume", 100_000))
    gate("dollar_volume", dv >= need_dv, f"${dv:,.0f} vs ${need_dv:,.0f}")
    gate("sustained_participation", (f.get("participation_bars") or 0) >= 3,
         f"{f.get('participation_bars')} active minutes (need 3+, not one print)")
    rot_cap = settings.get("rotation_hard_cap", 1.0)
    gate("not_over_rotated", rot is None or rot <= rot_cap,
         f"rotation {rot if rot is None else round(rot * 100, 1)}% zone "
         f"{(zone or {}).get('name')}")
    ext = f.get("ext_above_vwap_pct")
    max_ext = settings.get("max_ext_above_vwap_pct",
                           DEFAULT_SETUP_CFG["max_ext_above_vwap_pct"])
    gate("not_overextended", ext is None or ext <= max_ext,
         f"{ext}% above VWAP (max {max_ext})" if ext is not None else "vwap n/a")
    gate("no_dilution_block", not dilution.get("hard_block"),
         ",".join(dilution.get("flags", [])) or "clean")
    gate("not_halted", not f.get("halted"), "")
    setup = detect_setup(f)
    gate("valid_entry_setup", setup["valid"], setup["reason"])
    gate("data_health", not f.get("volume_incomplete") and not f.get("data_disagreement"),
         "complete" if not f.get("volume_incomplete") else "volume incomplete")

    failed = [g for g in gates if not g["pass"]]
    all_pass = not failed

    # ---- ranking score (only meaningful for gate-passers; logged for everyone) ----
    comp: Dict[str, float] = {}
    comp["catalyst"] = grade_pts["total"]                        # <= 37
    accel = f.get("volume_acceleration") or 0
    comp["dollar_vol_accel"] = min(12.0, 4.0 * accel) if accel else 0
    rvol = f.get("rvol") or 0
    rvol_conf = f.get("rvol_confidence") or 0
    comp["time_aligned_rvol"] = min(10.0, rvol) * (0.5 + 0.5 * rvol_conf)
    comp["spread_quality"] = 6.0 if (sp is not None and sp <= max_sp * 0.5) else \
        (3.0 if sp is not None and sp <= max_sp else 0.0)
    comp["quote_liquidity"] = 5.0 if fill.get("filled") else 0.0
    comp["rotation_zone"] = (zone or {}).get("points", 0.0)
    comp["structure"] = (4.0 if f.get("hh_hl") == 1.0 else 0.0) + \
        (3.0 if setup["valid"] else 0.0)
    comp["risk_reward"] = min(6.0, 2.0 * (setup.get("r_multiple") or 0))
    comp["dilution"] = dilution.get("penalty_pts", 0.0)
    comp["data_confidence"] = 3.0 if f.get("quote_fresh") and not f.get("volume_incomplete") else 0.0
    score = round(max(0.0, min(100.0, sum(comp.values()))), 2)

    if all_pass:
        lifecycle = "ACTIONABLE_BUY" if fill["filled"] else "REJECTED"
        rejection = None if fill["filled"] else f"no_fill:{fill['no_fill_reason']}"
        if not fill["filled"]:
            all_pass = False
    else:
        hard_names = {g["gate"] for g in failed}
        if early and hard_names <= {"actionable_window"}:
            lifecycle = "EARLY_WATCH"      # everything but the clock passes
            rejection = None
        elif mins > ACTIONABLE_END_MIN and hard_names <= {"actionable_window"}:
            lifecycle = "EXPIRED"
            rejection = "outside_entry_window(after_09:20)"
        else:
            lifecycle = "REJECTED"
            rejection = "; ".join(f"{g['gate']}:{g['detail']}" for g in failed
                                  if g["gate"] != "actionable_window")[:400]

    return {"lifecycle": lifecycle, "gates": gates, "rejection_reason": rejection,
            "score": score, "score_components": comp, "setup": setup, "fill": fill,
            "tier": (tier or {}).get("name"), "catalyst_grade": grade,
            "catalyst_points": grade_pts, "rotation_zone": (zone or {}).get("name"),
            "filter_version": FILTER_VERSION, "scoring_version": SCORING_VERSION,
            "versions": VERSIONS}
