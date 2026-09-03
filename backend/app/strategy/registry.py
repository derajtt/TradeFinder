"""The twelve-model registry + regime controller. Each model is an isolated
worker profile with its own settings, versions, signals, and $10,000 paper
account. Research-only methods stay visible but can never emit live BUYs."""
from typing import Any, Dict

STARTING_CASH = 10_000.0

MODELS: Dict[str, Dict[str, Any]] = {
    "premarket_scalper": {
        "name": "Premarket Scalper", "engine": "scalper", "build": True,
        "asset_classes": ["stocks"], "cadence": "premarket",
        "horizon": "7:00 AM–noon", "color": "#38bdf8",
        "edge": "Fresh catalyst plus early, executable microcap momentum",
        "universe": "microcaps $0.10–$5.00",
    },
    "technical_confluence": {
        "name": "Technical Confluence", "engine": "confluence", "build": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
        "horizon": "minutes–days", "color": "#22d3ee",
        "edge": "Explainable multi-timeframe structure with capped indicator clusters",
        "universe": "liquid stocks & ETFs",
    },
    "stat_arb_pairs": {
        "name": "Statistical Arbitrage", "engine": "pairs", "build": True,
        "asset_classes": ["stocks"], "cadence": "intraday",
        "horizon": "intraday–days", "color": "#a78bfa",
        "edge": "Convergence of stable relative-value relationships",
        "universe": "curated liquid pairs",
        "data_notes": "Borrow fees/short constraints not in plan — research long/short paper only",
    },
    "trend_following": {
        "name": "Trend Following", "engine": "trend", "build": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "daily",
        "horizon": "days–months", "color": "#34d399",
        "edge": "Persistence of established trends, volatility-scaled",
        "universe": "liquid ETFs & large caps",
    },
    "earnings_drift": {
        "name": "Earnings Drift", "engine": "earnings", "build": True,
        "asset_classes": ["stocks"], "cadence": "daily",
        "horizon": "days–weeks", "color": "#fbbf24",
        "edge": "Underreaction to quantified earnings surprises",
        "universe": "reporting stocks",
        "data_notes": "Historical consensus is current-value (labeled ESTIMATED); "
                      "forward captures are point-in-time",
    },
    "mean_reversion": {
        "name": "Intraday Mean Reversion", "engine": "meanrev", "build": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
        "horizon": "minutes", "color": "#67e8f9",
        "edge": "Temporary non-news dislocations in liquid instruments",
        "universe": "SPY/QQQ/sector ETFs",
    },
    "residual_momentum": {
        "name": "Residual Momentum", "engine": "resmom", "build": True,
        "asset_classes": ["stocks"], "cadence": "weekly",
        "horizon": "weeks–months", "color": "#f472b6",
        "edge": "Company-specific strength after factor removal",
        "universe": "liquid equities",
    },
    "multi_factor": {
        "name": "Multi-Factor Rotation", "engine": "factor", "build": True,
        "asset_classes": ["stocks"], "cadence": "monthly",
        "horizon": "months", "color": "#c084fc",
        "edge": "Momentum, value, quality, low-risk composite",
        "universe": "liquid equities/ETFs",
        "data_notes": "Fundamentals are current-value snapshots (labeled); "
                      "point-in-time history not in plan",
    },
    "insider_cluster": {
        "name": "Insider Cluster-Buying", "engine": "insider", "build": True,
        "asset_classes": ["stocks"], "cadence": "daily",
        "horizon": "weeks–months", "color": "#fb923c",
        "edge": "Information in meaningful Form 4 purchase clusters",
        "universe": "listed stocks (SEC Form 4)",
    },
    "opening_range_breakout": {
        "name": "Opening-Range Breakout", "engine": "orb", "build": True,
        "asset_classes": ["stocks"], "cadence": "intraday",
        "horizon": "minutes–hours", "color": "#4ade80",
        "edge": "Confirmed volatility expansion after the open",
        "universe": "liquid ETFs & large caps",
    },
    "breakout_finder": {
        "name": "Breakout Finder", "engine": "breakout", "build": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
        "horizon": "minutes–days", "color": "#facc15",
        "edge": "Multi-horizon consolidation and resistance breaks",
        "universe": "liquid stocks/ETFs",
    },
    "gaussian_channel": {
        "name": "Gaussian Channel", "engine": "gaussian", "build": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
        "horizon": "minutes–days", "color": "#818cf8",
        "edge": "Low-lag filtered trend expansion (Ehlers-style, causal)",
        "universe": "liquid stocks/ETFs",
    },
}

