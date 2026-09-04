"""Donchian 20-bar breakout with 10-bar exit and 2-ATR trail (Turtle-derived).

A close beyond the prior 20-bar extreme forces two groups to trade in the same
direction: holders of the losing side of the range who are stopped out, and
systematic trend followers whose entry rules trigger on exactly this level, and
because those positions are built over several bars rather than in one, the
initial move tends to persist. The ADX > 20 filter demands that directional
movement already dominates noise, so the break is not the first tick out of a
dead-flat box. This is a low-win-rate, high-payoff system by design: most
breakouts fail and are cut at the 10-bar exit or the 2-ATR trail, and the
entire edge lives in the minority that run several multiples of initial risk.
Falsified if the average winner is not at least twice the average loser, or if
breaks revert to the channel midpoint more often than they extend by one
channel height.
"""
from typing import Any, Dict, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import adx_series, atr

META = StrategyMeta(
    id="s11_donchian_atr_trail",
    name="Donchian 20 Breakout / ATR Trail",
    family="breakout",
    category="trend_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="trailing",
    params={"entry_len": 20, "exit_len": 10, "atr_mult": 2.0, "adx_min": 20.0},
    param_grid={"entry_len": [15, 20, 30], "exit_len": [7, 10, 15],
                "atr_mult": [1.5, 2.0, 2.5], "adx_min": [15.0, 20.0, 25.0]},
    regimes_on=None,
    max_hold_bars=60,
    version="1.0.0",
)

MAX_BARS = 400   # trailing window: a pure function of the supplied slice, bounds cost
MIN_BARS = 60    # ADX(14) needs ~2n bars to exist and more to settle


def _scale(x: float, lo: float, hi: float, pts: float) -> float:
    """Linear 0..pts as x moves lo..hi, clipped."""
    if hi == lo:
        return 0.0
    return pts * max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _vol_ratio(bars, n: int = 20) -> float:
    vols = [float(b.get("v") or 0) for b in bars[-(n + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 0.0
    v = float(bars[-1].get("v") or 0)
    return v / avg if avg > 0 and v > 0 else 1.0


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = dict(META.params)
    p.update(cfg or {})
    n_in, n_out = int(p["entry_len"]), int(p["exit_len"])
    mult, adx_min = float(p["atr_mult"]), float(p["adx_min"])
    bars = (ctx.get("bars") or [])[-MAX_BARS:]
    if len(bars) < max(MIN_BARS, n_in + 1, n_out + 1):
        return None
    close = float(bars[-1]["c"])
    a = atr(bars, 14)
    adx = adx_series(bars, 14)[-1]
    if not a or a <= 0 or adx is None or adx < adx_min:
        return None
    prior = bars[-(n_in + 1):-1]                 # the channel EXCLUDES the current bar
    hi_n = max(float(b["h"]) for b in prior)
    lo_n = min(float(b["l"]) for b in prior)
    # Fresh break only: the previous close must have been inside ITS OWN channel,
    # otherwise every new bar of an established run would re-enter.
    prev_ch = bars[-(n_in + 2):-2]
    prev_close = float(bars[-2]["c"])
    short_ok = ctx.get("market") in ("crypto", "etf")
    if close > hi_n and prev_close <= max(float(b["h"]) for b in prev_ch):
        d, level, side, word = 1, hi_n, "above", "high"
    elif close < lo_n and short_ok and prev_close >= min(float(b["l"]) for b in prev_ch):
        d, level, side, word = -1, lo_n, "below", "low"
    else:
        return None
    # Exit on whichever comes first: the 2N hard stop or the n_out-bar channel.
    # Never tighter than 1 ATR so a flat base cannot produce a noise stop.
    exit_bars = bars[-n_out:]
    if d == 1:
        chan_exit = min(float(b["l"]) for b in exit_bars)
        stop = min(max(close - mult * a, chan_exit), close - a)
    else:
        chan_exit = max(float(b["h"]) for b in exit_bars)
        stop = max(min(close + mult * a, chan_exit), close + a)
    risk = abs(close - stop)
    if risk <= 0:
        return None
    t1 = close + d * 2.0 * risk
    t2 = close + d * 4.0 * risk

    margin = (close - level) * d / a
    width = (hi_n - lo_n) / a
    vr = _vol_ratio(bars, 20)
    comps = {"adx": _scale(adx, adx_min, adx_min + 20.0, 30),
             "margin": _scale(margin, 0.0, 1.0, 25),
             "volume": _scale(vr, 1.0, 2.0, 20),
             "channel_width": _scale(width, 3.0, 8.0, 15)}
    conf = 10.0 + sum(comps.values())
    reasons = [
        f"Close {close:.2f} {side} the prior {n_in}-bar {word} {level:.2f} by {margin:.2f} ATR",
        f"ADX(14) {adx:.1f} clears the {adx_min:.0f} filter: directional movement dominates noise",
        f"Volume {vr:.2f}x the 20-bar average on the breakout bar",
        f"{n_in}-bar channel {lo_n:.2f}-{hi_n:.2f} spans {width:.1f} ATR",
        f"Stop {stop:.2f} is the tighter of the {mult:.1f}-ATR hard stop and the "
        f"{n_out}-bar exit {chan_exit:.2f}; risk {risk:.2f} = {risk / a:.2f} ATR",
    ]
    return Signal(
        direction="long" if d == 1 else "short",
        entry=round(close, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4),
        confidence=round(min(100.0, conf), 1),
        reasons=reasons,
        invalidation=(f"A close back inside the {n_in}-bar channel past {level:.2f} negates "
                      f"the breakout; the {n_out}-bar {word} then exits regardless of P&L."),
        expected_bars=n_in + n_out,
        trailing={"type": "atr", "mult": mult},
        features={"adx14": round(adx, 2), "atr14": round(a, 4),
                  "margin_atr": round(margin, 3), "vol_ratio": round(vr, 3),
                  "channel_hi": round(hi_n, 4), "channel_lo": round(lo_n, 4),
                  "channel_width_atr": round(width, 2), "exit_level": round(chan_exit, 4),
                  "conf_components": {k: round(v, 1) for k, v in comps.items()}},
    )
