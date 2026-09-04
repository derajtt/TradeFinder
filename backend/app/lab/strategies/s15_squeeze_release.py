"""Volatility squeeze release: Bollinger(20,2) inside Keltner(20,1.5), then break.

Volatility clusters, so quiet stretches are followed by loud ones, and when the
Bollinger(20,2) bands sit inside the Keltner(20,1.5) channel for six or more
bars, realised variance has fallen far enough below its range-based norm that
option sellers, range traders and stop placers have all crowded into a narrow
zone. The first close outside the Bollinger band on volume above 1.5x average
is where that crowd is forced to adjust at once: shorts inside the box cover,
breakout systems trigger and gamma hedgers chase, which is why compression
tends to resolve into a move roughly proportional to the box that held it.
The stop is the squeeze midline because a return to the centre of the box
means the expansion failed and range holders are back in control; the target
is twice the squeeze height, the measured move implied by the compression.
Falsified if release bars do not travel one squeeze height before returning
to the midline more often than random bars of equal volume do.
"""
from typing import Any, Dict, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, atr_series, bollinger_series, ema_series

META = StrategyMeta(
    id="s15_squeeze_release",
    name="Squeeze Release (BB inside KC)",
    family="volatility",
    category="trend_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["5min", "15min", "30min", "1hour"],
    hold="intraday",
    stop_method="structure",
    params={"length": 20, "bb_k": 2.0, "kc_mult": 1.5, "min_squeeze": 6,
            "vol_mult": 1.5, "target_mult": 2.0},
    param_grid={"kc_mult": [1.0, 1.5, 2.0], "min_squeeze": [4, 6, 8],
                "vol_mult": [1.2, 1.5, 2.0]},
    regimes_on=None,
    max_hold_bars=24,
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


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = dict(META.params)
    p.update(cfg or {})
    n, bb_k, kc_mult = int(p["length"]), float(p["bb_k"]), float(p["kc_mult"])
    min_sq, vol_mult, tgt_mult = int(p["min_squeeze"]), float(p["vol_mult"]), float(p["target_mult"])
    market = ctx.get("market")
    # The volume gate is meaningless in thin extended-hours tape.
    if market != "crypto" and ctx.get("session") in ("premarket", "afterhours"):
        return None
    bars = (ctx.get("bars") or [])[-MAX_BARS:]
    if len(bars) < max(MIN_BARS, n + min_sq + 2):
        return None
    closes = [float(b["c"]) for b in bars]
    bb = bollinger_series(closes, n, bb_k)
    kmid = ema_series(closes, n)
    katr = atr_series(bars, n)

    def in_squeeze(i: int) -> bool:
        b, ka = bb[i], katr[i]
        return (b is not None and ka is not None
                and b["upper"] < kmid[i] + kc_mult * ka
                and b["lower"] > kmid[i] - kc_mult * ka)

    n_sq, i = 0, len(bars) - 2                  # consecutive squeeze bars ending at the PRIOR bar
    while i >= 0 and in_squeeze(i):
        n_sq += 1
        i -= 1
    if n_sq < min_sq:
        return None
    cur = bb[-1]
    if cur is None or cur["sd"] <= 0 or katr[-1] is None:
        return None
    close = closes[-1]
    short_ok = market in ("crypto", "etf")
    if close > cur["upper"]:
        d, band, side = 1, cur["upper"], "above the upper"
    elif close < cur["lower"] and short_ok:
        d, band, side = -1, cur["lower"], "below the lower"
    else:
        return None
    vr = _vol_ratio(bars, n)
    if vr < vol_mult:
        return None
    sq_idx = range(len(bars) - 1 - n_sq, len(bars) - 1)
    height = sum(bb[j]["upper"] - bb[j]["lower"] for j in sq_idx) / n_sq
    stop = cur["basis"]                          # squeeze midline
    risk = (close - stop) * d
    if risk <= 0 or height <= 0:
        return None
    t1 = close + d * height
    t2 = close + d * max(tgt_mult, 1.5) * height

    a14 = atr(bars, 14) or katr[-1]
    kc_width = 2.0 * kc_mult * katr[-1]
    pre = bb[-2]["upper"] - bb[-2]["lower"]
    ratio = pre / kc_width if kc_width > 0 else 1.0
    margin = (close - band) * d / a14 if a14 > 0 else 0.0
    comps = {"squeeze_len": _scale(n_sq, min_sq, min_sq + 10, 25),
             "compression": _scale(1.0 - ratio, 0.0, 0.5, 20),
             "volume": _scale(vr, vol_mult, vol_mult + 1.5, 25),
             "margin": _scale(margin, 0.0, 0.5, 15)}
    conf = 15.0 + sum(comps.values())
    reasons = [
        f"Bollinger({n},{bb_k:.0f}) sat inside Keltner({n},{kc_mult:.1f}) for {n_sq} "
        f"consecutive bars before this one",
        f"Pre-release Bollinger width {pre:.2f} was {ratio * 100:.0f}% of the Keltner width {kc_width:.2f}",
        f"Close {close:.2f} is {side} Bollinger band {band:.2f} by {margin:.2f} ATR",
        f"Volume {vr:.2f}x the {n}-bar average (gate {vol_mult:.1f}x)",
        f"Stop at the squeeze midline {stop:.2f}; mean squeeze height {height:.2f} sets "
        f"targets {t1:.2f} and {t2:.2f}",
    ]
    return Signal(
        direction="long" if d == 1 else "short",
        entry=round(close, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4),
        confidence=round(min(100.0, conf), 1),
        reasons=reasons,
        invalidation=(f"A close back at or through the squeeze midline {stop:.2f} means the "
                      f"expansion failed and range holders regained control."),
        expected_bars=12,
        trailing=None,
        features={"squeeze_bars": n_sq, "squeeze_height": round(height, 4),
                  "bb_upper": round(cur["upper"], 4), "bb_lower": round(cur["lower"], 4),
                  "bb_basis": round(cur["basis"], 4), "kc_width": round(kc_width, 4),
                  "compression_ratio": round(ratio, 3), "vol_ratio": round(vr, 3),
                  "margin_atr": round(margin, 3), "atr14": round(a14, 4),
                  "conf_components": {k: round(v, 1) for k, v in comps.items()}},
    )
