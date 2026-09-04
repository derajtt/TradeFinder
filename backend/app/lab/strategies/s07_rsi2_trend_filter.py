"""RSI(2) pullback inside a long-term uptrend (Connors-style).

Hypothesis: a stock above its 200-day average is held by trend followers and
long-only funds whose demand is persistent, so a two-day RSI under 10 marks a
short-horizon liquidation — stop-outs, a bad tape, a margin call — that
briefly overwhelms that standing bid without changing its reason to exist.
Short-term overreaction inside long-term trends is a documented equity
anomaly: patient buyers absorb the forced selling and price snaps back to the
5-day mean within a few sessions. Falsified if two-day pullbacks in uptrends
continue lower over the next 3-5 sessions as often as they revert, or if the
snapback is smaller than the round-trip cost.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, rsi_series, sma

META = StrategyMeta(
    id="s07_rsi2_trend_filter",
    name="RSI(2) Pullback in Uptrend",
    family="mean_reversion",
    category="oversold_in_trend",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf"],
    timeframes=["1day"],
    hold="swing",
    stop_method="swing_low",
    params={"rsi_max": 10.0, "trend_sma": 200, "swing_lookback": 5,
            "stop_atr_mult": 0.5, "exit_sma": 5},
    param_grid={"rsi_max": [5.0, 10.0, 15.0],
                "trend_sma": [150, 200, 250],
                "swing_lookback": [3, 5, 8]},
    regimes_on=["trend_up", "range", "low_vol", "high_vol"],
    max_hold_bars=8,
)


def _p(cfg: Dict[str, Any], key: str) -> Any:
    return cfg.get(key, META.params[key])


def _daily_closes(ctx: Dict[str, Any], bars: List[dict]) -> List[float]:
    """Daily closes through today: the working bars when they are daily,
    otherwise prior-session dailies plus the live close."""
    if ctx.get("timeframe") == "1day":
        return [float(b["c"]) for b in bars]
    return [float(b["c"]) for b in (ctx.get("daily") or [])] + [float(bars[-1]["c"])]


def _down_streak(closes: List[float]) -> int:
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            n += 1
        else:
            break
    return n


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx.get("bars") or []
    if ctx.get("regime") in ("bear", "trend_down") or len(bars) < 30:
        return None
    n_trend = int(_p(cfg, "trend_sma"))
    dcl = _daily_closes(ctx, bars)
    trend = sma(dcl, n_trend)
    if trend is None or dcl[-1] <= trend:
        return None                          # long-term uptrend filter
    closes = [float(b["c"]) for b in bars]
    r2 = rsi_series(closes, 2)[-1]
    rsi_max = float(_p(cfg, "rsi_max"))
    if r2 is None or r2 >= rsi_max:
        return None
    n_exit = int(_p(cfg, "exit_sma"))
    s_exit = sma(closes, n_exit)
    a = atr(bars, 14)
    if s_exit is None or closes[-1] >= s_exit or not a or a <= 0:
        return None                          # must be a pullback below the short mean
    n_sw = int(_p(cfg, "swing_lookback"))
    swing_low = min(float(b["l"]) for b in bars[-n_sw:])
    stop_mult = float(_p(cfg, "stop_atr_mult"))
    stop = swing_low - stop_mult * a
    entry = closes[-1]
    risk = entry - stop
    if risk <= 0:
        return None
    t1 = s_exit                              # exit when price closes back over the 5 SMA
    hi10 = max(float(b["h"]) for b in bars[-10:])
    t2 = max(hi10, t1 + (t1 - entry))
    streak = _down_streak(closes)
    dist_atr = (s_exit - entry) / a
    pct_above = (entry / trend - 1.0) * 100.0
    comp = {
        "rsi_depth": (rsi_max - r2) / rsi_max * 30.0,
        "stretch_below_mean": min(25.0, dist_atr * 15.0),
        "trend_strength": min(20.0, max(0.0, pct_above) * 2.0),
        "down_streak": min(10.0, streak * 3.5),
    }
    confidence = max(0.0, min(100.0, 15.0 + sum(comp.values())))   # components sum to 85
    rr1 = (t1 - entry) / risk
    reasons = [
        f"RSI(2) is {r2:.1f}, under the {rsi_max:.0f} oversold line",
        f"Close {entry:.2f} is {pct_above:.1f}% above the {n_trend}-day SMA {trend:.2f}: "
        f"long-term uptrend intact",
        f"Close sits {dist_atr:.2f} ATR below the {n_exit}-day SMA {s_exit:.2f} after "
        f"{streak} straight down closes",
        f"Stop {stop:.2f} is the {n_sw}-bar swing low {swing_low:.2f} less {stop_mult} ATR "
        f"({a:.3f}); first target is {rr1:.2f}R",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=round(confidence, 1), reasons=reasons,
        invalidation=(f"A daily close below {stop:.2f} or below the {n_trend}-day SMA "
                      f"means the pullback is a trend break, not an overreaction."),
        expected_bars=4, trailing=None,
        features={"rsi2": r2, "trend_sma": trend, "pct_above_trend": pct_above,
                  "exit_sma": s_exit, "dist_below_exit_atr": dist_atr,
                  "swing_low": swing_low, "atr14": a, "down_streak": streak,
                  "rr1": rr1, "confidence_components": comp},
    )
