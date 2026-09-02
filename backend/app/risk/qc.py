"""Automated sanity checks. A signal that fails any of these is blocked and
logged rather than shown — a wrong trade plan is worse than no trade plan."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def qc_check(plan: Dict[str, Any], *, signal_time: Optional[float] = None,
             data_age_s: Optional[float] = None,
             max_data_age_s: float = 900.0) -> Dict[str, Any]:
    errors: List[str] = []
    d = plan.get("direction")
    entry, stop = plan.get("entry"), plan.get("stop")
    targets = plan.get("targets") or []

    if entry is None or stop is None:
        errors.append("missing entry or stop")
    else:
        if d == "long" and stop >= entry:
            errors.append("long stop must sit below entry")
        if d == "short" and stop <= entry:
            errors.append("short stop must sit above entry")
        for t in targets:
            if d == "long" and t["price"] <= entry:
                errors.append(f"long target {t['name']} is not above entry")
            if d == "short" and t["price"] >= entry:
                errors.append(f"short target {t['name']} is not below entry")

    q = (plan.get("risk") or {}).get("quantity")
    if q is not None and q <= 0:
        errors.append("position size is not positive")

    rp = (plan.get("risk") or {}).get("applied_risk_pct")
    mx = (plan.get("risk") or {}).get("max_risk_pct")
    if rp is not None and mx is not None and rp > mx + 1e-9:
        errors.append("applied risk exceeds the configured maximum")

    if data_age_s is not None and data_age_s > max_data_age_s:
        errors.append(f"market data is {data_age_s/60:.0f} minutes old — "
                      f"stale beyond the {max_data_age_s/60:.0f} minute limit")

    if signal_time is not None and signal_time > time.time() + 120:
        errors.append("signal timestamp is in the future")

    return {"passed": not errors, "errors": errors}
