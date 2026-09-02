"""EXTREME_BB_RSI — "Extreme Reversion".

Mean reversion from statistically extreme dislocations: price pierces a wide
Bollinger band while RSI is at an extreme, then *recovers back inside* the band
with RSI turning. The re-entry requirement is the whole point — it filters the
"oversold and still collapsing" case that kills naive band-fade systems.

Every value here is computed from bars[:i+1] only. `scan()` walks the series
forward once and is the single code path shared by the live scanner and the
backtester, so a backtest cannot see a candle the live engine would not have.
Signals confirm on CLOSED candles only; an intrabar poke that vanished before
the close never becomes history.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .indicators import (adx_series, atr_series, bollinger_series, ema_series,
                         percentile_rank, rsi_series, session_vwap)

STRATEGY_ID = "extreme_reversion"
STRATEGY_CODE = "EXTREME_BB_RSI"

# Materially different logic => different version => separate performance pool.
VERSIONS = {
    "video_baseline": "1.0.0",
    "confirm": "1.1.0",
    "adaptive": "1.2.0",
}

DEFAULTS: Dict[str, Any] = {
    "bb_length": 20,
    "bb_dev": 3.0,
    "rsi_length": 14,
    "rsi_oversold": 10.0,
    "rsi_overbought": 90.0,
    # confirmation
    "confirmation": True,
    "require_reentry": True,
    "require_rsi_turn": True,
    "confirm_window": 3,        # bars allowed between extreme and confirmation
    # filters (None = off)
    "htf_filter": "none",       # none | ema200 | soft
    "adx_max": None,            # reject when ADX above this
    "rvol_min": None,           # require relative volume
    "max_bars_outside": None,   # reject prolonged band-riding
    "block_breakout_risk": True,
    # risk / exits
    "stop_model": "extreme_atr",     # extreme_atr | atr | pct
    "stop_param": 0.2,
    "exit_model": "bb_basis",        # bb_basis | bb_opposite | rr | rsi_norm | atr
    "exit_param": 2.0,
    "expiry_bars": 5,
    "max_entry_dev_atr": 0.5,
    "min_score": 60.0,
    "allow_shorts": True,
    # frictions — never zero by default
    "slippage_pct": 0.05,
    "commission_pct": 0.02,
}

VARIANTS: Dict[str, Dict[str, Any]] = {
    # The raw concept, untouched, as a control. No confirmation, no filters.
    "video_baseline": {
        "label": "Video Baseline",
        "note": "Raw concept: 3σ band touch + RSI extreme, enter on that close. "
                "No re-entry confirmation, no filters. Control group only.",
        "params": {"confirmation": False, "require_reentry": False,
                   "require_rsi_turn": False, "block_breakout_risk": False,
                   "stop_model": "extreme_atr", "stop_param": 0.2,
                   "exit_model": "bb_basis", "min_score": 0.0},
    },
    # First improvement: require the band re-entry and the RSI turn.
    "confirm": {
        "label": "Confirmed Re-entry",
        "note": "Adds close-back-inside-band and RSI-turning-up confirmation.",
        "params": {"confirmation": True, "require_reentry": True,
                   "require_rsi_turn": True, "block_breakout_risk": True,
                   "min_score": 0.0},
    },
    # Everything on; the score decides. Weights are a starting hypothesis,
    # not a validated model — the lab reweights them from realised results.
    "adaptive": {
        "label": "Adaptive",
        "note": "Confirmation plus trend/ADX/volume/regime context feeding a "
                "setup-quality score. Score threshold gates alerts.",
        "params": {"confirmation": True, "require_reentry": True,
                   "require_rsi_turn": True, "block_breakout_risk": True,
                   "htf_filter": "soft", "adx_max": 30.0,
                   "max_bars_outside": 4, "min_score": 60.0},
    },
}

# The configuration the parameter study actually landed on. It is the least-bad
# of 1,056 evaluations and still backtested NEGATIVE (-0.364R out-of-sample), so
# it is never presented as validated. It exists because it is the only variant
# that produces signals often enough to accumulate forward evidence at all — the
# strict variants fire roughly once every 500 days and would prove nothing.
VARIANTS["studied"] = {
    "label": "Studied (widened)",
    "note": "Widest thresholds the study examined: 30-period bands at 2.0 sigma, "
            "RSI 14 oversold at 20, confirmation on. Produces a usable sample. "
            "Backtested NEGATIVE out-of-sample — carried for evidence, not edge.",
    "params": {"confirmation": True, "require_reentry": True,
               "require_rsi_turn": True, "block_breakout_risk": True,
               "bb_length": 30, "bb_dev": 2.0, "rsi_length": 14,
               "rsi_oversold": 20.0, "rsi_overbought": 80.0,
               "stop_model": "pct", "stop_param": 0.5,
               "exit_model": "bb_basis", "min_score": 0.0},
}
VERSIONS["studied"] = "1.3.0"

# Variants evaluated on every live pass. Same bars, so no extra API cost — each
# records its own signals under its own version, which is what lets the forward
# record answer "which of these is actually best" rather than assuming.
LIVE_VARIANTS = ["studied", "confirm", "video_baseline", "adaptive"]

SCORE_WEIGHTS = {
    "rsi_extremeness": 15, "bb_penetration": 15, "bb_reentry": 15,
    "rsi_reversal": 15, "trend_context": 10, "adx_suitability": 10,
    "volume": 5, "reversal_candle": 5, "divergence": 5, "expectancy": 5,
}

SCORE_BANDS = [(90, "Exceptional"), (80, "Strong"), (70, "Good"),
               (60, "Moderate"), (0, "Below alert threshold")]


def band_of(score: float) -> str:
    for floor, label in SCORE_BANDS:
        if score >= floor:
            return label
    return "Below alert threshold"


def params_for(variant: str, overrides: Optional[dict] = None) -> Dict[str, Any]:
    p = dict(DEFAULTS)
    p.update(VARIANTS.get(variant, {}).get("params", {}))
    if overrides:
        p.update({k: v for k, v in overrides.items() if v is not None})
    p["variant"] = variant
    p["strategy_version"] = VERSIONS.get(variant, "1.0.0")
    return p


# ---------------------------------------------------------------- regime ----

def classify_regime(adx: Optional[float],
                    bbw: Optional[float], bbw_hist: Sequence[float],
                    e20: Optional[float], e50: Optional[float],
                    e200: Optional[float]) -> str:
    """Deterministic regime label from measured values only."""
    bbw_pct = percentile_rank(bbw_hist, bbw) if (bbw is not None and bbw_hist) else None
    if bbw_pct is not None and bbw_pct >= 90 and (adx or 0) >= 25:
        return "BREAKOUT"
    if adx is not None and adx >= 25 and e20 and e50:
        return "TRENDING_UP" if e20 > e50 else "TRENDING_DOWN"
    if bbw_pct is not None and bbw_pct >= 80:
        return "HIGH_VOLATILITY"
    if bbw_pct is not None and bbw_pct <= 20:
        return "LOW_VOLATILITY"
    return "RANGE"


def _candle_quality(bars: Sequence[dict], i: int, direction: str) -> Dict[str, Any]:
    """Numeric reversal-candle conditions. Names are labels for the numbers."""
    b = bars[i]
    o, h, l, c = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"])
    rng = max(h - l, 1e-9)
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    prev_c = float(bars[i - 1]["c"]) if i > 0 else c
    prev_o = float(bars[i - 1]["o"]) if i > 0 else o
    mid = (h + l) / 2
    out = {"body_frac": body / rng, "upper_wick_frac": upper_wick / rng,
           "lower_wick_frac": lower_wick / rng, "close_vs_prev": c - prev_c,
           "close_vs_mid": c - mid}
    if direction == "long":
        out["engulfing"] = c > o and prev_c < prev_o and c >= prev_o and o <= prev_c
        out["hammer"] = out["lower_wick_frac"] >= 0.5 and out["body_frac"] <= 0.35
        out["long_lower_wick"] = out["lower_wick_frac"] >= 0.4
        out["closed_up"] = c > prev_c
        out["above_midpoint"] = c > mid
        out["score_frac"] = sum([out["engulfing"], out["hammer"],
                                 out["long_lower_wick"], out["closed_up"],
                                 out["above_midpoint"]]) / 5.0
    else:
        out["engulfing"] = c < o and prev_c > prev_o and c <= prev_o and o >= prev_c
        out["shooting_star"] = out["upper_wick_frac"] >= 0.5 and out["body_frac"] <= 0.35
        out["long_upper_wick"] = out["upper_wick_frac"] >= 0.4
        out["closed_down"] = c < prev_c
        out["below_midpoint"] = c < mid
        out["score_frac"] = sum([out["engulfing"], out["shooting_star"],
                                 out["long_upper_wick"], out["closed_down"],
                                 out["below_midpoint"]]) / 5.0
    return out


def _divergence(bars: Sequence[dict], rsis: Sequence[Optional[float]],
                i: int, direction: str, lookback: int = 30) -> Optional[dict]:
    """Classic RSI divergence against the prior swing inside `lookback` bars.
    Uses only bars[:i+1]."""
    start = max(1, i - lookback)
    if i - start < 6:
        return None
    if direction == "long":
        # prior lowest low, excluding the last 2 bars so it is a distinct swing
        cand = range(start, i - 2)
        prior = min(cand, key=lambda j: float(bars[j]["l"]), default=None)
        if prior is None or rsis[prior] is None or rsis[i] is None:
            return None
        if float(bars[i]["l"]) < float(bars[prior]["l"]) and rsis[i] > rsis[prior]:
            return {"type": "bullish", "prior_idx": prior,
                    "price_prior": float(bars[prior]["l"]), "price_now": float(bars[i]["l"]),
                    "rsi_prior": rsis[prior], "rsi_now": rsis[i]}
    else:
        cand = range(start, i - 2)
        prior = max(cand, key=lambda j: float(bars[j]["h"]), default=None)
        if prior is None or rsis[prior] is None or rsis[i] is None:
            return None
        if float(bars[i]["h"]) > float(bars[prior]["h"]) and rsis[i] < rsis[prior]:
            return {"type": "bearish", "prior_idx": prior,
                    "price_prior": float(bars[prior]["h"]), "price_now": float(bars[i]["h"]),
                    "rsi_prior": rsis[prior], "rsi_now": rsis[i]}
    return None


# ------------------------------------------------------------ the scanner ----

def _prep(bars: Sequence[dict], p: Dict[str, Any]) -> Dict[str, Any]:
    closes = [float(b["c"]) for b in bars]
    vols = [float(b.get("v") or 0) for b in bars]
    return {
        "closes": closes, "vols": vols,
        "rsi": rsi_series(closes, int(p["rsi_length"])),
        "bb": bollinger_series(closes, int(p["bb_length"]), float(p["bb_dev"])),
        "atr": atr_series(bars, 14),
        "adx": adx_series(bars, 14),
        "e20": ema_series(closes, 20), "e50": ema_series(closes, 50),
        "e200": ema_series(closes, 200),
    }


def snapshot(bars: Sequence[dict], i: int, p: Dict[str, Any],
             pre: Optional[dict] = None,
             htf_trend: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Full causal indicator snapshot at bar i."""
    pre = pre or _prep(bars, p)
    bb, rsi = pre["bb"][i], pre["rsi"][i]
    if bb is None or rsi is None:
        return None
    atr = pre["atr"][i]
    vols = pre["vols"]
    vol_sma = (sum(vols[max(0, i - 19):i + 1]) / min(20, i + 1)) if vols else 0.0
    bbw_hist = [x["width"] for x in pre["bb"][max(0, i - 99):i + 1]
                if x and x["width"] is not None]
    b = bars[i]
    e20 = pre["e20"][i] if i < len(pre["e20"]) else None
    e50 = pre["e50"][i] if i < len(pre["e50"]) else None
    e200 = pre["e200"][i] if i < len(pre["e200"]) else None
    prev_bbw = pre["bb"][i - 1]["width"] if i > 0 and pre["bb"][i - 1] else None
    return {
        "i": i, "time": b.get("time"), "o": float(b["o"]), "h": float(b["h"]),
        "l": float(b["l"]), "c": float(b["c"]), "v": float(b.get("v") or 0),
        "rsi": rsi, "rsi_prev": pre["rsi"][i - 1] if i > 0 else None,
        "bb_basis": bb["basis"], "bb_upper": bb["upper"], "bb_lower": bb["lower"],
        "bb_width": bb["width"], "bb_sd": bb["sd"],
        "bb_width_pctile": percentile_rank(bbw_hist, bb["width"]) if bb["width"] is not None else None,
        "bb_width_expansion": ((bb["width"] - prev_bbw) / prev_bbw * 100)
                              if (prev_bbw and bb["width"] is not None) else None,
        "atr": atr, "adx": pre["adx"][i],
        "ema20": e20, "ema50": e50, "ema200": e200,
        "rvol": (float(b.get("v") or 0) / vol_sma) if vol_sma > 0 else None,
        "vwap": session_vwap(bars[max(0, i - 78):i + 1]),
        "htf_trend": htf_trend,
        "regime": classify_regime(pre["adx"][i], bb["width"],
                                  bbw_hist, e20, e50, e200),
        # how many standard deviations the extreme actually reached
        "sigma_low": ((bb["basis"] - float(b["l"])) / bb["sd"]) if bb["sd"] else None,
        "sigma_high": ((float(b["h"]) - bb["basis"]) / bb["sd"]) if bb["sd"] else None,
    }


