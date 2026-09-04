"""Supertrend (ATR 10, x3) flip to bullish while ADX(14) rises above 20.

A Supertrend flip requires price to close through a band set three volatility
units from the prior swing, a move large enough that stop-loss orders on the
losing side and trend followers' entries trigger together; the band then
ratchets behind price and becomes the structural stop for everyone using it.
Alone the flip whipsaws in chop, so it is traded only while ADX(14) is above
20 and rising: directional movement is already expanding, the condition under
which forced repositioning has continued rather than reversed. The strategy
disables itself when the market regime is "range", because there a flip is
more often the end of a swing than the start of a trend. Falsified if flips
with rising ADX do not reach one risk unit before the Supertrend line is hit
more often than flips with falling ADX, or if range-regime flips turn out to
be just as profitable.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import adx_series, atr, atr_series

META = StrategyMeta(
    id="s14_supertrend_adx_regime",
    name="Supertrend Flip + Rising ADX",
    family="trend",
    category="trend_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="trailing",
    params={"atr_len": 10, "st_mult": 3.0, "adx_min": 20.0, "adx_slope_bars": 3},
    param_grid={"st_mult": [2.0, 3.0, 4.0], "adx_min": [15.0, 20.0, 25.0],
                "adx_slope_bars": [2, 3, 5]},
    regimes_on=["trend_up", "trend_down", "high_vol", "low_vol", "bear"],   # not "range"
    max_hold_bars=60,
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


def _supertrend(bars, n: int, mult: float) -> Tuple[List[Optional[float]], List[int]]:
    """Standard Supertrend. Returns (line, direction) per bar; direction +1 when
    the line is below price (bullish), -1 when above, 0 before ATR warm-up.
    Band ratchets use the PRIOR close only, so nothing repaints."""
    atrs = atr_series(bars, n)
    line: List[Optional[float]] = [None] * len(bars)
    dirn = [0] * len(bars)
    fu = fl = None
    for i, a in enumerate(atrs):
        if a is None:
            continue
        h, l, c = float(bars[i]["h"]), float(bars[i]["l"]), float(bars[i]["c"])
        hl2 = (h + l) / 2.0
        bu, bl = hl2 + mult * a, hl2 - mult * a
        if fu is None:
            fu, fl = bu, bl
            dirn[i] = 1 if c >= hl2 else -1
        else:
            pc = float(bars[i - 1]["c"])
            fu = bu if (bu < fu or pc > fu) else fu
            fl = bl if (bl > fl or pc < fl) else fl
            if dirn[i - 1] == -1:
                dirn[i] = 1 if c > fu else -1
            else:
                dirn[i] = -1 if c < fl else 1
        line[i] = fl if dirn[i] == 1 else fu
    return line, dirn


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = dict(META.params)
    p.update(cfg or {})
    n, mult = int(p["atr_len"]), float(p["st_mult"])
    adx_min, k = float(p["adx_min"]), int(p["adx_slope_bars"])
    regime = str(ctx.get("regime") or "")
    if regime == "range":                       # flips in a range are swing ends, not trend starts
        return None
    bars = (ctx.get("bars") or [])[-MAX_BARS:]
    if len(bars) < MIN_BARS:
        return None
    line, dirn = _supertrend(bars, n, mult)
    adx = adx_series(bars, 14)
    if adx[-1] is None or adx[-1 - k] is None or line[-2] is None:
        return None
    adx_delta = adx[-1] - adx[-1 - k]
    if adx[-1] < adx_min or adx_delta <= 0:
        return None
    short_ok = ctx.get("market") in ("crypto", "etf")
    if dirn[-1] == 1 and dirn[-2] == -1:
        d, flip = 1, "bullish"
    elif dirn[-1] == -1 and dirn[-2] == 1 and short_ok:
        d, flip = -1, "bearish"
    else:
        return None
    close, st, prev_line = float(bars[-1]["c"]), line[-1], line[-2]
    a = atr(bars, n)
    risk = (close - st) * d
    if not a or a <= 0 or risk <= 0:
        return None
    t1 = close + d * 1.0 * risk                 # risk is ~3 ATR wide, so 1R/2R are real distances
    t2 = close + d * 2.0 * risk

    margin = (close - prev_line) * d / a         # how far the close punched through the old band
    vr = _vol_ratio(bars, 20)
    comps = {"adx_level": _scale(adx[-1], adx_min, adx_min + 20.0, 30),
             "adx_slope": _scale(adx_delta, 0.0, 5.0, 20),
             "margin": _scale(margin, 0.0, 1.0, 20),
             "volume": _scale(vr, 1.0, 2.0, 15)}
    conf = 15.0 + sum(comps.values())
    reasons = [
        f"Supertrend({n}, x{mult:.1f}) flipped {flip}: close {close:.2f} through the prior "
        f"band {prev_line:.2f} by {margin:.2f} ATR",
        f"ADX(14) {adx[-1]:.1f} is above {adx_min:.0f} and up {adx_delta:.1f} points over {k} bars",
        f"Regime '{regime or 'unknown'}' is not 'range' (where this strategy stands down); "
        f"ATR({n}) {a:.2f} x {mult:.1f} sets the band",
        f"Volume {vr:.2f}x the 20-bar average on the flip bar",
        f"Stop is the new Supertrend line {st:.2f}; risk {risk:.2f} = {risk / a:.2f} ATR({n})",
    ]
    return Signal(
        direction="long" if d == 1 else "short",
        entry=round(close, 4), stop=round(st, 4),
        target1=round(t1, 4), target2=round(t2, 4),
        confidence=round(min(100.0, conf), 1),
        reasons=reasons,
        invalidation=(f"A close back through the Supertrend line at {st:.2f} flips the "
                      f"indicator {'bearish' if d == 1 else 'bullish'} and voids the entry."),
        expected_bars=30,
        trailing={"type": "atr", "mult": mult},
        features={"supertrend": round(st, 4), "prev_band": round(prev_line, 4),
                  "adx14": round(adx[-1], 2), "adx_delta": round(adx_delta, 2),
                  "atr": round(a, 4), "margin_atr": round(margin, 3),
                  "vol_ratio": round(vr, 3), "regime": regime,
                  "conf_components": {k_: round(v, 1) for k_, v in comps.items()}},
    )
