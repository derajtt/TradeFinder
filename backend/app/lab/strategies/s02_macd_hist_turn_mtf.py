"""MACD-histogram turn up from below zero inside a daily uptrend.

A negative MACD histogram on the working timeframe means short-horizon
momentum has been pulling against the daily trend; the first bar on which the
histogram stops falling is when the sellers who pressed the pullback run out
of supply while trend followers positioned off the daily 20>50 stack are still
adding. Momentum persistence after a sanctioned pullback is the effect; the
volume filter demands that the turn was transacted, not merely quoted.
Falsified if turns confirmed by a daily uptrend and above-average volume
continue no better than turns lacking either confirmation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, ema_series, sma

RTH_OPEN, RTH_CLOSE = 570, 960                          # minutes after 00:00 ET

META = StrategyMeta(
    id="s02_macd_hist_turn_mtf",
    name="MACD Histogram Turn, Daily-Trend Confirmed",
    family="momentum",
    category="momentum_pullback",
    hypothesis=" ".join(__doc__.split()),
    markets=["stocks", "etf", "crypto"],
    timeframes=["30min", "1hour", "4hour"],
    hold="swing",
    stop_method="atr",
    params={"vol_mult": 1.25, "atr_mult": 2.0, "rr1": 1.5},
    param_grid={"vol_mult": [1.0, 1.25, 1.5], "atr_mult": [1.5, 2.0, 2.5],
                "rr1": [1.0, 1.5, 2.0]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _macd_hist_series(closes: List[float]) -> List[float]:
    """12/26/9 MACD histogram at every index; ema_series is causal, so
    element i depends only on closes[:i+1]."""
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(line, 9)
    return [a - b for a, b in zip(line, sig)]


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    daily: List[dict] = ctx.get("daily") or []
    if len(bars) < 60 or len(daily) < 50:
        return None
    cur = bars[-1]
    m = int(cur.get("minute_of_day", 0))
    if ctx.get("market") != "crypto" and not (RTH_OPEN <= m < RTH_CLOSE):
        return None                                    # equity fills only in the regular session
    vol_mult, atr_mult, rr1 = float(_p(cfg, "vol_mult")), float(_p(cfg, "atr_mult")), float(_p(cfg, "rr1"))

    hist = _macd_hist_series([float(b["c"]) for b in bars])
    h0, h1, h2 = hist[-1], hist[-2], hist[-3]
    if not (h1 < 0 and h0 < 0 and h0 > h1 and h1 <= h2):   # trough below zero, turning up, not yet crossed
        return None
    vols = [float(b.get("v") or 0) for b in bars]
    vol_avg = sma(vols[:-1], 20)
    if not vol_avg or vol_avg <= 0:
        return None
    vol_ratio = vols[-1] / vol_avg
    if vol_ratio < vol_mult:
        return None
    dc = [float(d["c"]) for d in daily]
    s20, s50 = sma(dc, 20), sma(dc, 50)
    if not s20 or not s50 or not (dc[-1] > s50 and s20 > s50):
        return None
    a = atr(bars, 14)
    if not a or a <= 0:
        return None

    entry = float(cur["c"])
    stop = entry - atr_mult * a
    risk = entry - stop
    t1, t2 = entry + rr1 * risk, entry + 2.0 * rr1 * risk
    turn = (h0 - h1) / abs(h1) if h1 else 0.0                 # share of the trough recovered this bar
    depth = max(abs(x) for x in hist[-50:]) or 1.0
    trough_ratio = abs(h1) / depth
    stack_pct = (s20 / s50 - 1.0) * 100
    dist20 = (dc[-1] / s20 - 1.0) * 100
    comp = {
        "base": 20.0,
        "turn_sharpness": 20.0 * _clamp(turn / 0.5),
        "volume": 20.0 * _clamp((vol_ratio - vol_mult) / vol_mult),
        "trend_stack": 20.0 * _clamp(stack_pct / 5.0),
        "orderly_pullback": 20.0 * _clamp(1.0 - abs(dist20) / 5.0),
    }
    conf = round(min(100.0, max(0.0, sum(comp.values()))), 1)
    reasons = [
        "MACD histogram turned up to %.4g from a trough of %.4g while still below zero (%.0f%% of the trough recovered)"
        % (h0, h1, turn * 100),
        "Bar volume %.0f is %.2fx the prior 20-bar average %.0f" % (vols[-1], vol_ratio, vol_avg),
        "Daily close %.2f is above the 50-day SMA %.2f; the 20-day SMA %.2f sits %.1f%% above the 50-day"
        % (dc[-1], s50, s20, stack_pct),
        "Daily close is %+.1f%% from the 20-day SMA, so the pullback is %s"
        % (dist20, "orderly" if abs(dist20) < 5 else "stretched"),
        "ATR(14) %.4g places the stop %.1f ATR below entry at %.2f" % (a, atr_mult, stop),
    ]
    return Signal(
        direction="long", entry=round(entry, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4), confidence=conf, reasons=reasons,
        invalidation="A close below %.2f (%.1f ATR under entry) means the histogram turn was a pause in the pullback, not its end."
        % (stop, atr_mult),
        expected_bars=12, trailing={"type": "atr", "mult": atr_mult},
        features={"hist": round(h0, 6), "hist_prev": round(h1, 6), "hist_prev2": round(h2, 6),
                  "turn_recovered": round(turn, 3), "trough_ratio_50": round(trough_ratio, 3),
                  "vol_ratio": round(vol_ratio, 2), "vol_avg20": round(vol_avg, 1),
                  "daily_close": dc[-1], "sma20_d": round(s20, 4), "sma50_d": round(s50, 4),
                  "stack_pct": round(stack_pct, 2), "dist_from_sma20_pct": round(dist20, 2),
                  "atr14": round(a, 6), "confidence_components": comp},
    )
