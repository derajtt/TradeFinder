"""EMA ribbon (8/13/21/34) pullback to the 21, reclaim of the 8.

When the 8/13/21/34 EMAs are stacked in order, participants on every horizon
from a day to a month are positioned the same way with average cost below
price, so a retracement to the 21 EMA brings price back to where the
medium-horizon holder is roughly flat and the short-horizon trader has been
shaken out. Those two groups re-enter on the first close back above the 8 EMA
and dip buyers who missed the prior leg join them; the edge is continuation
after a retracement shallow enough (closes above the 34 EMA) to leave the
trend structure intact. The stop sits under the lower of the 34 EMA and the
pullback low, because a close there puts the medium-horizon group under water
and removes the premise. Falsified if the first reclaim of the 8 EMA after a
21-touch does not produce a new leg high more often than it fails, or if
ribbon alignment carries no information about the next 15 bars' drift.
"""
from typing import Any, Dict, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, ema_series

# Long only: a bearish ribbon pullback is a different structure (short covering
# is faster and less orderly than dip buying) and is not modelled here.
META = StrategyMeta(
    id="s12_ema_ribbon_pullback",
    name="EMA Ribbon Pullback",
    family="trend",
    category="trend_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="structure",
    params={"lookback": 3, "align_bars": 5, "min_spread_atr": 0.5, "stop_buffer_atr": 0.1},
    param_grid={"lookback": [2, 3, 5], "align_bars": [3, 5, 8],
                "min_spread_atr": [0.3, 0.5, 0.8]},
    regimes_on=None,
    max_hold_bars=30,
    version="1.0.0",
)

MAX_BARS = 400   # trailing window: pure function of the supplied slice, bounds cost
MIN_BARS = 60


def _scale(x: float, lo: float, hi: float, pts: float) -> float:
    if hi == lo:
        return 0.0
    return pts * max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _vol_ratio(bars, n: int = 20) -> float:
    vols = [float(b.get("v") or 0) for b in bars[-(n + 1):-1]]
    avg = sum(vols) / len(vols) if vols else 0.0
    v = float(bars[-1].get("v") or 0)
    return v / avg if avg > 0 and v > 0 else 1.0


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = dict(META.params)
    p.update(cfg or {})
    k, align_n = int(p["lookback"]), int(p["align_bars"])
    min_spread, buf = float(p["min_spread_atr"]), float(p["stop_buffer_atr"])
    bars = (ctx.get("bars") or [])[-MAX_BARS:]
    if len(bars) < MIN_BARS:
        return None
    closes = [float(b["c"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    e8, e13, e21, e34 = (ema_series(closes, n) for n in (8, 13, 21, 34))
    a = atr(bars, 14)
    if not a or a <= 0:
        return None

    def stacked(j: int) -> bool:
        return e8[j] > e13[j] > e21[j] > e34[j]

    # Trend must be established, not a fresh cross: stacked for the whole window.
    if not all(stacked(j) for j in range(-align_n, 0)):
        return None
    spread = (e8[-1] - e34[-1]) / a
    if spread < min_spread:                       # ribbon too compressed to mean anything
        return None
    win = range(-k, 0)                            # pullback window INCLUDING the current bar
    touched = [j for j in win if lows[j] <= e21[j]]
    if not touched:
        return None
    if any(closes[j] < e34[j] for j in win):      # retracement was not controlled
        return None
    close = closes[-1]
    if close <= e8[-1]:
        return None
    # Only the FIRST close back above the 8 after the touch, not every later bar.
    if closes[-2] > e8[-2] and lows[-1] > e21[-1]:
        return None
    pull_low = min(lows[j] for j in win)
    stop = min(e34[-1], pull_low) - buf * a
    risk = close - stop
    if risk <= 0:
        return None
    t1, t2 = close + 1.5 * risk, close + 3.0 * risk

    slope21 = (e21[-1] - e21[-6]) / a             # 5-bar slope of the touched EMA, in ATR
    above8 = (close - e8[-1]) / a
    vr = _vol_ratio(bars, 20)
    aligned_len = 0
    for j in range(-1, -min(len(bars), 30) - 1, -1):
        if not stacked(j):
            break
        aligned_len += 1
    comps = {"spread": _scale(spread, min_spread, min_spread + 2.0, 25),
             "slope21": _scale(slope21, 0.0, 1.0, 20),
             "reclaim": _scale(above8, 0.0, 0.5, 15),
             "volume": _scale(vr, 0.8, 1.8, 15),
             "aligned": _scale(aligned_len, align_n, 30, 15)}
    conf = 10.0 + sum(comps.values())
    reasons = [
        f"Ribbon stacked 8>13>21>34 for {aligned_len} bars; 8-34 spread {spread:.2f} ATR",
        f"Pullback low {pull_low:.2f} touched the 21 EMA ({e21[touched[0]]:.2f}) within "
        f"the last {k} bars and every close held above the 34 EMA {e34[-1]:.2f}",
        f"Close {close:.2f} is back above the 8 EMA {e8[-1]:.2f} by {above8:.2f} ATR",
        f"21 EMA rising {slope21:.2f} ATR over the last 5 bars",
        f"Volume {vr:.2f}x the 20-bar average on the reclaim bar",
        f"Stop {stop:.2f} sits {buf:.1f} ATR under the lower of the 34 EMA and the pullback low",
    ]
    return Signal(
        direction="long",
        entry=round(close, 4), stop=round(stop, 4),
        target1=round(t1, 4), target2=round(t2, 4),
        confidence=round(min(100.0, conf), 1),
        reasons=reasons,
        invalidation=(f"A close below {stop:.2f}, under both the 34 EMA and the pullback low, "
                      f"puts the medium-horizon holders under water and voids the continuation."),
        expected_bars=15,
        trailing={"type": "swing"},
        features={"ema8": round(e8[-1], 4), "ema13": round(e13[-1], 4),
                  "ema21": round(e21[-1], 4), "ema34": round(e34[-1], 4),
                  "atr14": round(a, 4), "spread_atr": round(spread, 3),
                  "slope21_atr": round(slope21, 3), "above8_atr": round(above8, 3),
                  "vol_ratio": round(vr, 3), "aligned_bars": aligned_len,
                  "pullback_low": round(pull_low, 4),
                  "conf_components": {k_: round(v, 1) for k_, v in comps.items()}},
    )
