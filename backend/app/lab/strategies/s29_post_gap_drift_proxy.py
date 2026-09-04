"""Post-gap drift: a PROXY for post-earnings announcement drift.

Hypothesis: prices under-react to large fundamental news. After an earnings-
type surprise, institutions accumulate over days because of position-size and
participation limits, analysts revise estimates in steps over the following
weeks, and anchored holders sell into the first rally, so the move continues
beyond the announcement day (post-earnings announcement drift, Bernard and
Thomas 1989). Because the earnings calendar is only available live and not in
the historical data plan, this strategy is a PROXY: an "earnings-like" event
is defined from price and volume alone, a gap up of at least 5% on volume at
least 3x its 20-day average that holds into the close. Entry is the first
close above the gap day's high within the next few sessions, i.e. the market
accepting the new price, with an ATR stop and a 5-10 session hold. Falsified
if such continuation entries earn no excess return over the following 5-10
sessions compared with non-event days, or if the proxy sample is dominated by
non-earnings gaps (offerings, deal rumours, index adds) that do not drift.
"""
from typing import Any, Dict, List, Optional

from ..base import Signal, StrategyMeta
from ...strategy.indicators import atr

META = StrategyMeta(
    id="s29_post_gap_drift_proxy",
    name="Post-Gap Drift Proxy",
    family="event",
    category="post_event_drift",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf"],
    timeframes=["1day"],
    hold="swing",
    stop_method="atr",
    params={"gap_min_pct": 5.0, "vol_mult": 3.0, "entry_window_days": 2, "atr_mult": 1.5},
    param_grid={"gap_min_pct": [4.0, 5.0, 7.0], "vol_mult": [2.0, 3.0, 4.0],
                "entry_window_days": [1, 2, 3], "atr_mult": [1.0, 1.5, 2.0]},
    regimes_on=None,
    max_hold_bars=10,
    version="1.0.0",
)

VOL_AVG_LEN = 20


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _gap_day(bars: List[dict], gap_min_pct: float, vol_mult: float, window: int
             ) -> Optional[Dict[str, Any]]:
    """Most recent qualifying gap day within `window` sessions before the current
    bar: gap >= gap_min_pct on >= vol_mult x 20-day average volume, and the day
    kept at least half of its gap into the close."""
    n = len(bars)
    for k in range(n - 2, max(VOL_AVG_LEN, n - 2 - window), -1):
        prev_c = float(bars[k - 1]["c"])
        if prev_c <= 0:
            continue
        gap_pct = (float(bars[k]["o"]) / prev_c - 1.0) * 100
        if gap_pct < gap_min_pct:
            continue
        vols = [float(b.get("v") or 0) for b in bars[k - VOL_AVG_LEN:k]]
        avg_v = sum(vols) / len(vols)
        vmult = float(bars[k].get("v") or 0) / avg_v if avg_v > 0 else 0.0
        if vmult < vol_mult:
            continue
        if float(bars[k]["c"]) < prev_c * (1 + gap_pct / 200):
            continue                       # gave back more than half the gap
        rng = float(bars[k]["h"]) - float(bars[k]["l"])
        pos = (float(bars[k]["c"]) - float(bars[k]["l"])) / rng if rng > 0 else 1.0
        return {"k": k, "gap_pct": gap_pct, "vmult": vmult, "avg_v": avg_v,
                "prev_c": prev_c, "close_pos": pos}
    return None


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    if len(bars) < VOL_AVG_LEN + 4:
        return None
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    g = _gap_day(bars, float(p["gap_min_pct"]), float(p["vol_mult"]),
                 int(p["entry_window_days"]))
    if g is None:
        return None
    k = g["k"]
    day1 = bars[k]
    d1_high, d1_low = float(day1["h"]), float(day1["l"])
    cur = bars[-1]
    entry = float(cur["c"])
    if entry <= d1_high:
        return None
    for b in bars[k + 1:-1]:               # today must be the FIRST acceptance close
        if float(b["c"]) > d1_high or float(b["c"]) < d1_low:
            return None
    a = atr(bars, 14)
    if not a or a <= 0:
        return None
    stop = entry - float(p["atr_mult"]) * a
    risk = entry - stop
    t1, t2 = entry + 1.0 * risk, entry + 2.5 * risk
    v_ratio = float(cur.get("v") or 0) / g["avg_v"] if g["avg_v"] > 0 else 0.0
    rng = float(cur["h"]) - float(cur["l"])
    close_pos = (entry - float(cur["l"])) / rng if rng > 0 else 1.0
    days_ago = len(bars) - 1 - k
    comp = {
        "gap_size": 25 * _clamp((g["gap_pct"] - float(p["gap_min_pct"])) / 10.0),
        "gap_volume": 25 * _clamp((g["vmult"] - float(p["vol_mult"])) / 5.0),
        "gap_day_close": 15 * _clamp(g["close_pos"]),
        "follow_through_volume": 15 * _clamp((v_ratio - 1.0) / 2.0),
        "entry_bar_close": 10 * _clamp(close_pos),
        "regime": {"trend_up": 10.0, "range": 5.0, "low_vol": 5.0}.get(
            str(ctx.get("regime")), 0.0),
    }
    conf = round(sum(comp.values()), 1)
    reasons = [
        f"Gap day {days_ago} session(s) ago opened {g['gap_pct']:+.1f}% above the prior close "
        f"{g['prev_c']:.2f} on {g['vmult']:.1f}x its 20-day average volume (earnings-like)",
        f"Gap day closed at {float(day1['c']):.2f}, {g['close_pos'] * 100:.0f}% of its range, "
        f"holding the gap",
        f"Today closed {entry:.2f}, the first close above the gap-day high {d1_high:.2f}, "
        f"on {v_ratio:.1f}x average volume",
        f"Stop {stop:.2f} is {p['atr_mult']} ATR ({a:.2f}) below entry; hold 5-10 sessions "
        f"for the drift",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=conf, reasons=reasons,
        invalidation=(f"A close below the gap-day low {d1_low:.2f} (gap filled) or the ATR "
                      f"stop {stop:.2f} says the market rejected the new information."),
        expected_bars=7, trailing={"type": "atr", "mult": 2.0},
        features={"gap_pct": g["gap_pct"], "gap_volume_mult": g["vmult"],
                  "gap_day_close_pos": g["close_pos"], "gap_day_high": d1_high,
                  "gap_day_low": d1_low, "days_since_gap": days_ago,
                  "entry_volume_ratio": v_ratio, "entry_close_pos": close_pos, "atr": a,
                  "conf_components": comp},
    )
