"""Anchored VWAP reclaim from a gap day.

A gap of 3% or more is a repricing event on which institutions build or
unwind positions, and the volume-weighted average price from that session is
their aggregate cost basis. When price later slips below that level and then
reclaims it on above-average volume, the same holders are defending their
entry and short-term sellers who leaned on the break are trapped, so the
level acts as support. Falsified if reclaims of the gap-day AVWAP resolve
lower as often as reclaims of an arbitrary moving average, or if the edge
vanishes once the reclaim-volume filter is removed.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

META = StrategyMeta(
    id="s24_anchored_vwap_reclaim",
    name="Anchored VWAP Reclaim",
    family="volume",
    category="anchored_vwap",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min", "30min", "1hour"],
    hold="swing",
    stop_method="structure",
    params={"gap_min": 3.0, "vol_mult": 1.2, "lost_max": 30, "max_sessions": 15,
            "vol_avg": 20, "atr_len": 14},
    param_grid={"gap_min": [2.0, 3.0, 5.0], "vol_mult": [1.0, 1.2, 1.5],
                "lost_max": [20, 30, 50]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)

MAX_BARS = 3000
RTH_OPEN, RTH_CLOSE = 570, 960        # 09:30 and 16:00 ET in minutes


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mod(b: Dict[str, Any]) -> int:
    return int(b.get("minute_of_day") or 0)


def _find_anchor(bars: List[Dict[str, Any]], gap_min: float, max_sessions: int):
    """Most recent session whose regular open gapped >= gap_min % from the prior
    regular close. Sessions are split where minute_of_day resets (ET). Returns
    (anchor_index, gap_pct, sessions_ago) or None."""
    starts = [0] + [i for i in range(1, len(bars)) if _mod(bars[i]) < _mod(bars[i - 1])]
    for k in range(len(starts) - 1, max(0, len(starts) - 1 - max_sessions), -1):
        s, e = starts[k], (starts[k + 1] if k + 1 < len(starts) else len(bars))
        prev = bars[starts[k - 1]:s]
        rth = [i for i in range(s, e) if _mod(bars[i]) >= RTH_OPEN]
        open_idx = rth[0] if rth else s
        prev_rth = [b for b in prev if _mod(b) < RTH_CLOSE]
        prev_close = float((prev_rth[-1] if prev_rth else prev[-1])["c"])
        if prev_close <= 0:
            continue
        gap = (float(bars[open_idx]["o"]) - prev_close) / prev_close * 100
        if abs(gap) >= gap_min:
            return open_idx, gap, len(starts) - 1 - k
    return None


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    if ctx.get("session") in ("premarket", "afterhours"):   # thin tape, unreliable reclaim
        return None
    bars = ctx["bars"][-MAX_BARS:]
    gap_min, vol_mult = float(_p(cfg, "gap_min")), float(_p(cfg, "vol_mult"))
    lost_max, max_sessions = int(_p(cfg, "lost_max")), int(_p(cfg, "max_sessions"))
    vol_avg, atr_len = int(_p(cfg, "vol_avg")), int(_p(cfg, "atr_len"))
    if len(bars) < max(vol_avg, atr_len) + 3:
        return None
    found = _find_anchor(bars, gap_min, max_sessions)
    if found is None or len(bars) - found[0] < 3:
        return None
    anchor, gap_pct, sessions_ago = found
    seg = bars[anchor:]

    pv = vol = 0.0
    avwap: List[Optional[float]] = []
    for b in seg:
        v = float(b.get("v") or 0)
        if v > 0:
            pv += (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3 * v
            vol += v
        avwap.append(pv / vol if vol > 0 else None)
    cur, prev = seg[-1], seg[-2]
    av_now, av_prev = avwap[-1], avwap[-2]
    if av_now is None or av_prev is None:
        return None
    close = float(cur["c"])
    if not (close > av_now and float(prev["c"]) <= av_prev):   # fresh reclaim only
        return None
    bars_below, had_above, k = 0, False, len(seg) - 2
    while k >= 0 and avwap[k] is not None:
        if float(seg[k]["c"]) > avwap[k]:
            had_above = True
            break
        bars_below += 1
        k -= 1
    if not had_above or bars_below > lost_max:     # never held, or stale anchor
        return None
    vols = [float(b.get("v") or 0) for b in bars]
    avg_vol = sum(vols[-1 - vol_avg:-1]) / vol_avg
    if avg_vol <= 0:
        return None
    vol_ratio = vols[-1] / avg_vol
    if vol_ratio < vol_mult:
        return None
    cur_atr = atr(bars, atr_len)
    if not cur_atr or cur_atr <= 0:
        return None

    h, l = float(cur["h"]), float(cur["l"])
    close_pos = (close - l) / (h - l) if h > l else 0.5
    entry = close
    stop = min(l, av_now) - 0.1 * cur_atr
    risk = entry - stop
    if risk <= 0:
        return None
    post_high = max(float(b["h"]) for b in seg)
    target1 = entry + 1.0 * risk
    target2 = max(post_high, entry + 2.0 * risk)
    reclaim_atr = (close - av_now) / cur_atr

    gap_score = _clamp((abs(gap_pct) - gap_min) / 3.0)
    vol_score = _clamp(vol_ratio - 1.0)
    fresh_score = _clamp(1.0 - bars_below / lost_max)
    confidence = round(35 + 20 * gap_score + 20 * vol_score + 15 * fresh_score
                       + 10 * close_pos, 1)
    reasons = [
        f"Gap of {gap_pct:+.1f}% {sessions_ago} sessions ago anchors VWAP at {av_now:.2f} "
        f"over {len(seg)} bars",
        f"Price closed below the anchored VWAP for {bars_below} bars before this reclaim",
        f"Close {close:.2f} reclaimed the AVWAP by {reclaim_atr:.2f} ATR with the close at "
        f"{close_pos * 100:.0f}% of the bar range",
        f"Reclaim volume {vols[-1]:,.0f} is {vol_ratio:.1f}x the {vol_avg}-bar average",
        f"Stop {stop:.2f} sits below the reclaim bar low {l:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back below the anchored VWAP {av_now:.2f} shows gap-day "
                      f"holders are not defending their cost basis."),
        expected_bars=12,
        trailing={"type": "swing"},
        features={"gap_pct": gap_pct, "sessions_ago": sessions_ago, "avwap": av_now,
                  "bars_below": bars_below, "vol_ratio": vol_ratio, "reclaim_atr": reclaim_atr,
                  "close_pos": close_pos, "post_high": post_high, "atr": cur_atr,
                  "gap_score": gap_score, "vol_score": vol_score,
                  "fresh_score": fresh_score},
    )
