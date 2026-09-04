"""Volume climax reversal.

A decline that ends on a volume spike several sigma above normal with a long
lower wick marks forced selling: margin calls, stop runs and risk limits dump
inventory at any price, and the wick shows that supply was absorbed inside
the bar. Once the forced sellers are out, the remaining holders have no
reason to sell at the low, so price tends to retrace part of the decline.
Falsified if climax bars are followed by lower lows as often as by retraces,
or if the 50% retrace is reached no more often than after ordinary down bars.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

META = StrategyMeta(
    id="s22_volume_climax_reversal",
    name="Volume Climax Reversal",
    family="volume",
    category="capitulation_reversal",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["5min", "15min", "30min", "1hour"],
    hold="intraday",
    stop_method="structure",
    params={"z_min": 3.0, "wick_min": 0.6, "decline_bars": 5, "vol_lookback": 50,
            "min_decline_atr": 1.5, "atr_len": 14},
    param_grid={"z_min": [2.5, 3.0, 3.5], "wick_min": [0.5, 0.6, 0.7],
                "decline_bars": [4, 5, 7]},
    regimes_on=None,
    max_hold_bars=24,
    version="1.0.0",
)

MAX_BARS = 200


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _vol_z(vols: List[float], n: int) -> Optional[float]:
    """Z-score of the last volume against the PRIOR n bars (spike excluded)."""
    if len(vols) < n + 1:
        return None
    ref = vols[-n - 1:-1]
    m = sum(ref) / n
    sd = (sum((x - m) ** 2 for x in ref) / n) ** 0.5
    return (vols[-1] - m) / sd if sd > 0 else None


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx["bars"][-MAX_BARS:]
    z_min, wick_min = float(_p(cfg, "z_min")), float(_p(cfg, "wick_min"))
    decline_bars, vol_lookback = int(_p(cfg, "decline_bars")), int(_p(cfg, "vol_lookback"))
    min_decline_atr, atr_len = float(_p(cfg, "min_decline_atr")), int(_p(cfg, "atr_len"))
    if len(bars) < max(vol_lookback + 1, decline_bars + 2, atr_len + 1):
        return None

    cur = bars[-1]
    o, h, l, c = float(cur["o"]), float(cur["h"]), float(cur["l"]), float(cur["c"])
    rng = h - l
    if rng <= 0:
        return None
    wick_frac = (min(o, c) - l) / rng
    close_pos = (c - l) / rng
    if wick_frac < wick_min:
        return None
    vols = [float(b.get("v") or 0) for b in bars]
    vz = _vol_z(vols, vol_lookback)
    if vz is None or vz < z_min:
        return None
    cur_atr = atr(bars, atr_len)
    if not cur_atr or cur_atr <= 0:
        return None

    window = bars[-1 - decline_bars:-1]
    pre_close = float(bars[-2 - decline_bars]["c"])
    decline_high = max(float(b["h"]) for b in window)
    win_low = min(float(b["l"]) for b in window)
    down_count = sum(1 for i in range(len(window))
                     if float(window[i]["c"]) < float((window[i - 1] if i else bars[-2 - decline_bars])["c"]))
    decline_atr = (decline_high - l) / cur_atr
    if float(window[-1]["c"]) >= pre_close or l >= win_low or decline_atr < min_decline_atr:
        return None
    decline_pct = (decline_high - l) / decline_high * 100

    entry = c
    stop = l - 0.1 * cur_atr
    risk = entry - stop
    target1 = l + 0.5 * (decline_high - l)          # 50% retrace of the decline
    target2 = decline_high                           # full retrace
    if risk <= 0 or target1 < entry + 0.5 * risk:    # bar already took the retrace
        return None

    z_score = _clamp((vz - z_min) / 2.0)
    wick_score = _clamp((wick_frac - wick_min) / 0.3)
    depth_score = _clamp((decline_atr - min_decline_atr) / 2.0)
    confidence = round(35 + 20 * z_score + 20 * wick_score + 15 * depth_score
                       + 10 * close_pos, 1)
    reasons = [
        f"Volume {vols[-1]:,.0f} is {vz:.1f} sigma above the prior {vol_lookback}-bar mean",
        f"Lower wick covers {wick_frac * 100:.0f}% of the {rng:.2f} bar range and the close "
        f"sits at {close_pos * 100:.0f}% of it",
        f"Decline of {decline_pct:.1f}% ({decline_atr:.1f} ATR) over the prior {decline_bars} "
        f"bars, {down_count} of them down closes",
        f"Climax low {l:.2f} undercut the prior {decline_bars}-bar low {win_low:.2f}",
        f"Target1 {target1:.2f} is the 50% retrace of the drop from {decline_high:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=f"A close below the climax low {l:.2f} means the selling was not exhausted.",
        expected_bars=decline_bars * 2,
        trailing=None,
        features={"volume_z": vz, "wick_frac": wick_frac, "close_pos": close_pos,
                  "decline_atr": decline_atr, "decline_pct": decline_pct,
                  "decline_high": decline_high, "down_count": down_count,
                  "atr": cur_atr, "z_score": z_score, "wick_score": wick_score,
                  "depth_score": depth_score},
    )
