"""Sector relative-strength rotation pullback.

Hypothesis: cross-sectional momentum. Capital rotates into leading sectors
slowly because the buyers are benchmark-relative mandates, quarterly
rebalancers and analysts revising estimates in steps; none of them can move in
a day, so a sector that has beaten SPY by a wide margin over 20 sessions keeps
attracting flow for weeks. Buying a pullback that holds the rising 20 EMA
inside that leadership avoids paying the breakout premium and puts the stop
where the leadership thesis is wrong. The cross-section is proxied by a fixed
excess-return threshold (instrument 20-day return more than 3% above SPY's,
roughly the top third of a sector-ETF set) because only the instrument and SPY
are available in ctx. Falsified if pullback entries in RS leaders fail to beat
holding SPY over the same 5-15 day windows, or if the excess return mean-
reverts within a week (leadership is noise, not flow). Known failure mode:
momentum crashes in bear-market rebounds, so the strategy stands down in the
bear regime.
"""
from typing import Any, Dict, List, Optional, Sequence

from ..base import Signal, StrategyMeta
from ...strategy.indicators import atr, ema_series

META = StrategyMeta(
    id="s27_sector_rs_rotation_pullback",
    name="Sector RS Rotation Pullback",
    family="relative_strength",
    category="cross_sectional_momentum",
    hypothesis=(__doc__ or "").strip(),
    markets=["etf", "stocks"],
    timeframes=["1day"],
    hold="swing",
    stop_method="atr",
    params={"rs_window": 20, "rs_excess_pct": 3.0, "pullback_days": 3, "atr_mult": 2.0},
    param_grid={"rs_window": [15, 20, 30], "rs_excess_pct": [2.0, 3.0, 5.0],
                "pullback_days": [2, 3, 4], "atr_mult": [1.5, 2.0, 2.5]},
    regimes_on=["trend_up", "trend_down", "range", "high_vol", "low_vol"],
    max_hold_bars=15,
    version="1.0.0",
)

EMA_LEN = 20
EMA_TOL_PCT = 0.5      # pullback low may undercut the EMA by this much and still "hold"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _idx_at_or_before(seq: Sequence[dict], t: int) -> Optional[int]:
    for i in range(len(seq) - 1, -1, -1):
        if int(seq[i]["time"]) <= t:
            return i
    return None


def _excess_return(bars: Sequence[dict], spy: Sequence[dict], n: int
                   ) -> Optional[Dict[str, float]]:
    """Instrument vs SPY return over n sessions, aligned by bar time on the last
    session both series have (spy_daily may lag the instrument by one session)."""
    if len(bars) < n + 2 or len(spy) < n + 2:
        return None
    k_end = _idx_at_or_before(spy, int(bars[-1]["time"]))
    if k_end is None or k_end < n:
        return None
    i_end = _idx_at_or_before(bars, int(spy[k_end]["time"]))
    if i_end is None or i_end < n:
        return None
    inst = float(bars[i_end]["c"]) / float(bars[i_end - n]["c"]) - 1.0
    mkt = float(spy[k_end]["c"]) / float(spy[k_end - n]["c"]) - 1.0
    return {"inst_ret_pct": inst * 100, "spy_ret_pct": mkt * 100,
            "excess_pct": (inst - mkt) * 100}


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars: List[dict] = ctx["bars"]
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    n, pd = int(p["rs_window"]), int(p["pullback_days"])
    if len(bars) < max(n + 2, EMA_LEN + pd + 6) or ctx.get("regime") == "bear":
        return None
    rs = _excess_return(bars, ctx.get("spy_daily") or [], n)
    if rs is None or rs["excess_pct"] < float(p["rs_excess_pct"]):
        return None
    closes = [float(b["c"]) for b in bars]
    ema = ema_series(closes, EMA_LEN)
    ema_now, ema_prev = ema[-1], ema[-6]
    if ema_now <= ema_prev:
        return None                  # leadership without a rising 20 EMA is a fade
    pull = list(range(len(bars) - 1 - pd, len(bars) - 1))
    if not all(closes[k] < closes[k - 1] for k in pull) or closes[-1] <= closes[-2]:
        return None                  # need pd falling closes, then today's bounce
    pull_low = min([float(bars[k]["l"]) for k in pull] + [float(bars[-1]["l"])])
    floor = ema_now * (1 - EMA_TOL_PCT / 100)
    if pull_low < floor or closes[-1] <= ema_now:
        return None
    a = atr(bars, 14)
    if not a or a <= 0:
        return None
    entry = closes[-1]
    stop = entry - float(p["atr_mult"]) * a
    risk = entry - stop
    t1, t2 = entry + 1.0 * risk, entry + 2.0 * risk
    depth_atr = (closes[len(bars) - 2 - pd] - pull_low) / a
    slope_pct = (ema_now / ema_prev - 1) * 100
    cur = bars[-1]
    rng = float(cur["h"]) - float(cur["l"])
    close_pos = (entry - float(cur["l"])) / rng if rng > 0 else 1.0
    held_pct = (pull_low / ema_now - 1) * 100
    comp = {
        "excess_return": 30 * _clamp((rs["excess_pct"] - float(p["rs_excess_pct"])) / 9.0),
        "shallow_pullback": 20 * _clamp(1.0 - (depth_atr - 1.0) / 2.0),
        "ema_slope": 15 * _clamp(slope_pct / 2.0),
        "bounce_bar": 15 * _clamp(close_pos),
        "ema_hold": 10 * (1.0 if held_pct >= 0 else _clamp(1.0 + held_pct / EMA_TOL_PCT)),
        "regime": {"trend_up": 10.0, "range": 5.0}.get(str(ctx.get("regime")), 0.0),
    }
    conf = round(sum(comp.values()), 1)
    reasons = [
        f"{n}-day return {rs['inst_ret_pct']:+.1f}% vs SPY {rs['spy_ret_pct']:+.1f}%: excess "
        f"{rs['excess_pct']:+.1f}% clears the {float(p['rs_excess_pct']):.1f}% leadership bar",
        f"{pd}-day pullback fell {depth_atr:.2f} ATR to {pull_low:.2f} and held the rising "
        f"20 EMA at {ema_now:.2f} ({slope_pct:+.2f}% over 5 sessions)",
        f"Bounce bar closed at {entry:.2f}, {close_pos * 100:.0f}% of its range, back above "
        f"the prior close {closes[-2]:.2f}",
        f"Stop {stop:.2f} is {p['atr_mult']} ATR ({a:.2f}) below entry; targets 1R "
        f"{t1:.2f} and 2R {t2:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=conf, reasons=reasons,
        invalidation=(f"A daily close below {floor:.2f} (20 EMA less {EMA_TOL_PCT}%) or the "
                      "excess return over SPY turning negative ends the leadership thesis."),
        expected_bars=8, trailing={"type": "atr", "mult": float(p["atr_mult"])},
        features={**rs, "ema20": ema_now, "ema20_slope_pct": slope_pct,
                  "pullback_low": pull_low, "pullback_depth_atr": depth_atr,
                  "held_pct_vs_ema": held_pct, "close_position": close_pos, "atr": a,
                  "conf_components": comp},
    )