def _breakout_risk(snap: dict, bars_outside: int, direction: str) -> Dict[str, Any]:
    """A 3σ move is not automatically a reversion. Sometimes it is the start of
    a real trend — in which case fading it is the worst available trade."""
    flags: List[str] = []
    adx, rvol = snap.get("adx"), snap.get("rvol")
    if bars_outside >= 3:
        flags.append(f"price closed outside the band {bars_outside} bars running")
    if (snap.get("bb_width_expansion") or 0) >= 8:
        flags.append(f"band width expanding {snap['bb_width_expansion']:.0f}% per bar")
    if adx is not None and adx >= 35:
        flags.append(f"ADX {adx:.0f} — strong directional trend")
    if rvol is not None and rvol >= 3.0:
        flags.append(f"relative volume {rvol:.1f}x — participation surge")
    e20, e50, e200 = snap.get("ema20"), snap.get("ema50"), snap.get("ema200")
    if e20 and e50 and e200:
        if direction == "short" and e20 > e50 > e200:
            flags.append("higher-timeframe structure stacked bullish against the short")
        if direction == "long" and e20 < e50 < e200:
            flags.append("higher-timeframe structure stacked bearish against the long")
    return {"high": len(flags) >= 2, "flags": flags}


def score_setup(snap: dict, setup: dict, p: Dict[str, Any],
                expectancy_pct: Optional[float] = None) -> Dict[str, Any]:
    """0-100 Setup Quality. NOT a probability of winning — a weighted measure of
    how well this instance matches the conditions the strategy is built on."""
    d = setup["direction"]
    parts: Dict[str, float] = {}
    w = SCORE_WEIGHTS

    # how far past the threshold RSI actually went
    if d == "long":
        depth = max(0.0, float(p["rsi_oversold"]) - setup["rsi_extreme"])
    else:
        depth = max(0.0, setup["rsi_extreme"] - float(p["rsi_overbought"]))
    parts["rsi_extremeness"] = w["rsi_extremeness"] * min(1.0, depth / 10.0)

    pen = setup.get("penetration_atr") or 0.0
    parts["bb_penetration"] = w["bb_penetration"] * min(1.0, pen / 0.75)

    re = setup.get("reentry_atr") or 0.0
    parts["bb_reentry"] = w["bb_reentry"] * min(1.0, re / 0.5)

    rev = setup.get("rsi_reversal") or 0.0
    parts["rsi_reversal"] = w["rsi_reversal"] * min(1.0, rev / 8.0)

    htf = snap.get("htf_trend")
    if htf in (None, "unknown"):
        parts["trend_context"] = w["trend_context"] * 0.5
    elif (d == "long" and htf == "up") or (d == "short" and htf == "down"):
        parts["trend_context"] = float(w["trend_context"])
    elif htf == "neutral":
        parts["trend_context"] = w["trend_context"] * 0.7
    else:
        parts["trend_context"] = w["trend_context"] * 0.15

    adx = snap.get("adx")
    if adx is None:
        parts["adx_suitability"] = w["adx_suitability"] * 0.5
    elif adx < 20:
        parts["adx_suitability"] = float(w["adx_suitability"])
    elif adx < 25:
        parts["adx_suitability"] = w["adx_suitability"] * 0.7
    elif adx < 30:
        parts["adx_suitability"] = w["adx_suitability"] * 0.4
    else:
        parts["adx_suitability"] = 0.0

    rvol = snap.get("rvol")
    parts["volume"] = (w["volume"] * min(1.0, rvol / 2.0)) if rvol else w["volume"] * 0.4
    parts["reversal_candle"] = w["reversal_candle"] * (setup.get("candle", {}).get("score_frac") or 0.0)
    parts["divergence"] = float(w["divergence"]) if setup.get("divergence") else 0.0

    if expectancy_pct is None:
        parts["expectancy"] = w["expectancy"] * 0.5      # unknown, not credited
    else:
        parts["expectancy"] = w["expectancy"] * max(0.0, min(1.0, (expectancy_pct + 1.0) / 2.0))

    total = round(sum(parts.values()), 1)
    return {"score": total, "band": band_of(total),
            "parts": {k: round(v, 2) for k, v in parts.items()},
            "weights": dict(w),
            "expectancy_known": expectancy_pct is not None}


