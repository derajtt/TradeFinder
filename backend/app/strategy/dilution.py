"""Dilution-risk engine over recent EDGAR filings + AI-extracted negative terms.
A recent filing is never automatically positive — content and form drive the flags."""
from typing import Any, Dict, List, Optional

SHELF_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A", "EFFECT"}
OFFERING_FORMS = {"424B1", "424B2", "424B3", "424B4", "424B5"}
FLAG_WEIGHTS = {  # severity 0-100 contribution
    "active_offering_prospectus": 40,
    "effective_shelf": 25,
    "recent_shelf_filing": 15,
    "atm_language": 35,
    "convertible_or_toxic_terms": 45,
    "warrants_outstanding": 15,
    "equity_line": 30,
    "recent_reverse_split": 25,
    "going_concern": 30,
    "authorized_share_increase": 20,
    "compliance_notice": 15,
    "late_filing": 15,
}


def assess(filings: List[dict], extraction: Optional[Dict[str, Any]] = None,
           recent_reverse_split: bool = False) -> Dict[str, Any]:
    flags: List[str] = []
    for f in filings or []:
        form = (f.get("form_type") or "").upper()
        title = ((f.get("title") or "") + " " + (f.get("items") or "")).lower()
        if form in OFFERING_FORMS:
            flags.append("active_offering_prospectus")
        if form == "EFFECT":
            flags.append("effective_shelf")
        elif form in SHELF_FORMS:
            flags.append("recent_shelf_filing")
        if "at-the-market" in title or "atm " in title:
            flags.append("atm_language")
        if "convertible" in title:
            flags.append("convertible_or_toxic_terms")
        if "warrant" in title:
            flags.append("warrants_outstanding")
        if "equity line" in title or "purchase agreement" in title and "equity" in title:
            flags.append("equity_line")
        if form.startswith("NT "):
            flags.append("late_filing")
        if "deficiency" in title or "compliance" in title or "delisting" in title:
            flags.append("compliance_notice")
        if "authorized" in title and "increase" in title:
            flags.append("authorized_share_increase")
    if recent_reverse_split:
        flags.append("recent_reverse_split")
    for term in (extraction or {}).get("negative_terms", []):
        t = term.lower()
        if "convert" in t or "toxic" in t or "variable rate" in t:
            flags.append("convertible_or_toxic_terms")
        if "warrant" in t:
            flags.append("warrants_outstanding")
        if "at-the-market" in t or "atm" in t:
            flags.append("atm_language")
    if (extraction or {}).get("going_concern_detected"):
        flags.append("going_concern")
    if (extraction or {}).get("dilution_detected") and not flags:
        flags.append("recent_shelf_filing")
    flags = sorted(set(flags))
    severity = min(100, sum(FLAG_WEIGHTS.get(f, 10) for f in flags))
    return {"flags": flags, "severity": severity,
            "hard_block": severity >= 60 or "active_offering_prospectus" in flags
                          and "atm_language" in flags,
            "penalty_pts": -min(30, severity * 0.4)}
