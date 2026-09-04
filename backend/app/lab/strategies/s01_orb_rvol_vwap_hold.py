"""Opening-range breakout confirmed by relative volume and a VWAP hold.

The first 15-30 minutes of the regular session price the overnight information
flow; the range they leave is where early buyers and sellers agreed to
transact. A close above that range with cumulative volume at least twice the
same-time-of-day norm says new participants are arriving rather than the same
inventory being repriced, and a bar that never lost session VWAP says the
average buyer today is in profit and has no reason to sell into the break.
Falsified if RVOL-confirmed breaks that hold VWAP retrace into the range as
often as unconfirmed breaks do.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, session_vwap

RTH_OPEN, RTH_CLOSE, LAST_ENTRY = 570, 960, 720          # minutes after 00:00 ET
_TF_MIN = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}

META = StrategyMeta(
    id="s01_orb_rvol_vwap_hold",
    name="Opening Range Break + RVOL + VWAP Hold",
    family="momentum",
    category="opening_range_breakout",
    hypothesis=" ".join(__doc__.split()),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="structure",
    params={"range_minutes": 30, "rvol_min": 2.0, "t1_mult": 1.0},
    param_grid={"range_minutes": [15, 30, 45], "rvol_min": [1.5, 2.0, 3.0],
                "t1_mult": [0.75, 1.0, 1.5]},
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
    """Cumulative RTH volume through cur_min today vs the same window's mean over up to
    n_days prior sessions in `bars` (a session missing its opening bar -- the truncated
    oldest day of a capped history -- is dropped); <3 priors -> fraction of 20-day daily volume."""
    dk, today, prior, first = _day_key(bars[-1]), 0.0, {}, {}   # type: ignore[var-annotated]
    for b in reversed(bars):
        k = _day_key(b)
        if k != dk and k not in prior:
            if len(prior) >= n_days:
                break
            prior[k] = 0.0
        m = b.get("minute_of_day", 0)
        if m < RTH_OPEN or m > cur_min:
            continue
        v = float(b.get("v") or 0)
        if k == dk:
            today += v
        else:
            prior[k] += v
            first[k] = m                 # walking backwards, so this ends at the day's earliest RTH bar
    base = [x for k, x in prior.items() if x > 0 and first[k] <= RTH_OPEN + tf_min]
    if len(base) >= 3:
        return today / (sum(base) / len(base)), "same-minute mean of %d prior sessions" % len(base)
    vols = [float(d.get("v") or 0) for d in daily[-20:]]
    if not vols or sum(vols) <= 0:
        return None, "unavailable"
    frac = 0.05 + 0.95 * _clamp((cur_min + tf_min - RTH_OPEN) / 390.0)
    return today / (sum(vols) / len(vols) * frac), "time-of-day fraction of 20-day daily volume"


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    if ctx.get("market") not in ("stocks", "etf"):
        return None
    bars: List[dict] = ctx["bars"]
    if len(bars) < 3:
        return None
    cur, prev = bars[-1], bars[-2]
    m = int(cur.get("minute_of_day", 0))
    range_min = int(cfg.get("range_minutes", META.params["range_minutes"]))
    rvol_min = float(cfg.get("rvol_min", META.params["rvol_min"]))
    t1_mult = float(cfg.get("t1_mult", META.params["t1_mult"]))
    range_end = RTH_OPEN + range_min
    if m < range_end or m > LAST_ENTRY:
        return None
    rth = [b for b in _today(bars) if RTH_OPEN <= b["minute_of_day"] < RTH_CLOSE]
    orb = [b for b in rth if b["minute_of_day"] < range_end]
    if not orb or orb[0]["minute_of_day"] > RTH_OPEN + 5:       # need the actual open
        return None
    or_high, or_low = max(b["h"] for b in orb), min(b["l"] for b in orb)
    range_h = or_high - or_low
    if range_h <= 0 or not (cur["c"] > or_high >= prev["c"]):    # first close above the range only
        return None
    ext = (cur["c"] - or_high) / range_h
    if ext > 0.5:                                                 # do not chase an extended break
        return None
    vwap = session_vwap(rth)
    if not vwap or cur["c"] <= vwap or cur["l"] < vwap:           # the break bar must hold VWAP
        return None
    tf_min, daily = _TF_MIN.get(ctx.get("timeframe", ""), 5), ctx.get("daily") or []
    rvol, basis = _rvol(bars, m, tf_min, daily)
    if rvol is None or rvol < rvol_min:
        return None

    orb_vol = sum(float(b.get("v") or 0) for b in orb) / len(orb)
    brk_vol_ratio = float(cur.get("v") or 0) / orb_vol if orb_vol > 0 else 0.0
    d_atr = atr(daily, 14) if len(daily) >= 15 else None
    range_atr_ratio = range_h / d_atr if d_atr else None
    entry, vwap_pos = float(cur["c"]), (vwap - or_low) / range_h   # vwap_pos: where buyers sat in the range
    stop_ref, stop_basis = (vwap, "session VWAP") if vwap > or_low else (or_low, "opening-range low")
    stop = stop_ref - 0.1 * range_h
    t1, t2 = or_high + t1_mult * range_h, or_high + 2.0 * t1_mult * range_h
    if not (stop < entry < t1 < t2):
        return None

    comp = {
        "base": 20.0,
        "rvol": 25.0 * _clamp((rvol - rvol_min) / rvol_min),
        "break_bar_volume": 15.0 * _clamp((brk_vol_ratio - 1.0) / 2.0),
        "low_extension": 15.0 * (1.0 - ext / 0.5),
        "range_compression": 15.0 * _clamp((1.0 - range_atr_ratio) / 0.7) if range_atr_ratio else 0.0,
        "vwap_in_range": 10.0 * _clamp(vwap_pos),
    }
    conf = round(min(100.0, max(0.0, sum(comp.values()))), 1)
    vwap_dist = (entry - vwap) / vwap * 100
    reasons = [
        "Closed at %.2f, first close above the %d-minute opening range high %.2f (range %.2f wide)"
        % (entry, range_min, or_high, range_h),
        "Relative volume %.1fx (%s)" % (rvol, basis),
        "Break bar volume %.1fx the average opening-range bar" % brk_vol_ratio,
        "Price %.2f%% above session VWAP %.2f and the bar's low never lost it" % (vwap_dist, vwap),
        "Break has used only %.0f%% of the range height beyond the level" % (ext * 100),
    ]
    if range_atr_ratio is not None:
        reasons.append("Opening range is %.2fx the 14-day ATR" % range_atr_ratio)
    return Signal(
        direction="long", entry=round(entry, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4), confidence=conf, reasons=reasons,
        invalidation="A close back below the %s at %.2f means the break drew no follow-through and the setup is wrong."
        % (stop_basis, stop_ref),
        expected_bars=max(4, int(90 / tf_min)), trailing=None,
        features={"or_high": or_high, "or_low": or_low, "range_h": range_h, "ext_of_range": round(ext, 3),
                  "vwap": vwap, "vwap_dist_pct": round(vwap_dist, 3), "vwap_pos_in_range": round(vwap_pos, 3),
                  "rvol": round(rvol, 2), "rvol_basis": basis, "break_bar_vol_ratio": round(brk_vol_ratio, 2),
                  "range_atr_ratio": round(range_atr_ratio, 3) if range_atr_ratio else None,
                  "stop_basis": stop_basis, "minute_of_day": m, "confidence_components": comp},
    )
