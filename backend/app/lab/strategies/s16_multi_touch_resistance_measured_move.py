"""A resistance level that has rejected price three or more times stacks two
pools of resting orders above it: buy-stops from the shorts who sold each
rejection, and breakout buy-stops from traders waiting for the level to give
way. When a bar finally closes through the whole zone on volume well above
recent norms, both pools are forced to buy at once, so the move tends to travel
at least the height of the zone before that forced flow is spent; the more
touches, the larger the pools. Falsified if closes through >=3-touch zones on
>=1.8x median volume do not reach one zone height before falling back under
the zone more often than plain single-touch breakouts do, which would mean the
level's "memory" adds nothing.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, pivots, resistance_zones

META = StrategyMeta(
    id="s16_multi_touch_resistance_measured_move",
    name="Multi-Touch Resistance Measured Move",
    family="breakout",
    category="breakout_vol",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "crypto"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="structure",
    params={"lookback": 120, "tol_pct": 0.6, "min_touches": 3, "vol_mult": 1.8,
            "vol_lookback": 20, "max_ext_pct": 1.0, "min_height_atr": 1.0,
            "stop_buffer_atr": 0.25},
    param_grid={"lookback": [80, 120, 200], "tol_pct": [0.4, 0.6, 0.9],
                "min_touches": [3, 4, 5], "vol_mult": [1.5, 1.8, 2.2]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)

_REGIME_ADJ = {"trend_up": 10, "range": 4, "high_vol": 2, "low_vol": 0,
               "trend_down": -6, "bear": -10}


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _best_zone(window: List[dict], close: float, prev_close: float,
               tol_pct: float, min_touches: int) -> Optional[dict]:
    """Highest >=min_touches zone whose whole band the current bar is the first to close above."""
    zones = resistance_zones(window, tol_pct=tol_pct, min_touches=min_touches)
    if not zones:
        return None
    highs, _ = pivots(window)
    best = None
    for z in zones:
        lvl = z["level"]
        members = [ph for _, ph in highs if abs(ph - lvl) / lvl * 100 <= tol_pct]
        if len(members) < min_touches:
            continue
        top, bottom = max(members), min(members)
        if close <= top or prev_close > top:          # not through the band, or not the first close through it
            continue
        if best is None or top > best["top"]:
            best = {"level": lvl, "top": top, "bottom": bottom, "touches": len(members)}
    return best


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    bars = ctx.get("bars") or []
    lookback = int(p["lookback"])
    if len(bars) < max(lookback + 1, 30):
        return None
    cur, prev = bars[-1], bars[-2]
    close = float(cur["c"])
    window = bars[-lookback - 1:-1]                    # the zone is built strictly before the break bar
    zone = _best_zone(window, close, float(prev["c"]), float(p["tol_pct"]), int(p["min_touches"]))
    if zone is None:
        return None
    ext_pct = (close - zone["top"]) / zone["top"] * 100
    if ext_pct > float(p["max_ext_pct"]):
        return None                                   # already too far through the zone: chasing
    a = atr(bars, 14)
    if not a or a <= 0:
        return None
    vol_lb = int(p["vol_lookback"])
    med_vol = _median([float(b.get("v") or 0) for b in window[-vol_lb:]])
    if med_vol <= 0:
        return None
    vol_ratio = float(cur.get("v") or 0) / med_vol
    if vol_ratio < float(p["vol_mult"]):
        return None

    height = max(zone["top"] - zone["bottom"], float(p["min_height_atr"]) * a)
    stop = zone["bottom"] - float(p["stop_buffer_atr"]) * a
    target1 = zone["top"] + height
    target2 = zone["top"] + 2 * height
    if target1 <= close or stop >= close:
        return None

    rng = float(cur["h"]) - float(cur["l"])
    close_loc = (close - float(cur["l"])) / rng if rng > 0 else 0.5
    regime = str(ctx.get("regime") or "")
    comp = {
        "base": 30.0,                                  # every factor saturated sums to exactly 100
        "touches": min(18.0, 6.0 * (zone["touches"] - int(p["min_touches"]))),
        "volume": 20.0 * _clamp((vol_ratio - float(p["vol_mult"])) / 1.7, 0.0, 1.0),
        "proximity": 12.0 * (1.0 - ext_pct / float(p["max_ext_pct"])),
        "close_location": 10.0 * close_loc,
        "regime": float(_REGIME_ADJ.get(regime, 0)),
    }
    confidence = round(_clamp(sum(comp.values()), 0.0, 100.0), 1)
    expected = int(_clamp(round(height / a * 3), 4, 40))

    reasons = [
        f"Resistance zone {zone['bottom']:.2f}-{zone['top']:.2f} was tested {zone['touches']} times "
        f"in the last {lookback} bars",
        f"Bar closed at {close:.2f}, {ext_pct:.2f}% above the zone top and the first close through the band",
        f"Volume {vol_ratio:.1f}x the {vol_lb}-bar median of {med_vol:,.0f}",
        f"Zone height {height:.2f} projects target1 {target1:.2f} and target2 {target2:.2f}",
        f"Stop {stop:.2f} sits {p['stop_buffer_atr']} ATR ({a:.2f}) under the zone bottom",
        f"Regime {regime or 'unknown'} contributes {comp['regime']:+.0f} confidence",
    ]
    return Signal(
        direction="long", entry=close, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back below the zone top {zone['top']:.2f} means the breakout failed "
                      f"and the trapped-short flow never arrived."),
        expected_bars=expected, trailing=None,
        features={"zone_level": zone["level"], "zone_top": zone["top"], "zone_bottom": zone["bottom"],
                  "touches": zone["touches"], "zone_height": height, "ext_pct": ext_pct,
                  "vol_ratio": vol_ratio, "median_vol": med_vol, "atr14": a,
                  "close_location": close_loc, "regime": regime, "confidence_components": comp},
    )
