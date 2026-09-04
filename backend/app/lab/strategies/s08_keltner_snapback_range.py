"""Keltner outer-band snapback in range and low-volatility regimes.

Hypothesis: in a range or low-volatility regime a close outside the 2.25-ATR
Keltner band is usually one participant's liquidity demand — an order too
large for the resting book — not the start of a trend, because the volatility
that would fund a trend is absent by construction. When the very next bar
closes back inside the band the aggressor has finished, and the market makers
who were run over re-centre inventory toward the 20-EMA, the mean the band is
drawn around. Falsified if post-excursion bars in quiet regimes travel to the
far band as often as they return to the mid band, or if excursions cluster at
genuine regime changes that the regime label identifies too late.
"""
import math
from typing import Any, Dict, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import adx_series, atr_series, ema_series

META = StrategyMeta(
    id="s08_keltner_snapback_range",
    name="Keltner Snapback (Range)",
    family="mean_reversion",
    category="band_reversion",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto"],
    timeframes=["15min", "30min", "1hour", "4hour"],
    hold="intraday",
    stop_method="structure",
    params={"kc_mult": 2.25, "ema_len": 20, "adx_max": 30.0, "atr_len": 14,
            "stop_buffer_atr": 0.1, "min_rr": 1.0},
    param_grid={"kc_mult": [2.0, 2.25, 2.5],
                "ema_len": [14, 20, 30],
                "adx_max": [25.0, 30.0, 40.0]},
    regimes_on=["range", "low_vol"],
    max_hold_bars=16,
)


def _p(cfg: Dict[str, Any], key: str) -> Any:
    return cfg.get(key, META.params[key])


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx.get("bars") or []
    market = ctx.get("market")
    if ctx.get("regime") not in ("range", "low_vol"):
        return None
    if market != "crypto" and ctx.get("session") in ("premarket", "afterhours"):
        return None
    n_ema, n_atr = int(_p(cfg, "ema_len")), int(_p(cfg, "atr_len"))
    if len(bars) < max(n_ema, n_atr) * 2 + 2:
        return None
    closes = [float(b["c"]) for b in bars]
    ema = ema_series(closes, n_ema)
    atr_s = atr_series(bars, n_atr)
    if atr_s[-2] is None or atr_s[-1] is None or atr_s[-1] <= 0:
        return None
    k = float(_p(cfg, "kc_mult"))
    mid_prev, mid_now = ema[-2], ema[-1]
    a_prev, a_now = atr_s[-2], atr_s[-1]
    prev, cur = bars[-2], bars[-1]
    lower_prev, upper_prev = mid_prev - k * a_prev, mid_prev + k * a_prev
    lower_now, upper_now = mid_now - k * a_now, mid_now + k * a_now
    if prev["c"] < lower_prev and lower_now < cur["c"] < mid_now:
        direction, depth = "long", (lower_prev - prev["c"]) / a_prev
        extreme = min(prev["l"], cur["l"])
        inside_pos = (cur["c"] - lower_now) / (mid_now - lower_now)
    elif (prev["c"] > upper_prev and upper_now > cur["c"] > mid_now
          and market in ("etf", "crypto")):
        direction, depth = "short", (prev["c"] - upper_prev) / a_prev
        extreme = max(prev["h"], cur["h"])
        inside_pos = (upper_now - cur["c"]) / (upper_now - mid_now)
    else:
        return None
    adx = adx_series(bars, 14)[-1]
    adx_max = float(_p(cfg, "adx_max"))
    if adx is not None and adx > adx_max:
        return None                          # a trend is forming; not a range excursion
    vols = [float(b.get("v") or 0) for b in bars[-21:-1]]
    avg_v = sum(vols) / len(vols) if vols else 0.0
    rvol_exc = float(prev.get("v") or 0) / avg_v if avg_v > 0 else 1.0
    buffer_atr = float(_p(cfg, "stop_buffer_atr"))
    entry = float(cur["c"])
    if direction == "long":
        stop, t1, t2 = extreme - buffer_atr * a_now, mid_now, mid_now + a_now
    else:
        stop, t1, t2 = extreme + buffer_atr * a_now, mid_now, mid_now - a_now
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    rr1 = abs(t1 - entry) / risk
    if rr1 < float(_p(cfg, "min_rr")):
        return None
    comp = {
        "excursion_depth": min(30.0, depth * 40.0),
        "snapback_progress": max(0.0, min(15.0, inside_pos * 15.0)),
        "quiet_adx": 8.0 if adx is None else max(0.0, (adx_max - adx) / adx_max * 20.0),
        "climactic_volume": max(0.0, min(20.0, (rvol_exc - 1.0) * 10.0)),
    }
    confidence = max(0.0, min(100.0, 15.0 + sum(comp.values())))   # components sum to 85
    expected = max(2, min(16, int(math.ceil(abs(t1 - entry) / a_now * 2))))
    side = "below the lower" if direction == "long" else "above the upper"
    adx_txt = f"{adx:.1f}" if adx is not None else "n/a"
    reasons = [
        f"Prior bar closed {prev['c']:.2f}, {depth:.2f} ATR {side} {k}x Keltner band "
        f"in a {ctx.get('regime')} regime",
        f"Current bar closed {entry:.2f}, back inside the band and {inside_pos:.0%} of "
        f"the way to the {n_ema}-EMA {mid_now:.2f}",
        f"ADX(14) {adx_txt} is under the {adx_max:.0f} cap; excursion volume was "
        f"{rvol_exc:.2f}x the 20-bar average",
        f"Stop {stop:.2f} sits beyond the excursion extreme {extreme:.2f}; mid-band "
        f"target is {rr1:.2f}R",
    ]
    return Signal(
        direction=direction, entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=round(confidence, 1), reasons=reasons,
        invalidation=(f"A close beyond {stop:.2f}, past the excursion extreme, means "
                      f"the aggressor is still working and the band is being re-priced."),
        expected_bars=expected, trailing=None,
        features={"mid_ema": mid_now, "atr": a_now, "band_lower": lower_now,
                  "band_upper": upper_now, "excursion_depth_atr": depth,
                  "inside_pos": inside_pos, "adx14": adx, "rvol_excursion": rvol_exc,
                  "extreme": extreme, "rr1": rr1, "confidence_components": comp},
    )
