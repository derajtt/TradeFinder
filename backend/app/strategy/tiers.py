"""Price-risk tiers. The sub-$0.25 tier carries hard extra protections."""
from typing import Any, Dict, Optional

TIERS = [
    {"name": "T1_penny", "lo": 0.10, "hi": 0.2499, "max_spread_pct": 2.0,
     "min_quote_size_usd": 2000, "min_pm_dollar_vol": 250_000,
     "require_grade": ("A",), "max_risk_pct": 4.0,
     "note": "special high-risk penny tier: hard Grade-A catalyst, tight spread, "
             "extra dilution/shell/reverse-split protections"},
    {"name": "T2", "lo": 0.25, "hi": 0.4999, "max_spread_pct": 3.0,
     "min_quote_size_usd": 1500, "min_pm_dollar_vol": 150_000,
     "require_grade": ("A", "B"), "max_risk_pct": 5.0, "note": ""},
    {"name": "T3", "lo": 0.50, "hi": 0.9999, "max_spread_pct": 3.5,
     "min_quote_size_usd": 1000, "min_pm_dollar_vol": 100_000,
     "require_grade": ("A", "B"), "max_risk_pct": 6.0, "note": ""},
    {"name": "T4", "lo": 1.00, "hi": 2.4999, "max_spread_pct": 4.0,
     "min_quote_size_usd": 1000, "min_pm_dollar_vol": 100_000,
     "require_grade": ("A", "B"), "max_risk_pct": 7.0, "note": ""},
    {"name": "T5", "lo": 2.50, "hi": 5.00, "max_spread_pct": 4.0,
     "min_quote_size_usd": 1000, "min_pm_dollar_vol": 100_000,
     "require_grade": ("A", "B"), "max_risk_pct": 8.0, "note": ""},
]


def tier_for(price: Optional[float]) -> Optional[Dict[str, Any]]:
    if price is None:
        return None
    for t in TIERS:
        if t["lo"] <= price <= t["hi"] + 1e-9:
            return t
    return None


# float-rotation zones — configurable hypotheses, NOT proven facts; forward-tested
ROTATION_ZONES = [
    {"name": "insufficient", "lo": 0.0, "hi": 0.01, "points": 0.0, "block": False},
    {"name": "early_interest", "lo": 0.01, "hi": 0.05, "points": 2.0, "block": False},
    {"name": "ignition", "lo": 0.05, "hi": 0.20, "points": 6.0, "block": False},
    {"name": "established", "lo": 0.20, "hi": 0.40, "points": 4.0, "block": False},
    {"name": "crowded", "lo": 0.40, "hi": 1.00, "points": 1.0, "block": False},
    {"name": "over_rotated", "lo": 1.00, "hi": 1e9, "points": 0.0, "block": True},
]


def rotation_zone(rot):
    if rot is None:
        return None
    for z in ROTATION_ZONES:
        if z["lo"] <= rot < z["hi"]:
            return z
    return ROTATION_ZONES[-1]
