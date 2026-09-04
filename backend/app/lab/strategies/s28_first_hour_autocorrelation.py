"""Intraday return autocorrelation: first-hour momentum.

Hypothesis: the first-hour return predicts the rest-of-day return when the
market is in a regime of positive intraday autocorrelation (Gao, Han, Li and
Zhou 2018, "Market intraday momentum"). The mechanism is institutional order
flow: large orders are sliced across the day by VWAP and participation
algorithms, so a morning imbalance keeps buying into the afternoon, and
late-informed traders join once the open has revealed the direction. The
effect is not constant, it flips sign in some months, so the strategy measures
the trailing correlation between first-hour and rest-of-day returns over the
last N sessions, trades only when it is positive and above a threshold, and
disables itself when it is negative. Entry is the 10:30 ET close after a first
hour up more than 0.5%, with the stop under session VWAP where the morning
buyers' average price is. Falsified if conditioning on the measured
correlation does not separate profitable from unprofitable sessions: rest-of-
day returns after strong first hours would be no better when the trailing
correlation is high than when it is low.
"""
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..base import Signal, StrategyMeta
from ...strategy.indicators import atr, session_vwap

META = StrategyMeta(
    id="s28_first_hour_autocorrelation",
    name="First-Hour Autocorrelation",
    family="statistical",
    category="intraday_momentum",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "index"],
    timeframes=["5min", "15min", "30min", "1hour"],
    hold="intraday",
    stop_method="vwap",
    params={"lookback_sessions": 20, "corr_min": 0.15, "first_hour_min_pct": 0.5},
    param_grid={"lookback_sessions": [10, 20, 30], "corr_min": [0.10, 0.15, 0.25],
                "first_hour_min_pct": [0.3, 0.5, 0.8]},
    regimes_on=None,
    max_hold_bars=66,
    version="1.0.0",
)

_TF_MIN = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}
RTH_OPEN, FIRST_HOUR_END, RTH_CLOSE = 570, 630, 960     # minutes after midnight ET


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _day_key(b: Dict[str, Any]) -> int:
    return int(b["time"]) - int(b.get("minute_of_day") or 0) * 60


def _sessions(bars: Sequence[dict]) -> List[List[dict]]:
    out: List[List[dict]] = []
    for b in bars:
        if out and _day_key(out[-1][-1]) == _day_key(b):
            out[-1].append(b)
        else:
            out.append([b])
    return out


def _split_day(day: Sequence[dict], tf: int) -> Optional[Tuple[float, float, float]]:
    """(first-hour return, rest-of-day return, first-hour volume) for one complete
    regular session; None for half days or sessions missing the open."""
    rth = [b for b in day if RTH_OPEN <= int(b["minute_of_day"]) < RTH_CLOSE]
    fh = [b for b in rth if int(b["minute_of_day"]) < FIRST_HOUR_END]
    rest = [b for b in rth if int(b["minute_of_day"]) >= FIRST_HOUR_END]
    if (not fh or not rest or int(fh[0]["minute_of_day"]) > RTH_OPEN + tf
            or int(rest[-1]["minute_of_day"]) < RTH_CLOSE - 2 * tf):
        return None
    o, c1, c2 = float(fh[0]["o"]), float(fh[-1]["c"]), float(rest[-1]["c"])
    if o <= 0 or c1 <= 0:
        return None
    return c1 / o - 1.0, c2 / c1 - 1.0, sum(float(b.get("v") or 0) for b in fh)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    if len(bars) < 40:
        return None
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    tf = _TF_MIN.get(str(ctx.get("timeframe")), 5)
    cur = bars[-1]
    m = int(cur["minute_of_day"])
    if not (FIRST_HOUR_END - tf <= m < FIRST_HOUR_END):
        return None                       # only the bar that completes the first hour
    days = _sessions(bars)
    today = [b for b in days[-1] if int(b["minute_of_day"]) >= RTH_OPEN]
    if not today or int(today[0]["minute_of_day"]) > RTH_OPEN + tf:
        return None                       # the opening bar must be present
    open_px, entry = float(today[0]["o"]), float(cur["c"])
    fh_ret_pct = (entry / open_px - 1.0) * 100 if open_px > 0 else -1.0
    if fh_ret_pct < float(p["first_hour_min_pct"]):
        return None
    look = int(p["lookback_sessions"])
    hist = [s for s in (_split_day(d, tf) for d in days[:-1][-look:]) if s]
    if len(hist) < max(6, int(0.7 * look)):
        return None
    corr = _pearson([h[0] for h in hist], [h[1] for h in hist])
    corr_min = float(p["corr_min"])
    if corr is None or corr <= 0 or corr < corr_min:
        return None                       # disabled: no positive intraday autocorrelation
    vwap = session_vwap(today)
    a = atr(bars, 14)
    if vwap is None or not a or a <= 0 or entry <= vwap:
        return None
    stop = vwap
    risk = entry - stop
    fh_move = entry - open_px
    t1 = entry + max(1.0 * risk, 0.5 * fh_move)
    t2 = entry + max(2.0 * risk, 1.0 * fh_move)
    fh_vol = sum(float(b.get("v") or 0) for b in today)
    base_vol = sum(h[2] for h in hist) / len(hist)
    rvol = fh_vol / base_vol if base_vol > 0 else 1.0
    dist_atr = risk / a
    comp = {
        "correlation": 30 * _clamp((corr - corr_min) / max(0.05, 0.5 - corr_min)),
        "first_hour_strength": 20 * _clamp(fh_ret_pct / (2 * float(p["first_hour_min_pct"]))),
        "first_hour_volume": 20 * _clamp((rvol - 1.0) / 2.0),
        "vwap_distance": 15 * _clamp(dist_atr / 0.5) if dist_atr <= 2.0 else 5.0,
        "sample_size": 10 * _clamp(len(hist) / look),
        "regime": 5.0 if ctx.get("regime") == "high_vol" else 0.0,
    }
    conf = round(sum(comp.values()), 1)
    reasons = [
        f"Trailing {len(hist)}-session correlation of first-hour vs rest-of-day return is "
        f"{corr:+.2f}, above the {corr_min:.2f} threshold",
        f"First hour up {fh_ret_pct:.2f}% from the {open_px:.2f} open to {entry:.2f}",
        f"First-hour volume {rvol:.1f}x the average first hour of the sample sessions",
        f"Entry is {dist_atr:.2f} ATR ({a:.2f}) above session VWAP {vwap:.2f}, the stop",
        f"Target 1 {t1:.2f} asks the afternoon to carry {t1 - entry:.2f} of the "
        f"{fh_move:.2f} first-hour move",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=conf, reasons=reasons,
        invalidation=(f"A trade back below session VWAP ({vwap:.2f}) puts the morning buyers "
                      "under water and ends the intraday momentum premise for the day."),
        expected_bars=max(1, (RTH_CLOSE - FIRST_HOUR_END) // tf),
        trailing={"type": "atr", "mult": 1.5},
        features={"corr": corr, "n_sessions": len(hist), "first_hour_ret_pct": fh_ret_pct,
                  "first_hour_move": fh_move, "first_hour_rvol": rvol, "vwap": vwap,
                  "vwap_dist_atr": dist_atr, "atr": a, "open": open_px,
                  "conf_components": comp},
    )
