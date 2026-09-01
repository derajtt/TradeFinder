"""Pure feature math. Unit-tested; no I/O. All functions treat inputs as untrusted."""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence


def gap_pct(price: Optional[float], previous_close: Optional[float]) -> Optional[float]:
    if price is None or previous_close is None or previous_close <= 0:
        return None
    return (price - previous_close) / previous_close * 100.0


def spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid * 100.0


def vwap(bars: Sequence[dict]) -> Optional[float]:
    """bars: [{high, low, close, volume}] — typical-price VWAP."""
    num = 0.0
    den = 0.0
    for b in bars:
        try:
            tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
            v = float(b["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if v <= 0 or tp <= 0:
            continue
        num += tp * v
        den += v
    return (num / den) if den > 0 else None


def cumulative_volume(bars: Sequence[dict]) -> float:
    total = 0.0
    for b in bars:
        try:
            v = float(b["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            total += v
    return total


def dollar_volume(bars: Sequence[dict]) -> float:
    total = 0.0
    for b in bars:
        try:
            total += max(0.0, float(b["close"])) * max(0.0, float(b["volume"]))
        except (KeyError, TypeError, ValueError):
            continue
    return total


def premarket_rvol(today_cum_volume: float,
                   baseline_cum_volumes: Sequence[float],
                   min_sessions: int = 5) -> Dict[str, Optional[float]]:
    """Time-adjusted RVOL: today's 4:00am->now cumulative volume vs the median of the
    same-through-minute cumulative volume across up to 10 prior sessions.
    Returns rvol=None when baseline coverage is insufficient (never fabricates)."""
    clean = [v for v in baseline_cum_volumes if isinstance(v, (int, float)) and v > 0]
    coverage = len(clean)
    if coverage < min_sessions or today_cum_volume <= 0:
        return {"rvol": None, "baseline_median": None, "coverage": coverage,
                "confidence": 0.0}
    base = median(clean)
    if base <= 0:
        return {"rvol": None, "baseline_median": None, "coverage": coverage,
                "confidence": 0.0}
    confidence = min(1.0, coverage / 10.0)
    return {"rvol": today_cum_volume / base, "baseline_median": base,
            "coverage": coverage, "confidence": confidence}


def volume_acceleration(current_5m_volume: float,
                        prior_5m_windows: Sequence[float]) -> Optional[float]:
    clean = [v for v in prior_5m_windows if isinstance(v, (int, float)) and v > 0]
    if not clean or current_5m_volume <= 0:
        return None
    base = median(clean)
    return current_5m_volume / base if base > 0 else None


def structure_features(bars: Sequence[dict]) -> Dict[str, Optional[float]]:
    """Premarket high/low, distance from high, and higher-high/higher-low structure."""
    highs: List[float] = []
    lows: List[float] = []
    closes: List[float] = []
    for b in bars:
        try:
            h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if h > 0 and l > 0:
            highs.append(h)
            lows.append(l)
            closes.append(c)
    if not highs:
        return {"pm_high": None, "pm_low": None, "dist_from_high_pct": None,
                "hh_hl": None, "last_close": None}
    pm_high, pm_low = max(highs), min(lows)
    last = closes[-1]
    dist = (pm_high - last) / pm_high * 100.0 if pm_high > 0 else None
    hh_hl = None
    if len(highs) >= 6:
        third = max(1, len(highs) // 3)
        h1, h2, h3 = (max(highs[:third]), max(highs[third:2 * third]), max(highs[2 * third:]))
        l1, l2, l3 = (min(lows[:third]), min(lows[third:2 * third]), min(lows[2 * third:]))
        hh_hl = 1.0 if (h3 >= h2 >= h1 and l3 >= l2 >= l1) else 0.0
    return {"pm_high": pm_high, "pm_low": pm_low, "dist_from_high_pct": dist,
            "hh_hl": hh_hl, "last_close": last}