# --------------------------------------------------------- trade levels ----

def plan_levels(snap: dict, setup: dict, p: Dict[str, Any],
                entry: float) -> Dict[str, Any]:
    """Stop from market structure first; targets from the strategy's own thesis
    (the mean). Position size is derived from the stop later, never the reverse."""
    d = setup["direction"]
    atr = snap.get("atr") or (entry * 0.01)
    model, param = p["stop_model"], float(p["stop_param"])
    if model == "extreme_atr":
        stop = (setup["extreme_low"] - param * atr) if d == "long" else \
               (setup["extreme_high"] + param * atr)
        stop_basis = f"beyond the extreme candle ({param}×ATR buffer)"
    elif model == "atr":
        stop = (entry - param * atr) if d == "long" else (entry + param * atr)
        stop_basis = f"{param}×ATR from entry"
    else:
        stop = entry * (1 - param / 100) if d == "long" else entry * (1 + param / 100)
        stop_basis = f"{param}% from entry"

    risk = abs(entry - stop)
    if risk <= 0:
        return {"valid": False, "reason": "stop resolved to the entry price"}

    basis, upper, lower = snap["bb_basis"], snap["bb_upper"], snap["bb_lower"]
    em, ep = p["exit_model"], float(p["exit_param"])
    targets: List[Dict[str, Any]] = []

    def add(name, price, why):
        if price is None:
            return
        if (d == "long" and price <= entry) or (d == "short" and price >= entry):
            return
        targets.append({"name": name, "price": round(price, 4),
                        "r": round(abs(price - entry) / risk, 2), "basis": why})

    if em == "bb_basis":
        add("TP1", basis, "20-period mean (the reversion thesis)")
    elif em == "bb_opposite":
        add("TP1", basis, "20-period mean")
        add("TP2", upper if d == "long" else lower, "opposite Bollinger band")
    elif em == "rr":
        add("TP1", entry + ep * risk if d == "long" else entry - ep * risk,
            f"{ep}R fixed reward multiple")
    elif em == "atr":
        add("TP1", entry + ep * atr if d == "long" else entry - ep * atr,
            f"{ep}×ATR move")
    elif em == "rsi_norm":
        add("TP1", basis, f"mean (exit also triggers when RSI reaches {ep:.0f})")

    # Always offer the mean and the far band as reference rungs when valid.
    if em != "bb_opposite":
        add("TP2", upper if d == "long" else lower, "opposite Bollinger band")
    if len(targets) >= 2:
        far = targets[-1]["price"]
        stretch = far + (far - entry) * 0.45 if d == "long" else far - (entry - far) * 0.45
        add("TP3", stretch, "measured extension beyond the far band")

    # de-duplicate and order outward from entry
    seen, ordered = set(), []
    for t in sorted(targets, key=lambda t: abs(t["price"] - entry)):
        if round(t["price"], 4) in seen:
            continue
        seen.add(round(t["price"], 4))
        t["name"] = f"TP{len(ordered) + 1}"
        ordered.append(t)

    if not ordered:
        return {"valid": False, "reason": "no technically valid target above entry"}

    # Entry quality zones scale with volatility, not a fixed percentage.
    z = 0.15 * atr
    if d == "long":
        ideal = (round(entry - z, 4), round(entry + z, 4))
        acceptable = (round(entry - 2 * z, 4), round(entry + 2 * z, 4))
        no_chase = round(entry + float(p["max_entry_dev_atr"]) * atr, 4)
    else:
        ideal = (round(entry - z, 4), round(entry + z, 4))
        acceptable = (round(entry - 2 * z, 4), round(entry + 2 * z, 4))
        no_chase = round(entry - float(p["max_entry_dev_atr"]) * atr, 4)

    return {"valid": True, "entry": round(entry, 4), "stop": round(stop, 4),
            "stop_basis": stop_basis, "risk_per_unit": round(risk, 4),
            "targets": ordered, "rr_primary": ordered[0]["r"],
            "rr_best": ordered[-1]["r"],
            "entry_zone": {"ideal": ideal, "acceptable": acceptable,
                           "no_chase": no_chase},
            "atr": round(atr, 4)}


