"""Three consecutive closes above the upper Bollinger(20,2) band with the bands
still widening mean the 20-bar distribution is being re-priced faster than its
own standard deviation can adapt, which only happens when a persistent buyer
(institutional accumulation, forced short covering, index inclusion) is
absorbing every offer. The crowd fades this because it "looks overbought", and
their stops sit just above the band, so the walk feeds on the fade; the middle
band is where that persistent buyer has demonstrably stopped absorbing, which
makes it the structural stop. Falsified if buying the third close above the
upper band with expanding width loses to fading it, or if walk entries fail to
travel a further band-width before touching the basis.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import bollinger_series

META = StrategyMeta(
    id="s20_bollinger_walk_continuation",
    name="Bollinger Walk Continuation",
    family="trend",
    category="breakout_vol",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="stdev",
    params={"bb_len": 20, "bb_k": 2.0, "walk_bars": 3, "min_exp_pct": 5.0, "tgt_sd": 3.0,
            "vol_lookback": 20},
    param_grid={"walk_bars": [2, 3, 4], "min_exp_pct": [0.0, 5.0, 10.0], "tgt_sd": [2.0, 3.0, 4.0]},
    regimes_on=None,
    max_hold_bars=30,
    version="1.0.0",
)

_REGIME_ADJ = {
    "long": {"trend_up": 10, "high_vol": 3, "low_vol": -4, "range": -8, "trend_down": -6, "bear": -10},
    "short": {"trend_down": 10, "bear": 10, "high_vol": 3, "low_vol": -4, "range": -8, "trend_up": -10},
}


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    bars = ctx.get("bars") or []
    n, k, walk = int(p["bb_len"]), float(p["bb_k"]), int(p["walk_bars"])
    vol_lb = int(p["vol_lookback"])
    if len(bars) < n + walk + vol_lb + 1:
        return None
    closes = [float(b["c"]) for b in bars]
    bb = bollinger_series(closes, n, k)
    recent = [bb[-i] for i in range(1, walk + 2)]       # recent[0] = now ... recent[walk] = bar before the walk
    if any(x is None or not x["sd"] or x["sd"] <= 0 or not x["width"] for x in recent):
        return None
    cur, start = recent[0], recent[walk]
    c_start = closes[-walk - 1]
    market = str(ctx.get("market") or "")
    walk_c = closes[-walk:]                             # oldest ... newest
    walk_bb = list(reversed(recent[:walk]))
    if all(c > b["upper"] for c, b in zip(walk_c, walk_bb)) and c_start <= start["upper"]:
        direction, sign = "long", 1.0
        excursions = [(c - b["upper"]) / b["sd"] for c, b in zip(walk_c, walk_bb)]
    elif (market in ("crypto", "etf") and all(c < b["lower"] for c, b in zip(walk_c, walk_bb))
          and c_start >= start["lower"]):
        direction, sign = "short", -1.0
        excursions = [(b["lower"] - c) / b["sd"] for c, b in zip(walk_c, walk_bb)]
    else:
        return None                                     # no walk, or the walk began earlier (already entered)

    exp_pct = (cur["width"] / start["width"] - 1.0) * 100
    if cur["width"] <= recent[1]["width"] or exp_pct < float(p["min_exp_pct"]):
        return None                                     # bands not expanding: squeeze, not a walk
    sd = cur["sd"]
    slope_sd = sign * (cur["basis"] - start["basis"]) / sd
    if slope_sd <= 0:
        return None                                     # basis must lean with the trade

    entry, stop = closes[-1], cur["basis"]
    tgt_sd = float(p["tgt_sd"])
    target1 = entry + sign * tgt_sd * sd
    target2 = entry + sign * 2 * tgt_sd * sd
    if sign * (entry - stop) <= 0:
        return None

    vols = [float(b.get("v") or 0) for b in bars]
    med_vol = _median(vols[-walk - vol_lb:-walk])
    walk_vol = sum(vols[-walk:]) / walk
    vol_ratio = walk_vol / med_vol if med_vol > 0 else None
    strength = sum(excursions) / len(excursions)
    regime = str(ctx.get("regime") or "")
    comp = {
        "base": 30.0,                                   # every factor saturated sums to exactly 100
        "walk_strength": 20.0 * _clamp(strength / 0.5, 0.0, 1.0),
        "width_expansion": 20.0 * _clamp(exp_pct / 25.0, 0.0, 1.0),
        "basis_slope": 10.0 * _clamp(slope_sd, 0.0, 1.0),
        "volume": 10.0 * _clamp(vol_ratio - 1.0, 0.0, 1.0) if vol_ratio else 4.0,
        "regime": float(_REGIME_ADJ[direction].get(regime, 0)),
    }
    confidence = round(_clamp(sum(comp.values()), 0.0, 100.0), 1)
    expected = int(_clamp(round(tgt_sd * 3), 5, 30))

    side = "above the upper" if direction == "long" else "below the lower"
    band = cur["upper"] if direction == "long" else cur["lower"]
    reasons = [
        f"{walk} consecutive closes {side} Bollinger({n},{k:g}) band; latest close {entry:.2f} vs band "
        f"{band:.2f}, averaging {strength:.2f} sigma beyond it",
        f"Band width {cur['width'] * 100:.2f}% of price has expanded {exp_pct:.1f}% since the walk began",
        f"Middle band {stop:.2f} moved {slope_sd:.2f} sigma with the trade over the walk and is the stop",
        f"Targets {target1:.2f} / {target2:.2f} are entry plus {tgt_sd:g}x and {2 * tgt_sd:g}x the "
        f"{n}-bar sigma {sd:.4g}",
    ]
    if vol_ratio is not None:
        reasons.append(f"Average walk-bar volume {vol_ratio:.1f}x the {vol_lb}-bar median before it")
    reasons.append(f"Regime {regime or 'unknown'} contributes {comp['regime']:+.0f} confidence")
    return Signal(
        direction=direction, entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back through the middle band ({stop:.2f}) means the persistent "
                      f"{'buyer' if direction == 'long' else 'seller'} has stopped absorbing and the walk is over."),
        expected_bars=expected, trailing=None,
        features={"basis": cur["basis"], "upper": cur["upper"], "lower": cur["lower"], "sd": sd,
                  "width_now": cur["width"], "width_start": start["width"], "width_exp_pct": exp_pct,
                  "walk_strength_sd": strength, "basis_slope_sd": slope_sd, "vol_ratio": vol_ratio,
                  "regime": regime, "confidence_components": comp},
    )
