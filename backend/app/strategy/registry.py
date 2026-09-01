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