def explain(snap: dict, setup: dict, levels: dict, p: Dict[str, Any],
            score: dict) -> List[str]:
    """Deterministic prose, generated from the measured numbers. No AI."""
    d = setup["direction"]
    sig = setup.get("sigma") or 0.0
    side = "below" if d == "long" else "above"
    lines = [
        f"Price traded {sig:.2f} standard deviations {side} its "
        f"{int(p['bb_length'])}-period mean of ${snap['bb_basis']:.2f} — a move that "
        f"size is uncommon and is what this strategy looks for.",
        f"RSI reached {setup['rsi_extreme']:.1f} "
        f"({'oversold' if d == 'long' else 'overbought'} threshold is "
        f"{p['rsi_oversold'] if d == 'long' else p['rsi_overbought']:.0f}).",
    ]
    if setup.get("confirmed"):
        inside = "back inside the lower band" if d == "long" else "back inside the upper band"
        lines.append(f"Price then closed {inside} at ${setup['confirm_close']:.2f}, "
                     f"which is the recovery this strategy waits for rather than "
                     f"buying while price is still falling.")
        if setup.get("rsi_reversal"):
            lines.append(f"RSI turned back up from {setup['rsi_extreme']:.1f} to "
                         f"{setup['rsi_confirm']:.1f}.")
    if snap.get("adx") is not None:
        adx = snap["adx"]
        verdict = ("the market is not in a strong directional trend, which suits "
                   "mean reversion" if adx < 25 else
                   "a strong trend is present, which works against mean reversion")
        lines.append(f"ADX is {adx:.1f} — {verdict}.")
    if snap.get("rvol"):
        lines.append(f"Volume on the extreme was {snap['rvol']:.1f}× its 20-bar average.")
    if setup.get("divergence"):
        dv = setup["divergence"]
        lines.append(f"RSI {dv['type']} divergence present: price made a "
                     f"{'lower low' if dv['type'] == 'bullish' else 'higher high'} "
                     f"while RSI did not.")
    if snap.get("regime"):
        lines.append(f"Market regime classified as {snap['regime'].replace('_', ' ').lower()}.")
    lines.append(f"Setup Quality scored {score['score']:.0f} of 100 "
                 f"({score['band']}). This measures how closely the setup matches "
                 f"the strategy's conditions — it is not a probability of profit.")
    return lines


