"""Low-volatility drift.

Leverage-constrained and benchmark-tracking investors overpay for high-beta
names with lottery-like payoffs and neglect quiet ones, so calm assets earn
more return per unit of risk than their volatility warrants (the low-vol
anomaly). When realised range sits in its bottom quintile and the 50-bar
mean is rising, holders are not being forced out and the drift persists, so
a wide ATR stop is rarely tagged. Falsified if calm-uptrend entries earn no
better risk-adjusted return than random entries in the same names, or if the
quiet state mostly precedes volatility expansion against the trend.
"""
from typing import Any, Dict, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr_series, percentile_rank, sma

META = StrategyMeta(
    id="s21_low_vol_drift",
    name="Low-Volatility Drift",
    family="volatility",
    category="low_vol_anomaly",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "index", "crypto"],
    timeframes=["1hour", "4hour", "1day"],
    hold="swing",
    stop_method="atr",
    params={"atr_len": 14, "lookback": 100, "pct_max": 20.0, "sma_len": 50,
            "stop_mult": 3.0, "slope_bars": 5, "cons_bars": 20},
    param_grid={"lookback": [80, 100, 150], "pct_max": [15.0, 20.0, 30.0],
                "sma_len": [30, 50, 80], "stop_mult": [2.5, 3.0, 4.0]},
    regimes_on=["trend_up", "range", "low_vol"],
    max_hold_bars=40,
    version="1.0.0",
)

MAX_BARS = 400  # fixed tail: history depth beyond this never changes the answer


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    if ctx.get("regime") == "bear":          # long-only drift; no drift to ride
        return None
    bars = ctx["bars"][-MAX_BARS:]
    atr_len, lookback = int(_p(cfg, "atr_len")), int(_p(cfg, "lookback"))
    pct_max, sma_len = float(_p(cfg, "pct_max")), int(_p(cfg, "sma_len"))
    stop_mult, slope_bars = float(_p(cfg, "stop_mult")), int(_p(cfg, "slope_bars"))
    cons_bars = int(_p(cfg, "cons_bars"))
    if len(bars) < max(lookback + atr_len + 1, sma_len + max(slope_bars, cons_bars) + 1):
        return None

    closes = [float(b["c"]) for b in bars]
    atrs = atr_series(bars, atr_len)
    cur_atr = atrs[-1]
    if not cur_atr or cur_atr <= 0 or closes[-1] <= 0:
        return None
    # Rank ATR as a % of price so a trending instrument does not bias the window.
    natr = [a / c * 100 for a, c in zip(atrs[-lookback:], closes[-lookback:]) if a and c > 0]
    cur_natr = cur_atr / closes[-1] * 100
    pct = percentile_rank(natr, cur_natr)
    if pct is None or pct >= pct_max:
        return None

    sma_now = sma(closes, sma_len)
    sma_prev = sma(closes[:-slope_bars], sma_len)
    if sma_now is None or sma_prev is None or sma_now <= sma_prev:
        return None
    close = closes[-1]
    if close <= sma_now:
        return None
    slope_pct = (sma_now - sma_prev) / sma_prev * 100
    slope_atr = (sma_now - sma_prev) / cur_atr
    above = 0
    for k in range(cons_bars):
        s = sma(closes[:len(closes) - k], sma_len)
        if s is not None and closes[-1 - k] > s:
            above += 1
    above_frac = above / cons_bars
    ext_atr = (close - sma_now) / cur_atr

    vol_score = _clamp((pct_max - pct) / pct_max)
    slope_score = _clamp(slope_atr / 0.5)
    ext_score = 1.0 if ext_atr <= 2.0 else _clamp(1.0 - (ext_atr - 2.0) / 3.0)
    confidence = round(30 + 25 * vol_score + 20 * slope_score
                       + 15 * above_frac + 10 * ext_score, 1)

    entry = close
    risk = stop_mult * cur_atr
    stop = entry - risk
    target1 = entry + 1.0 * risk
    target2 = entry + 2.0 * risk
    reasons = [
        f"Normalised ATR {cur_natr:.2f}% is at the {pct:.0f}th percentile of the "
        f"last {lookback} bars (threshold {pct_max:.0f})",
        f"Close {close:.2f} is {ext_atr:.1f} ATR above the rising {sma_len}-bar SMA {sma_now:.2f}",
        f"{sma_len}-bar SMA rose {slope_pct:.2f}% ({slope_atr:.2f} ATR) over the last {slope_bars} bars",
        f"{above_frac * 100:.0f}% of the last {cons_bars} closes held above the SMA",
        f"Stop {stop:.2f} is {stop_mult:.1f} ATR ({risk:.2f}) below entry",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close below {stop:.2f} ({stop_mult:.1f} ATR under entry) or a "
                      f"{sma_len}-bar SMA that turns down ends the quiet-drift thesis."),
        expected_bars=30,
        trailing={"type": "atr", "mult": stop_mult},
        features={"atr": cur_atr, "natr_pct": cur_natr, "atr_percentile": pct,
                  "sma": sma_now, "sma_slope_pct": slope_pct, "sma_slope_atr": slope_atr,
                  "above_frac": above_frac, "ext_atr": ext_atr,
                  "vol_score": vol_score, "slope_score": slope_score,
                  "ext_score": ext_score},
    )
