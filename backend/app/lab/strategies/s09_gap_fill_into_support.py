"""Gap-fill long when a 2-6% gap down opens into prior daily support.

Hypothesis: a 2-6% overnight gap down is priced in a thin auction where
overnight sellers (news readers, risk desks trimming, stops queued for the
open) meet almost no natural buyers, so the opening print overshoots. When
that print lands in a zone where the stock has already turned at least twice
before, the holders who bought there last time and the dealers who know the
level supply the bid, and a first 15-minute bar that closes above its open
shows the opening sell flow has been absorbed. The gap then tends to fill
toward the prior close because the sellers who wanted out are done and the
marginal buyer's view has not changed. Falsified if gap-downs into
multi-touch zones that reclaim the open fill no more often than same-sized
gap-downs into empty space — then the level adds nothing over gap size.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, pivots, resistance_zones

RTH_START = 570                             # 09:30 ET; bars are labelled by start time

META = StrategyMeta(
    id="s09_gap_fill_into_support",
    name="Gap Fill into Support",
    family="mean_reversion",
    category="gap_reversion",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf"],
    timeframes=["15min"],
    hold="intraday",
    stop_method="structure",
    params={"zone_tol_pct": 0.75, "gap_min_pct": 2.0, "gap_max_pct": 6.0,
            "min_rr": 1.0, "stop_buffer_atr": 0.15},
    param_grid={"zone_tol_pct": [0.5, 0.75, 1.0],
                "gap_min_pct": [1.5, 2.0, 2.5],
                "min_rr": [0.8, 1.0, 1.25]},
    regimes_on=["trend_up", "range", "low_vol"],
    max_hold_bars=26,
)


def _p(cfg: Dict[str, Any], key: str) -> Any:
    return cfg.get(key, META.params[key])


def _cluster(prices: List[float], tol_pct: float, min_touches: int) -> List[dict]:
    """Same clustering as resistance_zones, applied to any list of pivot prices."""
    zones: List[dict] = []
    for price in prices:
        for z in zones:
            if abs(price - z["level"]) / z["level"] * 100 <= tol_pct:
                z["touches"] += 1
                z["level"] = (z["level"] * (z["touches"] - 1) + price) / z["touches"]
                break
        else:
            zones.append({"level": price, "touches": 1})
    return [z for z in zones if z["touches"] >= min_touches]


def _support_zones(daily: List[dict], tol_pct: float) -> List[dict]:
    """Prior daily levels touched >= 2 times: old resistance (polarity flip)
    from resistance_zones plus clustered pivot lows."""
    zones = [dict(z, kind="prior_high") for z in resistance_zones(daily, tol_pct, 2)]
    _, lows = pivots(daily)
    zones += [dict(z, kind="prior_low") for z in _cluster([p for _, p in lows], tol_pct, 2)]
    return zones


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx.get("bars") or []
    daily = ctx.get("daily") or []
    if ctx.get("regime") not in META.regimes_on or ctx.get("session") != "open":
        return None
    if len(bars) < 2 or len(daily) < 60:
        return None
    cur, prev_bar = bars[-1], bars[-2]
    if cur["minute_of_day"] != RTH_START or prev_bar["minute_of_day"] == RTH_START:
        return None                          # only the completed 09:30 bar qualifies
    prev_close, prev_high = float(daily[-1]["c"]), float(daily[-1]["h"])
    gap_pct = (float(cur["o"]) - prev_close) / prev_close * 100.0
    gap_min, gap_max = float(_p(cfg, "gap_min_pct")), float(_p(cfg, "gap_max_pct"))
    if not (-gap_max <= gap_pct <= -gap_min) or cur["c"] <= cur["o"]:
        return None
    tol = float(_p(cfg, "zone_tol_pct"))
    zones = _support_zones(daily[-250:], tol)
    lo_edge, hi_edge = float(cur["l"]) * (1 - tol / 100), float(cur["o"]) * (1 + tol / 100)
    hits = [z for z in zones if lo_edge <= z["level"] <= hi_edge]
    if not hits:
        return None                          # the gap did not open into a known level
    zone = max(hits, key=lambda z: z["touches"])
    a_d = atr(daily, 14)
    if not a_d or a_d <= 0:
        return None
    buffer_atr = float(_p(cfg, "stop_buffer_atr"))
    zone_floor = zone["level"] * (1 - tol / 100)
    stop = min(zone_floor, float(cur["l"])) - buffer_atr * a_d
    entry = float(cur["c"])
    risk = entry - stop
    t1 = prev_close
    if risk <= 0 or t1 <= entry:
        return None                          # already filled, nothing left to capture
    t2 = max(prev_high, prev_close + 0.5 * a_d)
    if t2 <= t1:
        t2 = t1 + 0.5 * a_d
    rr1 = (t1 - entry) / risk
    if rr1 < float(_p(cfg, "min_rr")):
        return None
    rng = float(cur["h"]) - float(cur["l"])
    close_pos = (entry - float(cur["l"])) / rng if rng > 0 else 0.5
    open_vols = [float(b.get("v") or 0) for b in bars[:-1]
                 if b["minute_of_day"] == RTH_START][-10:]
    avg_open_v = sum(open_vols) / len(open_vols) if open_vols else 0.0
    rvol = float(cur.get("v") or 0) / avg_open_v if avg_open_v > 0 else None
    zone_off_pct = abs(float(cur["o"]) - zone["level"]) / zone["level"] * 100.0
    comp = {
        "gap_sweet_spot": max(0.0, 20.0 - abs(abs(gap_pct) - 3.5) * 6.0),
        "zone_touches": min(20.0, zone["touches"] * 7.0),
        "first_bar_strength": close_pos * 20.0,
        "zone_proximity": max(0.0, 10.0 * (1.0 - zone_off_pct / tol)),
        "opening_rvol": 5.0 if rvol is None else max(0.0, min(15.0, (rvol - 1.0) * 10.0)),
    }
    confidence = max(0.0, min(100.0, 15.0 + sum(comp.values())))   # components sum to 85
    rvol_txt = f"{rvol:.2f}x" if rvol is not None else "n/a"
    reasons = [
        f"Opened {cur['o']:.2f}, a {gap_pct:.2f}% gap down from the prior close {prev_close:.2f}",
        f"Open landed {zone_off_pct:.2f}% from a {zone['kind']} zone at {zone['level']:.2f} "
        f"with {zone['touches']} prior touches",
        f"First 15-min bar closed {entry:.2f}, above its open and {close_pos:.0%} up its range; "
        f"opening volume {rvol_txt} the 10-day opening-bar average",
        f"Gap fill to {t1:.2f} is {rr1:.2f}R against stop {stop:.2f} below the zone "
        f"(daily ATR {a_d:.2f})",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=round(confidence, 1), reasons=reasons,
        invalidation=(f"A 15-min close below {stop:.2f} means the zone has failed and the "
                      f"gap is informed rather than an overnight overreaction."),
        expected_bars=max(3, min(26, int(round(rr1 * 6)))), trailing=None,
        features={"gap_pct": gap_pct, "prev_close": prev_close, "prev_high": prev_high,
                  "zone_level": zone["level"], "zone_touches": zone["touches"],
                  "zone_kind": zone["kind"], "zone_off_pct": zone_off_pct,
                  "close_pos": close_pos, "opening_rvol": rvol, "daily_atr14": a_d,
                  "rr1": rr1, "confidence_components": comp},
    )