# ----------------------------------------------------------------- scan ----

def scan(bars: Sequence[dict], p: Dict[str, Any],
         htf_trend: Optional[str] = None,
         expectancy_pct: Optional[float] = None,
         start_at: int = 0) -> List[Dict[str, Any]]:
    """Walk the bar series once, forward, emitting every setup and signal.

    Only bars[:i+1] is ever consulted at index i, so the live scanner and the
    backtester produce identical output for identical input. Bars supplied here
    must be CLOSED bars; the caller drops any forming candle.
    """
    if len(bars) < max(int(p["bb_length"]), int(p["rsi_length"])) + 5:
        return []
    pre = _prep(bars, p)
    out: List[Dict[str, Any]] = []
    active: Dict[str, Optional[dict]] = {"long": None, "short": None}
    bars_outside = {"long": 0, "short": 0}
    rsi_extreme_run = {"long": 0, "short": 0}

    lo_bound = max(int(p["bb_length"]), int(p["rsi_length"]) + 1, start_at)
    for i in range(lo_bound, len(bars)):
        bb, rsi = pre["bb"][i], pre["rsi"][i]
        if bb is None or rsi is None:
            continue
        b = bars[i]
        low, high, close = float(b["l"]), float(b["h"]), float(b["c"])

        # running counters used by the breakout guard
        bars_outside["long"] = bars_outside["long"] + 1 if close < bb["lower"] else 0
        bars_outside["short"] = bars_outside["short"] + 1 if close > bb["upper"] else 0
        rsi_extreme_run["long"] = rsi_extreme_run["long"] + 1 if rsi <= float(p["rsi_oversold"]) else 0
        rsi_extreme_run["short"] = rsi_extreme_run["short"] + 1 if rsi >= float(p["rsi_overbought"]) else 0

        for d in ("long", "short"):
            if d == "short" and not p.get("allow_shorts", True):
                continue
            is_extreme = (low <= bb["lower"] and rsi <= float(p["rsi_oversold"])) \
                if d == "long" else \
                (high >= bb["upper"] and rsi >= float(p["rsi_overbought"]))

            st = active[d]
            # ---- stage 1: extreme detected -------------------------------
            if is_extreme:
                atr_i = pre["atr"][i] or (close * 0.01)
                pen = ((bb["lower"] - low) / atr_i) if d == "long" else \
                      ((high - bb["upper"]) / atr_i)
                if st is None:
                    active[d] = {
                        "direction": d, "setup_idx": i, "setup_time": b.get("time"),
                        "rsi_extreme": rsi, "rsi_min": rsi,
                        "extreme_low": low, "extreme_high": high,
                        "penetration_atr": max(0.0, pen),
                        "sigma": (abs(bb["basis"] - (low if d == "long" else high))
                                  / bb["sd"]) if bb["sd"] else 0.0,
                        "bars_outside": bars_outside[d],
                        "bars_rsi_extreme": rsi_extreme_run[d],
                        "confirmed": False,
                    }
                else:  # extreme deepened — track the true extreme, do not re-alert
                    st["rsi_min"] = min(st["rsi_min"], rsi) if d == "long" else st["rsi_min"]
                    st["rsi_extreme"] = min(st["rsi_extreme"], rsi) if d == "long" \
                        else max(st["rsi_extreme"], rsi)
                    st["extreme_low"] = min(st["extreme_low"], low)
                    st["extreme_high"] = max(st["extreme_high"], high)
                    st["penetration_atr"] = max(st["penetration_atr"], max(0.0, pen))
                    st["setup_idx"] = i
                    st["bars_outside"] = bars_outside[d]
                    st["bars_rsi_extreme"] = rsi_extreme_run[d]
                    st["sigma"] = max(st["sigma"],
                                      (abs(bb["basis"] - (low if d == "long" else high))
                                       / bb["sd"]) if bb["sd"] else 0.0)
                continue

            if st is None:
                continue

            # ---- stage 2: confirmation on a closed candle ----------------
            age = i - st["setup_idx"]
            reentry = (close > bb["lower"]) if d == "long" else (close < bb["upper"])
            prev_rsi = pre["rsi"][i - 1]
            rsi_turn = (prev_rsi is not None and rsi > prev_rsi) if d == "long" \
                else (prev_rsi is not None and rsi < prev_rsi)

            need_reentry = p["confirmation"] and p["require_reentry"]
            need_turn = p["confirmation"] and p["require_rsi_turn"]
            ok = (not need_reentry or reentry) and (not need_turn or rsi_turn)

            if age > int(p["confirm_window"]):
                out.append({"status": "EXPIRED", "direction": d,
                            "setup_idx": st["setup_idx"], "setup_time": st["setup_time"],
                            "expired_idx": i,
                            "reason": f"no confirmation within {p['confirm_window']} bars"})
                active[d] = None
                continue
            if not ok:
                continue

            atr_i = pre["atr"][i] or (close * 0.01)
            st.update({
                "confirmed": True, "confirm_idx": i, "confirm_time": b.get("time"),
                "confirm_close": close, "rsi_confirm": rsi,
                "rsi_reversal": abs(rsi - st["rsi_extreme"]),
                "reentry_atr": (abs(close - bb["lower"]) / atr_i) if d == "long"
                               else (abs(bb["upper"] - close) / atr_i),
                "candle": _candle_quality(bars, i, d),
                "divergence": _divergence(bars, pre["rsi"], st["setup_idx"], d),
            })
            snap = snapshot(bars, i, p, pre, htf_trend)
            active[d] = None
            if snap is None:
                continue

            # ---- hard filters --------------------------------------------
            blocks: List[str] = []
            if p.get("adx_max") is not None and snap["adx"] is not None \
                    and snap["adx"] > float(p["adx_max"]):
                blocks.append(f"ADX {snap['adx']:.0f} above the {p['adx_max']:.0f} "
                              f"limit — trend too strong to fade")
            if p.get("rvol_min") is not None and (snap.get("rvol") or 0) < float(p["rvol_min"]):
                blocks.append(f"relative volume {snap.get('rvol') or 0:.1f}x below "
                              f"the {p['rvol_min']}x minimum")
            if p.get("max_bars_outside") is not None \
                    and st["bars_outside"] > int(p["max_bars_outside"]):
                blocks.append(f"price rode the band for {st['bars_outside']} bars — "
                              f"prolonged extremes behave like trends, not snaps")
            if p.get("htf_filter") == "ema200" and htf_trend not in (None, "unknown"):
                if (d == "long" and htf_trend != "up") or (d == "short" and htf_trend != "down"):
                    blocks.append(f"higher-timeframe trend is {htf_trend}, against the trade")
            br = _breakout_risk(snap, st["bars_outside"], d)
            if p.get("block_breakout_risk") and br["high"]:
                blocks.append("breakout risk high: " + "; ".join(br["flags"]))

            score = score_setup(snap, st, p, expectancy_pct)
            levels = plan_levels(snap, st, p, entry=close)
            if levels.get("valid") is False:
                blocks.append(levels.get("reason", "trade plan could not be built"))

            rec = {
                "status": "NO_TRADE" if blocks else "CONFIRMED",
                "direction": d, "symbol": None,
                "setup_idx": st["setup_idx"], "setup_time": st["setup_time"],
                "confirm_idx": i, "confirm_time": b.get("time"),
                "signal_price": close,
                "score": score["score"], "score_band": score["band"],
                "score_parts": score["parts"],
                "levels": levels if levels.get("valid") else None,
                "snapshot": snap, "setup": {k: v for k, v in st.items()
                                            if k not in ("candle", "divergence")},
                "candle": st.get("candle"), "divergence": st.get("divergence"),
                "breakout_risk": br, "blocks": blocks,
                "bars_outside": st["bars_outside"],
                "bars_rsi_extreme": st["bars_rsi_extreme"],
                "variant": p.get("variant"), "strategy_version": p.get("strategy_version"),
            }
            if not blocks and levels.get("valid"):
                rec["explain"] = explain(snap, st, levels, p, score)
                if score["score"] < float(p.get("min_score", 0)):
                    rec["status"] = "BELOW_THRESHOLD"
                    rec["blocks"] = [f"Setup Quality {score['score']:.0f} below the "
                                     f"{p['min_score']:.0f} alert threshold"]
            out.append(rec)
    return out