MODELS["chart_patterns"] = {
    "name": "Chart Patterns", "engine": "chartpat", "build": True,
    "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
    "horizon": "hours–days", "color": "#38d9f8",
    "edge": "Deterministic chart intelligence: confirmed-pivot S/R zones, "
            "trendlines, double tops/bottoms, compression — trades only "
            "volume-confirmed breaks",
    "universe": "liquid stocks/ETFs + crypto",
}

# ── experimental models (Claude-designed; clearly labeled; same rules) ──
MODELS.update({
    "exp_liquidity_vacuum": {
        "name": "EXP · Liquidity Vacuum", "engine": "vacuum", "build": True,
        "experimental": True,
        "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
        "horizon": "hours–days", "color": "#2dd4bf",
        "edge": "Volatility-contraction + volume dry-up precedes expansion: "
                "NR-range compression with volume z<-1, entered on the first "
                "confirmed range expansion with participation",
        "universe": "liquid ETFs & large caps",
        "hypothesis": "Volatility clustering is well documented; the "
                      "contraction→expansion transition offers defined risk at "
                      "the compression boundary. UNPROVEN until forward-tested.",
    },
    "exp_open_drive": {
        "name": "EXP · Opening Drive", "engine": "opendrive", "build": True,
        "experimental": True,
        "asset_classes": ["stocks"], "cadence": "intraday",
        "horizon": "open–close", "color": "#f59e0b",
        "edge": "A first-15-minute return in the top percentile of its own "
                "60-day distribution, holding above VWAP, tends to continue "
                "intraday (documented intraday momentum persistence)",
        "universe": "SPY/QQQ/liquid large caps",
        "hypothesis": "Strong opening drives reflect real order-flow imbalance "
                      "that one print cannot absorb. UNPROVEN until forward-tested.",
    },
    "exp_rs_reclaim": {
        "name": "EXP · RS Leaders Reclaim", "engine": "rsreclaim", "build": True,
        "experimental": True,
        "asset_classes": ["stocks"], "cadence": "intraday",
        "horizon": "days", "color": "#e879f9",
        "edge": "Top-decile 5-day relative strength vs SPY, entered on an "
                "intraday VWAP reclaim after a shallow pullback — cross-"
                "sectional momentum with an intraday risk anchor",
        "universe": "liquid large caps",
        "hypothesis": "Short-horizon RS leaders attract systematic follow-on "
                      "flows; the VWAP reclaim gives a defined invalidation. "
                      "UNPROVEN until forward-tested.",
    },
})

MODELS["extreme_reversion"] = {
    "name": "Extreme Reversion", "engine": "extreme_bb_rsi", "build": True,
    "asset_classes": ["stocks", "crypto"], "cadence": "intraday",
    "horizon": "minutes–hours", "color": "#fb7185",
    "risk_model": "standard", "code": "EXTREME_BB_RSI", "own_worker": True,
    "edge": "Statistically extreme dislocation that has already begun to revert "
            "— band re-entry plus an RSI turn, never a falling knife",
    "universe": "liquid stocks, ETFs and major crypto",
    "data_notes": "Runs its own multi-timeframe worker (15m/1h, plus 5m on a core "
                  "set). 1-minute bars are not entitled on the current data plan, "
                  "so 1m/3m are reported untested rather than assumed.",
}

