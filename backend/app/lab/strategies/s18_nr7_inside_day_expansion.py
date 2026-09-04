"""Daily range contracts and expands in cycles because a narrow bar means both
sides have withdrawn: market makers pull quotes in and directional traders
wait, so the first decisive move out of the compressed range meets little
resting liquidity and forces the sidelined participants to chase. A break of
the narrow bar's high on the next session is the earliest confirmed sign that
the expansion phase has begun, and the narrow bar's low is where that thesis is
proven wrong. Falsified if next-session breaks of NR7 or inside-day highs do
not travel one daily ATR before returning below the narrow bar's low more often
than breaks of ordinary-range bars do.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

META = StrategyMeta(
    id="s18_nr7_inside_day_expansion",
    name="NR7 / Inside-Day Expansion",
    family="volatility",
    category="breakout_vol",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "crypto"],
    timeframes=["15min", "30min", "1hour", "1day"],
    hold="swing",
    stop_method="atr",
    params={"mode": "either", "stop_atr": 0.5, "tgt_atr": 1.0, "max_ext_atr": 0.5,
            "atr_len": 14, "vol_lookback": 20},
    param_grid={"mode": ["nr7", "either", "both"], "stop_atr": [0.25, 0.5, 1.0],
                "tgt_atr": [0.75, 1.0, 1.5], "max_ext_atr": [0.3, 0.5, 0.7]},
    regimes_on=None,
    max_hold_bars=30,
    version="1.0.0",
)

_BARS_PER_SESSION = {"5min": 78, "15min": 26, "30min": 13, "1hour": 7, "4hour": 2, "1day": 1}
_REGIME_ADJ = {"trend_up": 8, "range": 3, "low_vol": 2, "high_vol": 0,
               "trend_down": -4, "bear": -8}


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _today(bars: List[dict]) -> List[dict]:
    """Bars of the current session: walk back while minute_of_day keeps increasing."""
    i = len(bars) - 1
    while i > 0 and int(bars[i - 1].get("minute_of_day", 0)) < int(bars[i].get("minute_of_day", 0)):
        i -= 1
    return bars[i:]


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    bars = ctx.get("bars") or []
    tf = str(ctx.get("timeframe") or "")
    # Prior sessions: on the daily timeframe they are simply the bars before this one.
    hist = bars[:-1] if tf == "1day" else (ctx.get("daily") or [])
    n_atr = int(p["atr_len"])
    if len(bars) < 2 or len(hist) < max(n_atr + 1, 8):
        return None
    nb, pb = hist[-1], hist[-2]                        # narrow bar (prior session) and the one before it
    ranges = [float(h["h"]) - float(h["l"]) for h in hist[-7:]]
    nb_range = ranges[-1]
    if nb_range <= 0 or min(ranges[:-1]) <= 0:
        return None
    is_nr7 = nb_range <= min(ranges[:-1])
    is_inside = float(nb["h"]) <= float(pb["h"]) and float(nb["l"]) >= float(pb["l"])
    setups = {"nr7": is_nr7, "inside": is_inside, "either": is_nr7 or is_inside,
              "both": is_nr7 and is_inside}
    if not setups.get(str(p["mode"]), is_nr7 or is_inside):
        return None
    atr_d = atr(hist, n_atr)
    if not atr_d or atr_d <= 0:
        return None

    cur = bars[-1]
    close = float(cur["c"])
    level, nb_low = float(nb["h"]), float(nb["l"])
    if close <= level:
        return None
    today = [cur] if tf == "1day" else _today(bars)
    if any(float(b["c"]) > level for b in today[:-1]):
        return None                                    # only the first close through the level this session
    ext_atr = (close - level) / atr_d
    if ext_atr > float(p["max_ext_atr"]):
        return None                                    # break has already run: chasing

    stop = nb_low - float(p["stop_atr"]) * atr_d
    target1 = level + float(p["tgt_atr"]) * atr_d
    target2 = level + 2 * float(p["tgt_atr"]) * atr_d
    if target1 <= close or stop >= close:
        return None

    avg6 = sum(ranges[:-1]) / len(ranges[:-1])
    compression = nb_range / avg6                      # <1 means tighter than the recent norm
    ref = bars[-int(p["vol_lookback"]) - 1:-1]              # volume context is optional: needs half a window
    med_vol = _median([float(b.get("v") or 0) for b in ref]) if len(ref) >= int(p["vol_lookback"]) // 2 else 0.0
    vol_ratio = float(cur.get("v") or 0) / med_vol if med_vol > 0 else None
    regime = str(ctx.get("regime") or "")
    comp = {
        "base": 35.0,
        "compression": 25.0 * _clamp(1.0 - compression, 0.0, 1.0),
        "double_setup": 10.0 if (is_nr7 and is_inside) else 0.0,
        "volume": 15.0 * _clamp((vol_ratio - 1.0) / 1.5, 0.0, 1.0) if vol_ratio else 5.0,
        "extension": -10.0 * _clamp(ext_atr / float(p["max_ext_atr"]), 0.0, 1.0),
        "regime": float(_REGIME_ADJ.get(regime, 0)),
    }
    confidence = round(_clamp(sum(comp.values()), 0.0, 100.0), 1)
    per = _BARS_PER_SESSION.get(tf, 13)
    expected = 3 if tf == "1day" else int(_clamp(round(per * 0.75), 3, 40))

    label = " and ".join(s for s, ok in (("NR7", is_nr7), ("inside day", is_inside)) if ok)
    reasons = [
        f"Prior session was {label}: range {nb_range:.2f} is {compression * 100:.0f}% of the "
        f"{len(ranges) - 1}-day average range {avg6:.2f}",
        f"Close {close:.2f} is the first close of this session above the narrow bar high {level:.2f}, "
        f"{ext_atr:.2f} daily ATR beyond it",
        f"Stop {stop:.2f} is {p['stop_atr']} daily ATR ({atr_d:.2f}) under the narrow bar low {nb_low:.2f}",
        f"Targets {target1:.2f} / {target2:.2f} are the narrow high plus {p['tgt_atr']}x and "
        f"{2 * float(p['tgt_atr']):g}x daily ATR",
    ]
    if vol_ratio is not None:
        reasons.append(f"Break bar volume {vol_ratio:.1f}x the {int(p['vol_lookback'])}-bar median")
    reasons.append(f"Regime {regime or 'unknown'} contributes {comp['regime']:+.0f} confidence")
    return Signal(
        direction="long", entry=close, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back below the narrow bar low {nb_low:.2f} means the range did not "
                      f"expand upward and the compression is resolving the other way."),
        expected_bars=expected, trailing=None,
        features={"narrow_high": level, "narrow_low": nb_low, "narrow_range": nb_range,
                  "avg_range_6": avg6, "compression": compression, "is_nr7": is_nr7,
                  "is_inside": is_inside, "atr_daily": atr_d, "ext_atr": ext_atr,
                  "vol_ratio": vol_ratio, "regime": regime, "confidence_components": comp},
    )
