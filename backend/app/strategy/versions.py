"""Frozen version identifiers. Any parameter change requires bumping the relevant
version and starting a new performance cohort — never silently retune."""

STRATEGY_VERSION = "v2.0.0"        # entry/exit policy contract
SCORING_VERSION = "s2.0.0"         # ranking-score weights
FILTER_VERSION = "f2.0.0"          # hard-gate set
DATA_SOURCE_VERSION = "d1.1.0"     # FMP stable + aftermarket tape + EDGAR
OUTCOME_POLICY_VERSION = "o2.0.0"  # executable-fill early-window judgment
AI_PROMPT_VERSION = "p2.0.0"       # catalyst extraction contract

VERSIONS = {
    "strategy_version": STRATEGY_VERSION,
    "scoring_version": SCORING_VERSION,
    "filter_version": FILTER_VERSION,
    "data_source_version": DATA_SOURCE_VERSION,
    "outcome_policy_version": OUTCOME_POLICY_VERSION,
    "ai_prompt_version": AI_PROMPT_VERSION,
}

LIFECYCLE = (
    "DISCOVERED", "EARLY_WATCH", "QUALIFIED_WATCH", "ACTIONABLE_BUY",
    "REJECTED", "INVALIDATED", "EXPIRED", "CLOSED", "DATA_ERROR",
)

COHORTS = ("in_sample", "replay", "shadow", "forward", "live_paper")
