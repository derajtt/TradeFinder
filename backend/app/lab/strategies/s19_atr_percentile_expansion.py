"""When 14-bar ATR has sat in the bottom fifth of its 100-bar distribution,
positioning is built for quiet: option sellers are short gamma, trend followers
are flat and volatility-targeting funds are at maximum size. As ATR climbs back
through its median while price prints a 20-bar high, those groups are forced
to act in the same direction — gamma hedgers buy the rally, trend systems
re-enter, vol-targeters cut into strength — so the entry is placed at the start
of the expansion and an ATR trail captures the trend without predicting its
length. Falsified if buying 20-bar highs during a 20th-to-50th percentile ATR
climb does not beat buying 20-bar highs at arbitrary ATR levels on a
risk-adjusted basis.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr_series, percentile_rank

META = StrategyMeta(
    id="s19_atr_percentile_expansion",
    name="ATR Percentile Expansion",
    family="volatility",
    category="breakout_vol",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="atr",
    params={"atr_len": 14, "pct_window": 100, "quiet_pct": 20.0, "trigger_pct": 50.0,
            "hh_len": 20, "stop_atr": 2.0, "quiet_lookback": 12, "cross_bars": 3,
            "tgt_atr": 2.0, "max_ext_atr": 2.0, "vol_lookback": 20},
    param_grid={"quiet_pct": [15.0, 20.0, 30.0], "trigger_pct": [40.0, 50.0, 60.0],
                "hh_len": [15, 20, 30], "stop_atr": [1.5, 2.0, 3.0]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)

_REGIME_ADJ = {
    "long": {"trend_up": 8, "high_vol": 4, "low_vol": 0, "range": 0, "trend_down": -5, "bear": -8},
    "short": {"trend_down": 8, "bear": 8, "high_vol": 4, "low_vol": 0, "range": 0, "trend_up": -8},
}


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    m = len(s) // 2
    return s[m] if len(s) % 2 else 0.5 * (s[m - 1] + s[m])


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    bars = ctx.get("bars") or []
    n_atr, win = int(p["atr_len"]), int(p["pct_window"])
    quiet_lb, cross = int(p["quiet_lookback"]), int(p["cross_bars"])
    hh = int(p["hh_len"])
    if len(bars) < max(n_atr + win + quiet_lb + 1, hh + 2):
        return None
    a_ser = atr_series(bars, n_atr)
    last = len(bars) - 1
    a_now = a_ser[last]
    if not a_now or a_now <= 0:
        return None

    def pct_at(j: int) -> float:                       # ATR percentile within its own trailing window
        return percentile_rank(a_ser[j - win + 1:j + 1], a_ser[j]) or 0.0

    pct_now = pct_at(last)
    hist_pct = [pct_at(last - k) for k in range(1, quiet_lb + 1)]   # hist_pct[0] is the previous bar
    trigger, quiet = float(p["trigger_pct"]), float(p["quiet_pct"])
    pct_min = min(hist_pct)
    if pct_now < trigger or min(hist_pct[:cross]) >= trigger or pct_min > quiet:
        return None                                    # not through the median, stale cross, or never quiet

    close = float(bars[last]["c"])
    prior = bars[-hh - 1:-1]
    prior_hh = max(float(b["h"]) for b in prior)
    prior_ll = min(float(b["l"]) for b in prior)
    market = str(ctx.get("market") or "")
    if close > prior_hh:
        direction, level, sign = "long", prior_hh, 1.0
    elif close < prior_ll and market in ("crypto", "etf"):
        direction, level, sign = "short", prior_ll, -1.0
    else:
        return None
    ext_atr = abs(close - level) / a_now
    if ext_atr > float(p["max_ext_atr"]):
        return None                                    # blow-off bar: too far from the level to risk 2 ATR

    stop_atr, tgt_atr = float(p["stop_atr"]), float(p["tgt_atr"])
    stop = close - sign * stop_atr * a_now
    target1 = close + sign * tgt_atr * a_now
    target2 = close + sign * 2 * tgt_atr * a_now

    ref = bars[-int(p["vol_lookback"]) - 1:-1]
    med_vol = _median([float(b.get("v") or 0) for b in ref])
    vol_ratio = float(bars[last].get("v") or 0) / med_vol if med_vol > 0 else None
    regime = str(ctx.get("regime") or "")
    comp = {
        "base": 35.0,
        "pct_rise": 20.0 * _clamp((pct_now - pct_min) / 60.0, 0.0, 1.0),
        "pct_level": 10.0 * _clamp((pct_now - trigger) / 30.0, 0.0, 1.0),
        "breakout": 15.0 * _clamp(ext_atr, 0.0, 1.0),
        "volume": 10.0 * _clamp((vol_ratio - 1.0) / 1.5, 0.0, 1.0) if vol_ratio else 4.0,
        "regime": float(_REGIME_ADJ[direction].get(regime, 0)),
    }
    confidence = round(_clamp(sum(comp.values()), 0.0, 100.0), 1)
    expected = int(_clamp(round(tgt_atr * 5), 5, 40))

    word = "high" if direction == "long" else "low"
    reasons = [
        f"{n_atr}-bar ATR {a_now:.4g} is at percentile {pct_now:.0f} of its {win}-bar range, "
        f"up from percentile {pct_min:.0f} within the last {quiet_lb} bars",
        f"ATR crossed percentile {trigger:.0f} within the last {cross} bars "
        f"(previous bar read {hist_pct[0]:.0f})",
        f"Close {close:.2f} is a {hh}-bar {word}, {ext_atr:.2f} ATR beyond the prior {word} {level:.2f}",
        f"Stop {stop:.2f} is {stop_atr:g} ATR from entry and trails by the same multiple; "
        f"targets {target1:.2f} / {target2:.2f} are {tgt_atr:g}x and {2 * tgt_atr:g}x ATR",
    ]
    if vol_ratio is not None:
        reasons.append(f"Breakout bar volume {vol_ratio:.1f}x the {int(p['vol_lookback'])}-bar median")
    reasons.append(f"Regime {regime or 'unknown'} contributes {comp['regime']:+.0f} confidence")
    return Signal(
        direction=direction, entry=close, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"The ATR percentile falling back under {trigger:.0f} or a close beyond the stop "
                      f"{stop:.2f} means the expansion stalled and the quiet regime is reasserting."),
        expected_bars=expected, trailing={"type": "atr", "mult": stop_atr},
        features={"atr_now": a_now, "atr_pct_now": pct_now, "atr_pct_prev": hist_pct[0],
                  "atr_pct_min": pct_min, "pct_window": win, "level": level, "ext_atr": ext_atr,
                  "vol_ratio": vol_ratio, "regime": regime, "confidence_components": comp},
    )