# Custom confluence strategies. Chosen from the data, not designed: across
# every 2- and 3-model combination that fired on the same symbol-day, every
# PAIR was negative (2-model symbol-days ran -0.86R, worse than singles at
# -0.63), and only TRIPLES anchored on chart_patterns + exp_open_drive were
# positive. Each fires only when ALL of its required finders have fired on the
# same symbol today, and runs its own \$10,000 ledger. Three sessions of data —
# these are experiments with a stated basis, not proven edges.
MODELS["custom_strategy_1"] = {
    "name": "Custom Strategy 1", "engine": "custom", "build": True,
    "asset_classes": ["stocks"], "cadence": "intraday",
    "horizon": "intraday", "color": "#f0abfc", "custom": True, "own_worker": True,
    "requires": ["chart_patterns", "exp_open_drive", "technical_confluence"],
    "edge": "Fires only when Chart Patterns, Opening Drive AND Technical "
            "Confluence have all fired on the same symbol today",
    "universe": "intersection of its three finders",
    "data_notes": "Basis: +0.38R avg, 45% win over 11 symbol-days (Sep 1-3). "
                  "Small sample; treat as an experiment until it has 30+ trades.",
}
MODELS["custom_strategy_2"] = {
    "name": "Custom Strategy 2", "engine": "custom", "build": True,
    "asset_classes": ["stocks"], "cadence": "intraday",
    "horizon": "intraday", "color": "#c4b5fd", "custom": True, "own_worker": True,
    "requires": ["chart_patterns", "exp_open_drive", "trend_following"],
    "edge": "Fires only when Chart Patterns, Opening Drive AND Trend Following "
            "have all fired on the same symbol today",
    "universe": "intersection of its three finders",
    "data_notes": "Basis: +0.28R avg, 30% win over 10 symbol-days (Sep 1-3). "
                  "Small sample; treat as an experiment until it has 30+ trades.",
}
MODELS["custom_strategy_3"] = {
    "name": "Custom Strategy 3", "engine": "custom", "build": True,
    "asset_classes": ["stocks"], "cadence": "intraday",
    "horizon": "intraday", "color": "#fda4af", "custom": True, "own_worker": True,
    "requires": ["chart_patterns", "exp_open_drive", "exp_rs_reclaim"],
    "edge": "Fires only when Chart Patterns, Opening Drive AND RS Leaders "
            "Reclaim have all fired on the same symbol today",
    "universe": "intersection of its three finders",
    "data_notes": "Basis: +0.24R avg, 30% win over 10 symbol-days (Sep 1-3). "
                  "Small sample; treat as an experiment until it has 30+ trades.",
}

# Which risk model governs each strategy. "standard" routes through the platform
# risk layer (stop-derived sizing, R:R gate, portfolio ceilings). "scalper" and
# "WINGED" keep their own tested execution rules; the layer still renders their
# entry/stop/targets/size for display but never overrides them.
RISK_MODELS = {mid: ("scalper" if meta.get("engine") == "scalper"
                     else meta.get("risk_model", "standard"))
               for mid, meta in MODELS.items()}

REGIME_CONTROLLER = {
    "name": "Regime Controller", "engine": "regime",
    "note": "Overlay, not a competitor: classifies trend/range/event/high-risk/"
            "uncertain and gates model participation.",
}

RESEARCH_ONLY = [
    {"id": "index_rebalance", "name": "Index-rebalance flow",
     "why_not": "needs point-in-time membership/announcement/auction data"},
    {"id": "carry_basis", "name": "Carry / basis",
     "why_not": "needs futures/FX contracts, rolls, financing data"},
    {"id": "order_flow", "name": "Order-flow imbalance",
     "why_not": "needs tick/Level-2 data; 1-min bars cannot reconstruct the book"},
    {"id": "hft_mm", "name": "Retail HFT market making",
     "why_not": "latency, queue position, venue fragmentation"},
    {"id": "short_vol", "name": "Unhedged short volatility",
     "why_not": "negative skew; catastrophic tail without defined-risk structures"},
    {"id": "ai_next_candle", "name": "AI next-candle guessing",
     "why_not": "overfit-prone; no explanation; needs tick data + strict OOT validation"},
    {"id": "martingale", "name": "Martingale / grid recovery",
     "why_not": "loss grows as evidence moves against the trade — do not promote"},
]

# shared liquid universes (bounded for API budget)
ETF_UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "XLE", "XLF", "XLK", "XLV", "XLI",
                "SMH", "GDX", "TLT", "AAPL", "NVDA", "TSLA", "AMD", "META",
                "AMZN", "MSFT", "GOOGL"]
CRYPTO_UNIVERSE = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "ADAUSD"]
PAIRS = [("GDX", "GLD"), ("XLE", "XOP"), ("QQQ", "SPY"), ("SMH", "QQQ"),
         ("IWM", "SPY")]
