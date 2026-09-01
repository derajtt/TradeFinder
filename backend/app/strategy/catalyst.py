"""Catalyst engine v2: the AI performs structured EXTRACTION into strict enums;
every point value comes from the deterministic maps below. A 1-3 scale can never
leak into a 0-100 field again because free-numeric scores no longer exist."""
from typing import Any, Dict, Optional

from .versions import AI_PROMPT_VERSION

CATEGORY_ENUM = [
    "fda_regulatory", "government_contract", "merger_acquisition",
    "commercial_agreement", "clinical_result", "earnings_surprise",
    "legal_patent", "strategic_financing", "customer_order", "partnership",
    "guidance_raise", "product_launch", "insider_ownership", "sec_filing_positive",
    "vague_pr", "conference", "corporate_update", "recycled", "rumor_promo",
    "non_binding_mou", "filing_housekeeping", "compliance_notice",
    "reverse_split", "dilution_negative", "other_negative", "none",
]

# deterministic grade per category (A = hard, B = meaningful, C/R = reject-quality)
CATEGORY_GRADE = {
    "fda_regulatory": "A", "government_contract": "A", "merger_acquisition": "A",
    "commercial_agreement": "A", "clinical_result": "A", "earnings_surprise": "A",
    "legal_patent": "A", "strategic_financing": "A",
    "customer_order": "B", "partnership": "B", "guidance_raise": "B",
    "product_launch": "B", "insider_ownership": "B", "sec_filing_positive": "B",
    "vague_pr": "C", "conference": "C", "corporate_update": "C", "recycled": "C",
    "rumor_promo": "R", "non_binding_mou": "C", "filing_housekeeping": "C",
    "compliance_notice": "C", "reverse_split": "R", "dilution_negative": "R",
    "other_negative": "R", "none": "R",
}

MATERIALITY_ENUM = ["transformative", "major", "moderate", "minor", "immaterial"]
CREDIBILITY_ENUM = ["primary_source", "reputable_media", "company_claim",
                    "unverified", "contradicted"]
FRESHNESS_ENUM = ["breaking", "today", "overnight", "stale", "recycled"]

MATERIALITY_POINTS = {"transformative": 25, "major": 19, "moderate": 12,
                      "minor": 5, "immaterial": 0}
CREDIBILITY_POINTS = {"primary_source": 6, "reputable_media": 4,
                      "company_claim": 2, "unverified": 0, "contradicted": -10}
FRESHNESS_POINTS = {"breaking": 6, "today": 4, "overnight": 2, "stale": 0,
                    "recycled": -8}
GRADE_OK_FOR_BUY = ("A", "B")

ANALYSIS_SCHEMA_V2: Dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": CATEGORY_ENUM},
        "materiality": {"type": "string", "enum": MATERIALITY_ENUM},
        "credibility": {"type": "string", "enum": CREDIBILITY_ENUM},
        "freshness": {"type": "string", "enum": FRESHNESS_ENUM},
        "counterparties": {"type": "array", "items": {"type": "string"}},
        "quantified_value": {"type": "string"},
        "binding": {"type": "boolean"},
        "novel": {"type": "boolean"},
        "negative_terms": {"type": "array", "items": {"type": "string"}},
        "dilution_detected": {"type": "boolean"},
        "going_concern_detected": {"type": "boolean"},
        "headline_appeal": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence_summary": {"type": "string"},
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["category", "materiality", "credibility", "freshness",
                 "counterparties", "quantified_value", "binding", "novel",
                 "negative_terms", "dilution_detected", "going_concern_detected",
                 "headline_appeal", "evidence_summary", "source_urls", "confidence"],
}

SYSTEM_PROMPT_V2 = (
    "You are a disciplined equity-catalyst extractor for premarket microcaps. "
    "Choose the single best category enum for the dominant catalyst in the evidence. "
    "Materiality is relative to a microcap: transformative = changes the company's "
    "trajectory (approval, acquisition, contract vs. company size); major = clearly "
    "market-moving; moderate = meaningful but incremental; minor = small; "
    "immaterial = routine. Credibility: primary_source = official filing/PR from "
    "the company or agency in the sources; contradicted = other evidence disputes it. "
    "Freshness: breaking = within hours and first report; recycled = re-published old "
    "news. binding=false for MOUs/LOIs/non-binding language. Never invent numbers or "
    "counterparties not present in the evidence. Extraction only — no scores."
)


def validate_extraction(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Schema-consistency validation. Returns cleaned dict or None (=> unknown)."""
    try:
        if d["category"] not in CATEGORY_ENUM: return None
        if d["materiality"] not in MATERIALITY_ENUM: return None
        if d["credibility"] not in CREDIBILITY_ENUM: return None
        if d["freshness"] not in FRESHNESS_ENUM: return None
        d["confidence"] = max(0.0, min(1.0, float(d["confidence"])))
        d["binding"] = bool(d["binding"]); d["novel"] = bool(d["novel"])
        d["counterparties"] = [str(x)[:120] for x in d.get("counterparties", [])][:10]
        d["negative_terms"] = [str(x)[:200] for x in d.get("negative_terms", [])][:10]
        d["source_urls"] = [str(x)[:400] for x in d.get("source_urls", [])][:10]
        d["evidence_summary"] = str(d.get("evidence_summary", ""))[:900]
        # consistency: recycled freshness or non-novel caps materiality
        if (d["freshness"] == "recycled" or not d["novel"]) and \
                d["materiality"] in ("transformative", "major"):
            d["materiality"] = "moderate"
        return d
    except (KeyError, TypeError, ValueError):
        return None


def grade_of(extraction: Optional[Dict[str, Any]]) -> str:
    if not extraction:
        return "R"
    g = CATEGORY_GRADE.get(extraction["category"], "R")
    # unverified or contradicted evidence can never be a hard catalyst
    if extraction["credibility"] in ("unverified", "contradicted") and g in ("A", "B"):
        g = "C"
    if extraction["freshness"] in ("stale", "recycled") and g in ("A", "B"):
        g = "C"
    if not extraction.get("novel", True) and g in ("A", "B"):
        g = "C"
    return g


def catalyst_points(extraction: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic mapping: enums -> points. Max 37 across the three axes."""
    if not extraction:
        return {"materiality_pts": 0, "credibility_pts": 0, "freshness_pts": 0,
                "headline_pts": 0, "total": 0, "grade": "R"}
    m = MATERIALITY_POINTS[extraction["materiality"]]
    c = CREDIBILITY_POINTS[extraction["credibility"]]
    f = FRESHNESS_POINTS[extraction["freshness"]]
    h = {"high": 3, "medium": 1.5, "low": 0}[extraction.get("headline_appeal", "low")]
    return {"materiality_pts": m, "credibility_pts": c, "freshness_pts": f,
            "headline_pts": h, "total": max(0, m + c + f + h),
            "grade": grade_of(extraction), "prompt_version": AI_PROMPT_VERSION}
