"""Momentum ignition: accelerating range and volume through a 20-bar high.

Three consecutive bars of expanding range on rising volume, ending with a
volume surge more than two standard deviations above the recent norm and a
close through the prior 20-bar high, is the signature of feedback trading:
breakout systems, stop orders resting above the high and short covering all
buy the same move, and each execution recruits the next. The edge is the short
continuation while that cascade runs; it decays fast, so the stop trails ATR
rather than waiting on a distant target. It must never fire on the first bar
of a session, where range and volume expansion are mechanical. Falsified if
ignition bars are followed by no more upside than random bars with the same
ATR-normalised extension.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

RTH_OPEN, RTH_CLOSE = 570, 960                          # minutes after 00:00 ET
VOL_BASELINE = 20                                       # prior bars for the volume z-score

META = StrategyMeta(
    id="s05_momentum_ignition_accel",
    name="Momentum Ignition (Range + Volume Acceleration)",
    family="momentum",
    category="momentum_ignition",
    hypothesis=" ".join(__doc__.split()),
    markets=["stocks", "etf", "crypto"],
    timeframes=["5min", "15min", "30min"],
    hold="intraday",
    stop_method="trailing",
    params={"vol_z_min": 2.0, "breakout_len": 20, "atr_mult": 2.0},
    param_grid={"vol_z_min": [1.5, 2.0, 2.5], "breakout_len": [15, 20, 30],
                "atr_mult": [1.5, 2.0, 2.5]},
    regimes_on=None,
    max_hold_bars=36,
    version="1.0.0",
)


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _day_key(b: dict) -> int:
    return (int(b.get("time", 0)) - int(b.get("minute_of_day", 0)) * 60) // 86400


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    if not bars:
        return None
    z_min, n, atr_mult = float(_p(cfg, "vol_z_min")), int(_p(cfg, "breakout_len")), float(_p(cfg, "atr_mult"))
    # Equities work on regular-session bars only, so the volume baseline is not
    # a premarket trickle that any 9:35 bar would clear. Crypto trades 24h.
    work = bars if ctx.get("market") == "crypto" else [
        b for b in bars if RTH_OPEN <= b.get("minute_of_day", 0) < RTH_CLOSE]
    if len(work) < max(n, VOL_BASELINE) + 4 or work[-1] is not bars[-1]:
        return None
    b3 = work[-3:]
    dk = _day_key(b3[-1])
    if any(_day_key(b) != dk for b in b3):       # first bars of a session, or a run straddling sessions
        return None
    rng = [float(b["h"]) - float(b["l"]) for b in b3]
    vol = [float(b.get("v") or 0) for b in b3]
    cl = [float(b["c"]) for b in b3]
    cur = b3[-1]
    if not (rng[0] > 0 and rng[2] > rng[1] > rng[0]):
        return None
    if not (vol[0] > 0 and vol[2] > vol[1] > vol[0]):
        return None
    if not (cl[2] > cl[1] > cl[0] and cur["c"] > cur["o"]):
        return None
    base = [float(b.get("v") or 0) for b in work[-VOL_BASELINE - 1:-1]]
    mean = sum(base) / len(base)
    sd = math.sqrt(sum((x - mean) ** 2 for x in base) / len(base))
    if sd <= 0:
        return None
    z = (vol[2] - mean) / sd
    if z < z_min:
        return None
    prior_high = max(float(b["h"]) for b in work[-n - 1:-1])
    if cur["c"] <= prior_high:
        return None
    a = atr(work, 14)
    if not a or a <= 0:
        return None
    ext_atr = (float(cur["c"]) - prior_high) / a
    if ext_atr > 1.0 or rng[2] > 4.0 * a:        # already extended, or a climactic bar
        return None

    entry = float(cur["c"])
    stop = entry - atr_mult * a
    risk = entry - stop
    t1, t2 = entry + 1.5 * risk, entry + 3.0 * risk
    close_loc = (entry - float(cur["l"])) / rng[2]
    ign_low = min(float(b["l"]) for b in b3)
    expansion = rng[2] / rng[0]
    ramp = vol[2] / vol[0]
    comp = {
        "base": 15.0,
        "volume_z": 25.0 * _clamp((z - z_min) / z_min),
        "range_expansion": 20.0 * _clamp((expansion - 1.0) / 2.0),
        "close_location": 15.0 * _clamp(close_loc),
        "low_extension": 15.0 * (1.0 - ext_atr),
        "volume_ramp": 10.0 * _clamp((ramp - 1.0) / 2.0),
    }
    conf = round(min(100.0, max(0.0, sum(comp.values()))), 1)
    reasons = [
        "Three bars expanded range %.4g -> %.4g -> %.4g (%.1fx)" % (rng[0], rng[1], rng[2], expansion),
        "Volume rose %.0f -> %.0f -> %.0f; the last bar is %.1f standard deviations above the prior %d-bar mean %.0f"
        % (vol[0], vol[1], vol[2], z, VOL_BASELINE, mean),
        "Close %.2f cleared the %d-bar high %.2f by %.2f ATR" % (entry, n, prior_high, ext_atr),
        "Closed %.0f%% up its own range with three rising closes" % (close_loc * 100),
        "ATR(14) %.4g; initial stop %.2f is %.1f ATR below entry and trails at that distance (ignition low %.2f)"
        % (a, stop, atr_mult, ign_low),
    ]
    return Signal(
        direction="long", entry=round(entry, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4), confidence=conf, reasons=reasons,
        invalidation="A close %.1f ATR below the running high means the buyers the surge recruited have been absorbed and the cascade is over."
        % atr_mult,
        expected_bars=8, trailing={"type": "atr", "mult": atr_mult},
        features={"range_seq": [round(x, 6) for x in rng], "range_expansion": round(expansion, 2),
                  "vol_seq": vol, "vol_ramp": round(ramp, 2), "vol_z": round(z, 2), "vol_mean20": round(mean, 1),
                  "vol_sd20": round(sd, 1), "prior_high": prior_high, "ext_atr": round(ext_atr, 3),
                  "close_loc": round(close_loc, 3), "ignition_low": ign_low, "atr14": round(a, 6),
                  "minute_of_day": int(cur.get("minute_of_day", 0)), "confidence_components": comp},
    )
