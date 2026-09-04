"""Relative-strength leader pulling back to session VWAP and holding.

Names that have out-run SPY by several percent over the past week are being
accumulated by institutions that work orders through the day and are judged
against VWAP; when price dips back to VWAP those desks are natural buyers at
their own benchmark, while the short-term sellers who chased the earlier run
are already out. A hold — a touch within a few tenths of a percent followed by
a close back above — is the footprint of that defence. Falsified if leaders'
VWAP touches resolve upward no more often than laggards' touches do, or if
hold bars fail as often as they succeed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, session_vwap

RTH_OPEN, RTH_CLOSE, FIRST_ENTRY, LAST_ENTRY = 570, 960, 600, 900   # minutes after 00:00 ET
TOUCH_WINDOW = 3                                                     # bars in which the touch must occur
_TF_MIN = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}

META = StrategyMeta(
    id="s03_rs_leader_vwap_pullback",
    name="RS Leader VWAP Pullback Hold",
    family="momentum",
    category="relative_strength_pullback",
    hypothesis=" ".join(__doc__.split()),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="swing_low",
    params={"rs_days": 5, "rs_min_pct": 5.0, "touch_pct": 0.3},
    param_grid={"rs_days": [3, 5, 10], "rs_min_pct": [3.0, 5.0, 8.0],
                "touch_pct": [0.2, 0.3, 0.5]},
    regimes_on=None,
    max_hold_bars=48,
    version="1.0.0",
)


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _day_key(b: dict) -> int:
    return (int(b.get("time", 0)) - int(b.get("minute_of_day", 0)) * 60) // 86400


def _today_rth(bars: List[dict]) -> List[dict]:
    dk, i = _day_key(bars[-1]), len(bars) - 1
    while i > 0 and _day_key(bars[i - 1]) == dk:
        i -= 1
    return [b for b in bars[i:] if RTH_OPEN <= b.get("minute_of_day", 0) < RTH_CLOSE]


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    if ctx.get("market") not in ("stocks", "etf"):
        return None
    bars: List[dict] = ctx["bars"]
    daily: List[dict] = ctx.get("daily") or []
    spy: List[dict] = ctx.get("spy_daily") or []
    rs_days, rs_min, touch_pct = int(_p(cfg, "rs_days")), float(_p(cfg, "rs_min_pct")), float(_p(cfg, "touch_pct"))
    if len(bars) < 20 or len(daily) <= rs_days or len(spy) <= rs_days:
        return None
    cur = bars[-1]
    m = int(cur.get("minute_of_day", 0))
    if m < FIRST_ENTRY or m > LAST_ENTRY:
        return None
    rth = _today_rth(bars)
    if len(rth) < TOUCH_WINDOW + 1 or rth[-1] is not cur:
        return None

    sym_ret = (float(daily[-1]["c"]) / float(daily[-1 - rs_days]["c"]) - 1.0) * 100
    spy_ret = (float(spy[-1]["c"]) / float(spy[-1 - rs_days]["c"]) - 1.0) * 100
    rs = sym_ret - spy_ret
    if rs < rs_min:
        return None
    vwap = session_vwap(rth)
    if not vwap:
        return None
    pre, win = rth[:-TOUCH_WINDOW], rth[-TOUCH_WINDOW:]
    run_high = max(b["h"] for b in pre)
    run_pct = (run_high - vwap) / vwap * 100
    if run_pct < 2.0 * touch_pct:                       # nothing above VWAP to pull back from
        return None
    touch_low = min(b["l"] for b in win)
    touch_dist = (touch_low - vwap) / vwap * 100
    if touch_dist > touch_pct:                          # never came within reach of VWAP
        return None
    if min(b["c"] for b in win) < vwap * (1.0 - touch_pct / 100):   # closed through it: no hold
        return None
    if cur["c"] <= vwap or cur["c"] < cur["o"]:         # hold bar: green and back above VWAP
        return None

    a = atr(bars, 14)
    entry = float(cur["c"])
    buffer = 0.25 * a if a else 0.001 * entry
    stop = touch_low - buffer
    risk = entry - stop
    t1 = max(run_high, entry + risk)
    t2 = t1 + (t1 - entry)
    if not (stop < entry < t1 < t2):
        return None
    rng = cur["h"] - cur["l"]
    close_loc = (cur["c"] - cur["l"]) / rng if rng > 0 else 1.0
    pre_vol = sum(float(b.get("v") or 0) for b in pre) / len(pre)
    win_vol = sum(float(b.get("v") or 0) for b in win) / len(win)
    pull_vol_ratio = win_vol / pre_vol if pre_vol > 0 else 1.0
    comp = {
        "base": 20.0,
        "rs_excess": 25.0 * _clamp((rs - rs_min) / rs_min),
        "touch_precision": 15.0 * _clamp(1.0 - abs(touch_dist) / touch_pct),
        "hold_bar": 15.0 * close_loc,
        "light_pullback_volume": 15.0 * _clamp(1.5 - pull_vol_ratio),
        "run_size": 10.0 * _clamp(run_pct / (4.0 * touch_pct)),
    }
    conf = round(min(100.0, max(0.0, sum(comp.values()))), 1)
    tf_min = _TF_MIN.get(ctx.get("timeframe", ""), 5)
    reasons = [
        "%d-day return %+.1f%% vs SPY %+.1f%%: leads by %.1f points" % (rs_days, sym_ret, spy_ret, rs),
        "Pulled back from a session high %.2f (%.2f%% above VWAP) to a low of %.2f, %.2f%% from VWAP %.2f"
        % (run_high, run_pct, touch_low, touch_dist, vwap),
        "Every close in the last %d bars held within %.1f%% of VWAP; hold bar closed at %.2f, %.0f%% up its range"
        % (TOUCH_WINDOW, touch_pct, entry, close_loc * 100),
        "Pullback volume %.2fx the earlier session average" % pull_vol_ratio,
        "Stop %.2f sits %.4g under the pullback low %.2f" % (stop, buffer, touch_low),
    ]
    return Signal(
        direction="long", entry=round(entry, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4), confidence=conf, reasons=reasons,
        invalidation="A close below the pullback low %.2f means the VWAP defence failed and today's accumulation thesis is wrong."
        % touch_low,
        expected_bars=max(4, int(120 / tf_min)), trailing=None,
        features={"rs_pct": round(rs, 2), "sym_ret_pct": round(sym_ret, 2), "spy_ret_pct": round(spy_ret, 2),
                  "vwap": round(vwap, 4), "run_high": run_high, "run_pct": round(run_pct, 3),
                  "touch_low": touch_low, "touch_dist_pct": round(touch_dist, 3), "close_loc": round(close_loc, 3),
                  "pull_vol_ratio": round(pull_vol_ratio, 2), "atr14": round(a, 6) if a else None,
                  "minute_of_day": m, "confidence_components": comp},
    )
