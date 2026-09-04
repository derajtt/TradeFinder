"""VWAP two-sigma band fade on fading volume, range regime only.

Hypothesis: in a range-bound session, the flow that pushes price two
volume-weighted standard deviations below VWAP is usually liquidity demand —
an oversized sell order being worked, a stop cascade, a rebalance — rather
than new information, so the price it prints is not a new consensus value.
Once that flow is absorbed the tape goes quiet (volume declining across three
bars) and the resting bids that were run over pull price back toward the
day's fair value, the VWAP itself, where most of the session's volume has
already cleared. Falsified if two-sigma extensions with fading volume in
range regimes continue toward -3 sigma as often as they close back at VWAP,
or if the reversion is smaller than spread plus slippage.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

RTH_START, RTH_END = 570, 960          # 09:30 and 16:00 ET, minutes of day

META = StrategyMeta(
    id="s06_vwap_band_fade_fading_volume",
    name="VWAP Band Fade on Fading Volume",
    family="mean_reversion",
    category="vwap_reversion",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="structure",
    params={"band_k": 2.0, "stop_buffer_atr": 0.2, "min_rr": 1.0,
            "min_session_bars": 8, "ext_bars": 3},
    param_grid={"band_k": [1.75, 2.0, 2.5],
                "stop_buffer_atr": [0.1, 0.2, 0.35],
                "min_rr": [0.8, 1.0, 1.25]},
    regimes_on=["range"],
    max_hold_bars=24,
)


def _p(cfg: Dict[str, Any], key: str) -> Any:
    return cfg.get(key, META.params[key])


def _session_rth_bars(bars: List[dict]) -> List[dict]:
    """Regular-hours bars of the session that contains bars[-1], oldest first."""
    j = len(bars) - 1
    while j > 0:
        prev, cur = bars[j - 1], bars[j]
        gap = (cur.get("time") or 0) - (prev.get("time") or 0)
        if prev["minute_of_day"] > cur["minute_of_day"] or gap > 3 * 3600:
            break
        j -= 1
    return [b for b in bars[j:] if RTH_START <= b["minute_of_day"] < RTH_END]


def _vwap_bands(bars: List[dict]) -> Optional[Tuple[float, float]]:
    """Session VWAP and its volume-weighted standard deviation."""
    pv = vol = 0.0
    for b in bars:
        v = float(b.get("v") or 0)
        if v > 0:
            pv += (b["h"] + b["l"] + b["c"]) / 3 * v
            vol += v
    if vol <= 0:
        return None
    vwap = pv / vol
    var = sum(float(b["v"]) * ((b["h"] + b["l"] + b["c"]) / 3 - vwap) ** 2
              for b in bars if float(b.get("v") or 0) > 0) / vol
    return vwap, math.sqrt(var)


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx.get("bars") or []
    if ctx.get("regime") != "range":
        return None
    if ctx.get("session") not in ("open", "midday", "power_hour"):
        return None
    if len(bars) < 30:
        return None
    sess = _session_rth_bars(bars)
    if len(sess) < int(_p(cfg, "min_session_bars")):
        return None
    vb = _vwap_bands(sess)
    a = atr(bars, 14)
    if vb is None or vb[1] <= 0 or not a or a <= 0:
        return None
    vwap, sd = vb
    cur, prev = bars[-1], bars[-2]
    band_k = float(_p(cfg, "band_k"))
    ext_low = min(b["l"] for b in bars[-int(_p(cfg, "ext_bars")):])
    z_ext = (vwap - ext_low) / sd            # depth of the extension, in sigmas
    z_close = (cur["c"] - vwap) / sd
    if z_ext < band_k or cur["c"] >= vwap:
        return None
    v3 = [float(b.get("v") or 0) for b in bars[-3:]]
    if not (v3[0] > v3[1] > v3[2] > 0):
        return None                          # volume must fade bar over bar
    vol_ratio = v3[2] / v3[0]
    rng = cur["h"] - cur["l"]
    if rng <= 0 or cur["c"] <= cur["o"] or cur["c"] <= prev["c"]:
        return None                          # need a reversal bar
    close_pos = (cur["c"] - cur["l"]) / rng
    entry = float(cur["c"])
    buffer_atr = float(_p(cfg, "stop_buffer_atr"))
    stop = ext_low - buffer_atr * a
    risk = entry - stop
    if risk <= 0:
        return None
    t1, t2 = vwap, vwap + sd
    rr1 = (t1 - entry) / risk
    if rr1 < float(_p(cfg, "min_rr")):
        return None
    comp = {
        "stretch": min(30.0, 15.0 + (z_ext - band_k) * 20.0),
        "volume_fade": min(25.0, (1.0 - vol_ratio) * 50.0),
        "reversal_bar": close_pos * 15.0,
        "session": {"midday": 10.0, "open": 5.0}.get(ctx.get("session"), 0.0),
    }
    confidence = max(0.0, min(100.0, 20.0 + sum(comp.values())))   # components sum to 80
    expected = max(2, min(20, int(math.ceil((t1 - entry) / a * 2))))
    reasons = [
        f"Range regime; extension low {ext_low:.2f} sits {z_ext:.2f} sigma below "
        f"session VWAP {vwap:.2f}",
        f"Volume faded {v3[0]:.0f} -> {v3[1]:.0f} -> {v3[2]:.0f} over the last 3 bars "
        f"(ratio {vol_ratio:.2f})",
        f"Reversal bar closed {close_pos:.0%} up its range at {entry:.2f}, above the "
        f"prior close {prev['c']:.2f}",
        f"Close is still {abs(z_close):.2f} sigma under VWAP; VWAP target is {rr1:.2f}R "
        f"against stop {stop:.2f}",
        f"{len(sess)} regular-hours bars in the VWAP; ATR(14) {a:.3f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=round(confidence, 1), reasons=reasons,
        invalidation=(f"A close below {stop:.2f} (the extension low less {buffer_atr} "
                      f"ATR) says the seller is not done and the move was informed."),
        expected_bars=expected, trailing=None,
        features={"vwap": vwap, "vwap_sd": sd, "z_ext": z_ext, "z_close": z_close,
                  "ext_low": ext_low, "vol_ratio_3bar": vol_ratio,
                  "close_pos": close_pos, "atr14": a, "session_bars": len(sess),
                  "rr1": rr1, "confidence_components": comp},
    )
