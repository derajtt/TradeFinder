"""Beta-hedged ETF pairs z-score reversion.

Hypothesis: ETFs on overlapping baskets (QQQ/SPY, SMH/QQQ, IWM/SPY, XLE/XOP,
GDX/GLD) share most of their cash flows, so their beta-hedged log-price
spread is anchored by arbitrageurs and authorised participants who create and
redeem against the same underlying stocks. Short-lived flow imbalances — a
sector rotation, an options-driven hedge, one large program — push the
spread two standard deviations out; the AR(1) half-life of the spread
measures how fast that anchor reasserts itself, and only spreads that have
been reverting quickly (half-life under 30 bars) are traded. Falsified if
spreads past two sigma with short half-lives reach three sigma as often as
they return to zero, or if the in-window half-life fails to predict the
realised time to revert out of sample.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import rolling_beta

META = StrategyMeta(
    id="s10_etf_pairs_zscore",
    name="ETF Pairs Z-Score",
    family="mean_reversion",
    category="statistical_arbitrage",
    hypothesis=__doc__.strip(),
    markets=["etf", "crypto"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="stdev",
    params={"window": 60, "entry_z": 2.0, "max_half_life": 30.0, "stop_z": 3.0,
            "overshoot_z": 0.5, "min_corr": 0.5},
    param_grid={"window": [40, 60, 90],
                "entry_z": [1.75, 2.0, 2.5],
                "max_half_life": [20.0, 30.0, 45.0]},
    regimes_on=None,
    max_hold_bars=60,
)


def _p(cfg: Dict[str, Any], key: str) -> Any:
    return cfg.get(key, META.params[key])


def _align(bars: List[dict], pair_bars: List[dict]) -> Tuple[List[float], List[float]]:
    """Closes at matching timestamps, oldest first; the partner series is cut
    at the current bar's time so nothing after it can leak in."""
    t_now = bars[-1].get("time")
    px = {b["time"]: float(b["c"]) for b in pair_bars
          if b.get("time") is not None and (t_now is None or b["time"] <= t_now)}
    ys, xs = [], []
    for b in bars:
        x = px.get(b.get("time"))
        if x is not None and x > 0 and float(b["c"]) > 0:
            ys.append(float(b["c"]))
            xs.append(x)
    return ys, xs


def _half_life(spread: List[float]) -> Optional[float]:
    """AR(1) half-life in bars from ds_t = phi * (s_{t-1} - mean); None unless reverting."""
    lag, diff = spread[:-1], [b - a for a, b in zip(spread[:-1], spread[1:])]
    m_lag, m_diff = sum(lag) / len(lag), sum(diff) / len(diff)
    var = sum((s - m_lag) ** 2 for s in lag)
    if var <= 0:
        return None
    phi = sum((s - m_lag) * (d - m_diff) for s, d in zip(lag, diff)) / var
    if phi >= 0 or phi <= -1:
        return None
    return -math.log(2) / math.log(1 + phi)


def _corr(a: List[float], b: List[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else 0.0


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx.get("bars") or []
    pair_bars = ctx.get("pair_bars")
    market = ctx.get("market")
    if not pair_bars or len(bars) < 2:
        return None
    if market != "crypto" and ctx.get("session") in ("premarket", "afterhours"):
        return None
    n = int(_p(cfg, "window"))
    ys, xs = _align(bars, pair_bars)
    if len(ys) < n + 1:
        return None
    ys, xs = ys[-(n + 1):], xs[-(n + 1):]
    beta = rolling_beta(ys, xs, n)
    if beta is None or beta <= 0:
        return None                          # hedge ratio must be positive to mean anything
    spread = [math.log(y) - beta * math.log(x) for y, x in zip(ys, xs)]
    win = spread[-n:]
    mean = sum(win) / n
    sd = math.sqrt(sum((s - mean) ** 2 for s in win) / n)
    if sd <= 0:
        return None
    z = (spread[-1] - mean) / sd
    hl = _half_life(win)
    max_hl = float(_p(cfg, "max_half_life"))
    if hl is None or hl < 1.0 or hl > max_hl:
        return None                          # spread is not reverting fast enough
    ry = [math.log(ys[i] / ys[i - 1]) for i in range(1, len(ys))]
    rx = [math.log(xs[i] / xs[i - 1]) for i in range(1, len(xs))]
    corr = _corr(ry, rx)
    min_corr = float(_p(cfg, "min_corr"))
    if corr < min_corr:
        return None
    entry_z = float(_p(cfg, "entry_z"))
    if z <= -entry_z:
        direction = "long"
    elif z >= entry_z and market in ("etf", "crypto"):
        direction = "short"
    else:
        return None
    stop_z, over_z = float(_p(cfg, "stop_z")), float(_p(cfg, "overshoot_z"))
    lx = math.log(xs[-1])

    def price_at(zz: float) -> float:        # primary price that puts the spread at z = zz
        return math.exp(mean + zz * sd + beta * lx)

    entry = ys[-1]
    sgn = 1.0 if direction == "long" else -1.0
    stop, t1, t2 = price_at(-sgn * stop_z), price_at(0.0), price_at(sgn * over_z)
    if not (sgn * (entry - stop) > 0 and sgn * (t1 - entry) > 0 and sgn * (t2 - t1) > 0):
        return None
    comp = {
        "z_depth": min(30.0, 15.0 + (abs(z) - entry_z) * 25.0),
        "half_life": max(0.0, (max_hl - hl) / max_hl * 25.0),
        "correlation": max(0.0, (corr - min_corr) / (1.0 - min_corr) * 25.0),
    }
    confidence = max(0.0, min(100.0, 20.0 + sum(comp.values())))
    partner = ctx.get("pair_symbol") or "pair partner"
    rr1 = abs(t1 - entry) / abs(entry - stop)
    reasons = [
        f"Beta-hedged spread vs {partner} is {z:.2f} sigma from its {n}-bar mean: "
        f"{ctx.get('symbol', 'primary')} is {'cheap' if direction == 'long' else 'rich'}",
        f"Hedge beta {beta:.3f} on log returns; return correlation {corr:.2f} over the window",
        f"AR(1) half-life {hl:.1f} bars, under the {max_hl:.0f}-bar cap",
        f"Stop at z = {stop_z if direction == 'short' else -stop_z:+.1f} ({stop:.2f}); "
        f"z = 0 target {t1:.2f} is {rr1:.2f}R away",
    ]
    return Signal(
        direction=direction, entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=round(confidence, 1), reasons=reasons,
        invalidation=(f"The spread reaching {stop_z:.0f} sigma (price {stop:.2f}) means "
                      f"the relationship has broken, not stretched."),
        expected_bars=max(3, min(META.max_hold_bars, int(round(hl * 1.5)))),
        trailing=None,
        features={"z": z, "beta": beta, "spread": spread[-1], "spread_mean": mean,
                  "spread_sd": sd, "half_life": hl, "corr": corr,
                  "pair_close": xs[-1], "pair_symbol": ctx.get("pair_symbol"),
                  "window": n, "rr1": rr1, "confidence_components": comp},
    )
