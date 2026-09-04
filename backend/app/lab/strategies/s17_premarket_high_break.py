"""The premarket high is where overnight information (news, earnings, guidance)
was last rejected on thin liquidity; when the regular session opens and the
deep pool of buyers arrives, a break of that level on relative volume >= 1.5x
says the broad market agrees with the overnight repricing while premarket
shorts and early sellers into the high are forced to cover. The edge lives in
the transfer from thin to thick liquidity, so it should exist only in the first
minutes after 9:30 and only when volume confirms wide participation. This is
the modest-repricing case: the regular-session open must sit within 3% of the
prior close, so the level break itself, not gap continuation (s04), carries the
information. Falsified if opening-window breaks of the PM high with RVOL >= 1.5
do not reach one PM range above the level more often than they close back
under session VWAP.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, session_vwap

META = StrategyMeta(
    id="s17_premarket_high_break",
    name="Premarket High Break",
    family="session",
    category="breakout_vol",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="vwap",
    params={"window_min": 30, "min_rvol": 1.5, "min_pm_bars": 3, "tgt_mult": 1.0,
            "rvol_days": 20, "stop_buffer_atr": 0.25, "max_ext_pct": 1.0, "max_gap_pct": 3.0},
    param_grid={"window_min": [15, 30, 45], "min_rvol": [1.2, 1.5, 2.0],
                "min_pm_bars": [2, 3, 6], "tgt_mult": [0.75, 1.0, 1.5]},
    regimes_on=None,
    max_hold_bars=36,
    version="1.0.0",
)

_OPEN = 570                                            # 9:30 ET in minutes of day
_BARS_PER_DAY = {"5min": 78, "15min": 26, "30min": 13, "1hour": 7}
_REGIME_ADJ = {"trend_up": 6, "high_vol": 2, "range": 0, "low_vol": 0,
               "trend_down": -4, "bear": -10}


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
    daily = ctx.get("daily") or []
    n_days = int(p["rvol_days"])
    if len(bars) < 30 or len(daily) < n_days:
        return None
    if ctx.get("session") in ("premarket", "afterhours", "crypto"):
        return None
    cur = bars[-1]
    mod = int(cur.get("minute_of_day", -1))
    if not (_OPEN <= mod < _OPEN + int(p["window_min"])):
        return None
    today = _today(bars)
    pm = [b for b in today if int(b.get("minute_of_day", 0)) < _OPEN]
    reg = [b for b in today if int(b.get("minute_of_day", 0)) >= _OPEN]
    if len(pm) < int(p["min_pm_bars"]) or not reg:
        return None
    pm_high = max(float(b["h"]) for b in pm)
    pm_low = min(float(b["l"]) for b in pm)
    close = float(cur["c"])
    if close <= pm_high or any(float(b["c"]) > pm_high for b in reg[:-1]):
        return None                                    # not a break, or not the first one today
    ext_pct = (close - pm_high) / pm_high * 100
    if ext_pct > float(p["max_ext_pct"]):
        return None
    prev_close = float(daily[-1]["c"])
    open_gap_pct = (float(reg[0]["o"]) / prev_close - 1.0) * 100 if prev_close > 0 else 99.0
    if abs(open_gap_pct) > float(p["max_gap_pct"]):
        return None                                    # large gaps are gap-continuation (s04), not a level break

    med_day_vol = _median([float(d.get("v") or 0) for d in daily[-n_days:]])
    if med_day_vol <= 0:
        return None
    per_day = _BARS_PER_DAY.get(str(ctx.get("timeframe")), 78)
    frac = _clamp(len(reg) / per_day, 1.0 / per_day, 1.0)
    reg_vol = sum(float(b.get("v") or 0) for b in reg)
    rvol = reg_vol / (med_day_vol * frac)              # time-aligned relative volume
    if rvol < float(p["min_rvol"]):
        return None

    a = atr(bars, 14)
    vwap = session_vwap(today)
    if not a or a <= 0 or vwap is None or vwap >= close:
        return None
    stop = vwap - float(p["stop_buffer_atr"]) * a
    pm_range = pm_high - pm_low
    unit = max(pm_range, a)                            # a razor-thin PM range still needs a real target
    target1 = pm_high + float(p["tgt_mult"]) * unit
    target2 = pm_high + 2 * float(p["tgt_mult"]) * unit
    if target1 <= close:
        return None

    gap_pct = (pm_high - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
    rng = float(cur["h"]) - float(cur["l"])
    close_loc = (close - float(cur["l"])) / rng if rng > 0 else 0.5
    vwap_dist_atr = (close - vwap) / a
    regime = str(ctx.get("regime") or "")
    comp = {
        "base": 33.0,                                  # every factor saturated sums to exactly 100
        "rvol": 25.0 * _clamp((rvol - float(p["min_rvol"])) / 2.5, 0.0, 1.0),
        "gap": 12.0 * _clamp(gap_pct / 5.0, 0.0, 1.0),
        "close_location": 10.0 * close_loc,
        "vwap_distance": 8.0 * _clamp(vwap_dist_atr, 0.0, 1.0),
        "pm_depth": 6.0 * _clamp(len(pm) / 12.0, 0.0, 1.0),
        "regime": float(_REGIME_ADJ.get(regime, 0)),
    }
    confidence = round(_clamp(sum(comp.values()), 0.0, 100.0), 1)
    expected = int(_clamp(round(unit / a * 3), 3, 30))

    reasons = [
        f"Premarket high {pm_high:.2f} was set over {len(pm)} bars; the bar starting at minute {mod} "
        f"closed {ext_pct:.2f}% above it, the first close through it this session",
        f"Regular-session volume so far is {rvol:.1f}x the time-adjusted {n_days}-day median",
        f"Session VWAP {vwap:.2f} is {vwap_dist_atr:.2f} ATR below the close; stop {stop:.2f} sits "
        f"{p['stop_buffer_atr']} ATR ({a:.2f}) under it",
        f"Overnight repricing: the PM high is {gap_pct:+.2f}% versus the prior close {prev_close:.2f}; "
        f"the 9:30 open gapped {open_gap_pct:+.2f}% (cap {p['max_gap_pct']}%), so this is a level break, not gap-and-go",
        f"PM range {pm_range:.2f} (floored at one ATR) projects target1 {target1:.2f} and target2 {target2:.2f}",
    ]
    return Signal(
        direction="long", entry=close, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back below session VWAP ({vwap:.2f}) during the opening hour means the "
                      f"deeper liquidity rejected the overnight repricing."),
        expected_bars=expected, trailing=None,
        features={"pm_high": pm_high, "pm_low": pm_low, "pm_range": pm_range, "pm_bars": len(pm),
                  "reg_bars": len(reg), "rvol": rvol, "median_day_vol": med_day_vol, "vwap": vwap,
                  "vwap_dist_atr": vwap_dist_atr, "atr14": a, "gap_pct": gap_pct, "open_gap_pct": open_gap_pct, "ext_pct": ext_pct,
                  "close_location": close_loc, "minute_of_day": mod, "regime": regime,
                  "confidence_components": comp},
    )
