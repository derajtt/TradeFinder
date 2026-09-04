"""Support/resistance polarity flip retest.

Hypothesis: a resistance zone is a price where earlier rallies were sold, by
holders exiting at break-even and by shorts leaning on a visible level. Once
price closes above the zone and later returns to it from above, two groups are
forced to trade: shorts from the level are under water and cover into the
retest, and sellers who exited there re-buy rather than watch it leave without
them. That forced demand is what turns the old ceiling into a floor, so a
retest that holds (touches the zone, closes back above it) should carry
positive expectancy with a stop just under the zone. Falsified if retests of
broken resistance lose money against random same-hour entries, or if the zone
is lost as often as it holds (no polarity effect, only noise around a pivot).
"""
from typing import Any, Dict, List, Optional, Tuple

from ..base import Signal, StrategyMeta
from ...strategy.indicators import atr, resistance_zones

META = StrategyMeta(
    id="s26_sr_flip_retest",
    name="S/R Flip Retest",
    family="structure",
    category="support_resistance",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "crypto"],
    timeframes=["5min", "15min", "30min", "1hour"],
    hold="intraday",
    stop_method="structure",
    params={"zone_tol_pct": 0.6, "min_touches": 2, "stop_buffer_atr": 0.5,
            "daily_lookback": 120},
    param_grid={"zone_tol_pct": [0.4, 0.6, 0.8], "min_touches": [2, 3, 4],
                "stop_buffer_atr": [0.25, 0.5, 0.75],
                "daily_lookback": [60, 120, 250]},
    regimes_on=None,
    max_hold_bars=36,
    version="1.0.0",
)

MAX_BREAK_LOOKBACK = 390      # working bars searched backwards for the break


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _day_key(b: Dict[str, Any]) -> int:
    return int(b["time"]) - int(b.get("minute_of_day") or 0) * 60


def _today(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    k, i = _day_key(bars[-1]), len(bars) - 1
    while i > 0 and _day_key(bars[i - 1]) == k:
        i -= 1
    return bars[i:]


def _find_break(bars: List[Dict[str, Any]], hi: float, lo: float
                ) -> Optional[Tuple[int, int]]:
    """Most recent bar that closed above the zone after a close at or below it.
    Returns (break_index, earlier_retests) or None when any bar since the break
    closed back under the zone (failed flip) or no break is in range."""
    n, retests = len(bars), 0
    for j in range(n - 2, max(0, n - MAX_BREAK_LOOKBACK), -1):
        c = float(bars[j]["c"])
        if c < lo:
            return None
        if c > hi and float(bars[j - 1]["c"]) <= hi:
            return j, retests
        if float(bars[j]["l"]) <= hi:
            retests += 1
    return None


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx["bars"]
    if len(bars) < 30 or ctx.get("session") == "afterhours":
        return None
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    tol = float(p["zone_tol_pct"]) / 100.0
    a = atr(bars, 14)
    if not a or a <= 0:
        return None
    cur = bars[-1]
    close, low, high = float(cur["c"]), float(cur["l"]), float(cur["h"])
    daily = (ctx.get("daily") or [])[-int(p["daily_lookback"]):]
    zones = (resistance_zones(daily, float(p["zone_tol_pct"]), int(p["min_touches"]))
             if len(daily) >= 10 else [])
    earlier_today = _today(bars)[:-1]
    if len(earlier_today) >= 8:
        zones = zones + resistance_zones(earlier_today, float(p["zone_tol_pct"]),
                                         int(p["min_touches"]))
    if not zones:
        return None
    vols = [float(b.get("v") or 0) for b in bars]
    best: Optional[Dict[str, Any]] = None
    for z in zones:
        lvl = float(z["level"])
        hi, lo = lvl * (1 + tol), lvl * (1 - tol)
        if not (low <= hi and close > hi):
            continue                                  # not a touch-and-hold
        found = _find_break(bars, hi, lo)
        if not found:
            continue
        j, earlier = found
        stop = lo - float(p["stop_buffer_atr"]) * a
        if low <= stop:
            continue                                  # retest already through the stop
        base_v = sum(vols[max(0, j - 20):j]) / max(1, min(20, j))
        brk_vol = vols[j] / base_v if base_v > 0 else 1.0
        rng = high - low
        close_pos = (close - low) / rng if rng > 0 else 1.0
        depth_atr = max(0.0, (hi - low) / a)
        comp = {
            "zone_touches": 5.0 * min(int(z["touches"]), 5),
            "break_volume": 25.0 * _clamp((brk_vol - 1.0) / 2.0),
            "close_position": 20.0 * _clamp(close_pos),
            "shallow_retest": 15.0 * _clamp(1.0 - depth_atr),
            "first_retest": 10.0 if earlier == 0 else (5.0 if earlier == 1 else 0.0),
            "regime": 5.0 if ctx.get("regime") == "trend_up" else 0.0,
        }
        cand = {"conf": round(sum(comp.values()), 1), "lvl": lvl, "hi": hi, "lo": lo,
                "touches": int(z["touches"]), "j": j, "earlier": earlier, "stop": stop,
                "brk_vol": brk_vol, "close_pos": close_pos, "depth_atr": depth_atr,
                "comp": comp}
        if best is None or cand["conf"] > best["conf"]:
            best = cand
    if best is None:
        return None
    b = best
    entry, stop = close, b["stop"]
    risk = entry - stop
    t1 = entry + 1.5 * risk
    above = sorted(float(q["level"]) * (1 - tol) for q in zones
                   if float(q["level"]) * (1 - tol) > t1)
    t2 = above[0] if above else entry + 3.0 * risk
    bars_since = len(bars) - 1 - b["j"]
    reasons = [
        f"Resistance zone {b['lvl']:.2f} ({b['touches']} pivot touches) was broken "
        f"{bars_since} bars ago on {b['brk_vol']:.1f}x average volume",
        f"Retest low {low:.2f} touched the zone and the bar closed at {close:.2f}, "
        f"above the zone's upper edge {b['hi']:.2f}",
        f"Close sits at {b['close_pos'] * 100:.0f}% of the bar's range; retest reached "
        f"{b['depth_atr']:.2f} ATR into the zone",
        f"{b['earlier']} earlier retests since the break, none closed below {b['lo']:.2f}",
        f"Stop {stop:.2f} is {p['stop_buffer_atr']} ATR ({a:.2f}) under the zone's "
        f"lower edge {b['lo']:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=b["conf"], reasons=reasons,
        invalidation=(f"A close below {b['lo']:.2f}, the zone's lower edge, means the "
                      "flip failed and the level is resistance again."),
        expected_bars=12, trailing={"type": "swing"},
        features={"zone_level": b["lvl"], "zone_hi": b["hi"], "zone_lo": b["lo"],
                  "touches": b["touches"], "bars_since_break": bars_since,
                  "break_volume_ratio": b["brk_vol"], "close_position": b["close_pos"],
                  "retest_depth_atr": b["depth_atr"], "earlier_retests": b["earlier"],
                  "atr": a, "n_zones": len(zones), "conf_components": b["comp"]},
    )
