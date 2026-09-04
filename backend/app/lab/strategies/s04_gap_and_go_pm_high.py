"""Gap-and-go: a large gap up that reclaims its premarket high in the first hour.

Overnight information (earnings, contracts, upgrades) is under-reacted to when
a stock gaps because most capital cannot transact before the open; the
premarket high is the price the thin early crowd was willing to pay. When
regular-session volume arrives at two or more times the same-minute norm and
price closes above that high, latecomers who waited for the open and shorts
who faded the gap are forced to buy the same prints. Falsified if
RVOL-confirmed premarket-high breaks on 4%+ gaps fade back to VWAP as often as
they extend. Needs extended-hours bars in ctx["bars"]; silent without them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, session_vwap

PM_OPEN, RTH_OPEN, FIRST_HOUR_END = 240, 570, 630          # minutes after 00:00 ET

META = StrategyMeta(
    id="s04_gap_and_go_pm_high",
    name="Gap-and-Go Premarket High Break",
    # Same trigger geometry as s17_premarket_high_break (first regular-session
    # close above the premarket high, VWAP side, RVOL confirmed); the gap filter
    # makes it a nested variant, not an independent idea.  Sharing s17's family
    # keeps the ensemble from counting one signal as two agreeing families.
    family="session",
    category="gap_continuation",
    hypothesis=" ".join(__doc__.split()),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="structure",
    params={"gap_min_pct": 4.0, "rvol_min": 2.0, "rr1": 1.5},
    param_grid={"gap_min_pct": [3.0, 4.0, 6.0], "rvol_min": [1.5, 2.0, 3.0],
                "rr1": [1.0, 1.5, 2.0]},
    regimes_on=None,
    max_hold_bars=48,
    version="1.0.0",
)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _day_key(b: dict) -> int:               # ET calendar day: epoch of the bar's ET midnight, in days
    return (int(b.get("time", 0)) - int(b.get("minute_of_day", 0)) * 60) // 86400


def _today(bars: List[dict]) -> List[dict]:
    dk, i = _day_key(bars[-1]), len(bars) - 1
    while i > 0 and _day_key(bars[i - 1]) == dk:
        i -= 1
    return bars[i:]


def _rvol(bars: List[dict], cur_min: int, tf_min: int, daily: List[dict],
          n_days: int = 10) -> Tuple[Optional[float], str]:
    """Cumulative volume from the premarket open through cur_min today vs the same window's
    mean over up to n_days prior sessions; with <3 priors, a fraction of 20-day daily volume."""
    dk, today, prior = _day_key(bars[-1]), 0.0, {}   # type: ignore[var-annotated]
    for b in reversed(bars):
        k = _day_key(b)
        if k != dk and k not in prior:
            if len(prior) >= n_days:
                break
            prior[k] = 0.0
        m = b.get("minute_of_day", 0)
        if m < PM_OPEN or m > cur_min:
            continue
        v = float(b.get("v") or 0)
        if k == dk:
            today += v
        else:
            prior[k] += v
    base = [x for x in prior.values() if x > 0]
    if len(base) >= 3:
        return today / (sum(base) / len(base)), "same-minute mean of %d prior sessions" % len(base)
    vols = [float(d.get("v") or 0) for d in daily[-20:]]
    if not vols or sum(vols) <= 0:
        return None, "unavailable"
    frac = 0.05 + 0.95 * _clamp((cur_min + tf_min - RTH_OPEN) / 390.0)
    return today / (sum(vols) / len(vols) * frac), "time-of-day fraction of 20-day daily volume"


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    daily: List[dict] = ctx.get("daily") or []
    if (ctx.get("market") not in ("stocks", "etf") or len(bars) < 3 or not daily
            or not (RTH_OPEN <= bars[-1].get("minute_of_day", 0) < FIRST_HOUR_END)):
        return None
    cur, prev = bars[-1], bars[-2]
    m = int(cur["minute_of_day"])
    gap_min, rvol_min, rr1 = (float(cfg.get("gap_min_pct", META.params["gap_min_pct"])),
                              float(cfg.get("rvol_min", META.params["rvol_min"])),
                              float(cfg.get("rr1", META.params["rr1"])))
    today = _today(bars)
    pm = [b for b in today if PM_OPEN <= b["minute_of_day"] < RTH_OPEN]
    rth = [b for b in today if b["minute_of_day"] >= RTH_OPEN]
    if len(pm) < 3 or not rth or rth[0]["minute_of_day"] > RTH_OPEN + 5:
        return None
    prev_close = float(daily[-1]["c"])
    gap = (float(rth[0]["o"]) / prev_close - 1.0) * 100 if prev_close > 0 else -1.0
    if gap < gap_min:
        return None
    pm_high, pm_low = max(b["h"] for b in pm), min(b["l"] for b in pm)
    pm_range = pm_high - pm_low
    if pm_range <= 0 or not (cur["c"] > pm_high >= prev["c"]) or any(b["c"] > pm_high for b in rth[:-1]):
        return None                                             # first regular-session close above the PM high
    vwap = session_vwap(today)
    if not vwap or cur["c"] <= vwap:
        return None
    a = atr(bars, 14)
    ext, ext_cap = float(cur["c"]) - pm_high, max(0.5 * pm_range, a or 0.0)
    if ext > ext_cap:                                           # do not chase past the level
        return None
    tf_min = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60}.get(ctx.get("timeframe", ""), 5)
    rvol, basis = _rvol(bars, m, tf_min, daily)
    if rvol is None or rvol < rvol_min:
        return None
    entry = float(cur["c"])
    buffer = 0.25 * a if a else 0.002 * entry
    stop_ref, stop_basis = (vwap, "session VWAP") if vwap > pm_low else (pm_low, "premarket low")
    stop, risk = stop_ref - buffer, entry - stop_ref + buffer
    t1, t2 = entry + rr1 * risk, entry + 2.0 * rr1 * risk
    if not (stop < entry < t1 < t2):
        return None
    third = max(1, len(pm) // 3)                                 # premarket structure: last third vs first third
    h_early, h_late = max(b["h"] for b in pm[:third]), max(b["h"] for b in pm[-third:])
    l_early, l_late = min(b["l"] for b in pm[:third]), min(b["l"] for b in pm[-third:])
    hh_hl = 1.0 if (h_late >= h_early and l_late >= l_early) else 0.0
    vwap_pos, vwap_dist = _clamp((vwap - pm_low) / pm_range), (entry - vwap) / vwap * 100
    comp = {
        "base": 15.0,
        "gap_size": 20.0 * _clamp((gap - gap_min) / gap_min),
        "rvol": 25.0 * _clamp((rvol - rvol_min) / rvol_min),
        "vwap_in_pm_range": 15.0 * vwap_pos,
        "low_extension": 15.0 * (1.0 - ext / ext_cap),
        "pm_structure": 10.0 * hh_hl,
    }
    conf = round(min(100.0, max(0.0, sum(comp.values()))), 1)
    reasons = [
        "Opened at %.2f, a %.1f%% gap over the prior close %.2f" % (rth[0]["o"], gap, prev_close),
        "Closed at %.2f, first regular-session close above the premarket high %.2f" % (entry, pm_high),
        "Relative volume %.1fx (%s)" % (rvol, basis),
        "Price %.2f%% above session VWAP %.2f, which sits %.0f%% up the premarket range" % (vwap_dist, vwap, vwap_pos * 100),
        "Late premarket high %.2f vs early %.2f and low %.2f vs %.2f: %s"
        % (h_late, h_early, l_late, l_early, "higher highs and higher lows" if hh_hl else "no higher-high/higher-low structure"),
        "Break has extended %.2f past the level against a chase cap of %.2f" % (ext, ext_cap),
    ]
    return Signal(
        direction="long", entry=round(entry, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4), confidence=conf, reasons=reasons,
        invalidation="A close back below the %s at %.2f means the open brought sellers, not the buyers the gap implied."
        % (stop_basis, stop_ref),
        expected_bars=max(4, int(60 / tf_min)), trailing=None,
        features={"gap_pct": round(gap, 2), "prev_close": prev_close, "rth_open": rth[0]["o"], "pm_bars": len(pm),
                  "pm_high": pm_high, "pm_low": pm_low, "pm_range": round(pm_range, 4), "pm_hh_hl": hh_hl,
                  "vwap": round(vwap, 4), "vwap_dist_pct": round(vwap_dist, 3), "vwap_pos_in_pm_range": round(vwap_pos, 3),
                  "ext_past_pm_high": round(ext, 4), "ext_cap": round(ext_cap, 4), "rvol": round(rvol, 2), "rvol_basis": basis,
                  "atr14": round(a, 6) if a else None, "stop_basis": stop_basis, "minute_of_day": m, "confidence_components": comp},
    )
