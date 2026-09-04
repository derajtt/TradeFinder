"""Gaussian-filter channel break with the daily Gaussian channel rising.

A cascaded Gaussian filter has near-zero overshoot and, for equal smoothing,
less lag than a moving average, so price crossing its volatility band marks a
genuine departure from the recent equilibrium rather than the noise crossings
that plague MA breakouts; participants keyed to smooth trend definitions all
see the same change at once and reposition together. Requiring the daily
Gaussian midline to be rising as well means the intraday break trades with,
not against, the multi-day flow of capital, which is where breakouts have
persisted. The stop is a volatility distance under entry because the filter
adapts slowly by construction and cannot define invalidation fast enough on a
failed break. Falsified if band breaks under a rising daily channel do not
outperform those under a falling one, or if the filter's break points prove
no more persistent than 20-EMA crosses on the same bars.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, gaussian_filter

# Long only: the mechanism as briefed is a rising daily channel.
META = StrategyMeta(
    id="s13_gaussian_channel_mtf",
    name="Gaussian Channel Break (daily-confirmed)",
    family="trend",
    category="trend_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["15min", "30min", "1hour", "4hour"],
    hold="swing",
    stop_method="atr",
    params={"period": 20, "poles": 3, "band_mult": 1.414, "atr_mult": 1.5,
            "daily_slope_bars": 3},
    param_grid={"period": [14, 20, 30], "band_mult": [1.0, 1.414, 2.0],
                "atr_mult": [1.0, 1.5, 2.0]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)

MAX_BARS = 400   # trailing window: pure function of the supplied slice, bounds cost
MIN_BARS = 60


def _scale(x: float, lo: float, hi: float, pts: float) -> float:
    if hi == lo:
        return 0.0
    return pts * max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _vol_ratio(bars, n: int = 20) -> float:
    vols = [float(b.get("v") or 0) for b in bars[-(n + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 0.0
    v = float(bars[-1].get("v") or 0)
    return v / avg if avg > 0 and v > 0 else 1.0


def _true_ranges(bars) -> List[float]:
    out = [float(bars[0]["h"]) - float(bars[0]["l"])]
    for i in range(1, len(bars)):
        h, l, pc = float(bars[i]["h"]), float(bars[i]["l"]), float(bars[i - 1]["c"])
        out.append(max(h - l, abs(h - pc), abs(l - pc)))
    return out


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = dict(META.params)
    p.update(cfg or {})
    period, poles = int(p["period"]), int(p["poles"])
    band_mult, atr_mult = float(p["band_mult"]), float(p["atr_mult"])
    slope_bars = int(p["daily_slope_bars"])
    bars = (ctx.get("bars") or [])[-MAX_BARS:]
    daily = (ctx.get("daily") or [])[-MAX_BARS:]
    if len(bars) < max(MIN_BARS, 2 * period) or len(daily) < 2 * period + slope_bars:
        return None
    closes = [float(b["c"]) for b in bars]
    mid = gaussian_filter(closes, period, poles)
    half = gaussian_filter(_true_ranges(bars), period, poles)   # smoothed TR = band width
    if half[-1] <= 0:
        return None
    upper = [m + band_mult * w for m, w in zip(mid, half)]
    close = closes[-1]
    if not (close > upper[-1] and closes[-2] <= upper[-2]):    # FRESH break only
        return None
    if mid[-1] <= mid[-2]:                                       # working channel must be turning up
        return None
    dmid = gaussian_filter([float(b["c"]) for b in daily], period, poles)
    if dmid[-1] <= dmid[-1 - slope_bars]:                        # daily channel rising
        return None
    a = atr(bars, 14)
    if not a or a <= 0:
        return None
    stop = close - atr_mult * a
    risk = close - stop
    t1, t2 = close + 1.5 * risk, close + 3.0 * risk

    margin = (close - upper[-1]) / a
    mid_slope = (mid[-1] - mid[-4]) / a
    daily_slope_pct = (dmid[-1] / dmid[-1 - slope_bars] - 1.0) * 100.0
    band_atr = band_mult * half[-1] / a
    vr = _vol_ratio(bars, 20)
    comps = {"margin": _scale(margin, 0.0, 0.75, 25),
             "mid_slope": _scale(mid_slope, 0.0, 0.5, 20),
             "daily_slope": _scale(daily_slope_pct, 0.0, 2.0, 20),
             "volume": _scale(vr, 1.0, 2.0, 15)}
    conf = 20.0 + sum(comps.values())
    reasons = [
        f"Close {close:.2f} broke above the Gaussian upper band {upper[-1]:.2f} by "
        f"{margin:.2f} ATR; prior close {closes[-2]:.2f} was still inside",
        f"Gaussian midline ({period}-bar, {poles}-pole) rising {mid_slope:.2f} ATR over 3 bars",
        f"Daily Gaussian midline up {daily_slope_pct:.2f}% over the last {slope_bars} sessions",
        f"Channel half-width {band_mult * half[-1]:.2f} = {band_atr:.2f} ATR",
        f"Volume {vr:.2f}x the 20-bar average on the break bar",
        f"Stop {stop:.2f} is {atr_mult:.1f} x ATR(14) {a:.2f} under entry",
    ]
    return Signal(
        direction="long",
        entry=round(close, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4),
        confidence=round(min(100.0, conf), 1),
        reasons=reasons,
        invalidation=(f"A close back below the Gaussian midline {mid[-1]:.2f}, or under "
                      f"{stop:.2f}, returns price to the channel's equilibrium and voids the break."),
        expected_bars=20,
        trailing={"type": "atr", "mult": 2.0},
        features={"gauss_mid": round(mid[-1], 4), "gauss_upper": round(upper[-1], 4),
                  "half_width": round(band_mult * half[-1], 4), "atr14": round(a, 4),
                  "margin_atr": round(margin, 3), "mid_slope_atr": round(mid_slope, 3),
                  "daily_mid": round(dmid[-1], 4), "daily_slope_pct": round(daily_slope_pct, 3),
                  "vol_ratio": round(vr, 3),
                  "conf_components": {k: round(v, 1) for k, v in comps.items()}},
    )
