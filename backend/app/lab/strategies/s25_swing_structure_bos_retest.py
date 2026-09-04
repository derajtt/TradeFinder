"""Break of structure with retest.

In a downtrend each rally fails at a lower high because trapped longs sell
into strength; when pullbacks start printing higher lows that supply is being
absorbed, and the first close above the last lower high forces late shorts to
cover and sidelined buyers to chase. A retest that holds above the broken
level shows the sellers who defended it before are gone, which filters the
false breaks a plain breakout entry would take. Falsified if BOS-plus-retest
entries fail back below the broken level at the same rate as raw breakouts,
or if the higher-low requirement adds nothing to the win rate.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr, pivots

META = StrategyMeta(
    id="s25_swing_structure_bos_retest",
    name="Structure Break and Retest",
    family="structure",
    category="market_structure",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto", "index"],
    timeframes=["15min", "30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="swing_low",
    params={"pivot_len": 3, "min_hl": 2, "tol_atr": 0.25, "touch_atr": 0.35,
            "max_age": 30, "atr_len": 14},
    param_grid={"pivot_len": [2, 3, 4], "min_hl": [1, 2, 3],
                "tol_atr": [0.15, 0.25, 0.5]},
    regimes_on=None,
    max_hold_bars=40,
    version="1.0.0",
)

MAX_BARS = 300
Piv = List[Tuple[int, float]]


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _merge(pivs: Piv, pl: int, highs: bool) -> Piv:
    """Pivots within `pl` bars of each other are one swing (equal highs/lows
    on adjacent bars); keep the extreme, earliest on ties."""
    out: Piv = []
    for i, p in pivs:
        if out and i - out[-1][0] <= pl:
            if (p > out[-1][1]) if highs else (p < out[-1][1]):
                out[-1] = (i, p)
            continue
        out.append((i, p))
    return out


def _candidate(bars: List[dict], highs: Piv, lows: Piv, n: int, cur_atr: float,
               cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Test pivot-high pair (n-1, n) as lower-high -> higher-lows -> break ->
    retest holding -> confirming close on the current bar."""
    last = len(bars) - 1
    (ip, pp), (ih, ph) = highs[n - 1], highs[n]
    if ph >= pp:
        return None                                       # not a lower high
    ib = next((i for i in range(ih + 1, last + 1) if float(bars[i]["c"]) > ph), None)
    if ib is None or ib >= last or last - ib > int(_p(cfg, "max_age")):
        return None
    seq = [lw for lw in lows if lw[0] < ib]
    run = 1
    while run < len(seq) and seq[-run][1] > seq[-run - 1][1]:
        run += 1
    if run - 1 < int(_p(cfg, "min_hl")) or seq[-1][0] <= ih:   # need a HL after the LH
        return None
    touch = ph + float(_p(cfg, "touch_atr")) * cur_atr
    floor = ph - float(_p(cfg, "tol_atr")) * cur_atr
    region = bars[ib + 1:]
    min_low = min(float(b["l"]) for b in region)
    if min_low > touch or min_low < floor:                # no retest, or level failed
        return None
    c, o = float(bars[-1]["c"]), float(bars[-1]["o"])
    if c <= ph or c <= o:
        return None
    if len(region) > 1:                                   # only the first confirming bar fires
        pv = region[-2]
        prev_touch = min(float(b["l"]) for b in region[:-1]) <= touch
        if prev_touch and float(pv["c"]) > ph and float(pv["c"]) > float(pv["o"]):
            return None
    return {"pp": pp, "ih": ih, "ph": ph, "ib": ib, "last_hl": seq[-1][1],
            "n_hl": run - 1, "min_low": min_low}


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx["bars"][-MAX_BARS:]
    pl, atr_len = int(_p(cfg, "pivot_len")), int(_p(cfg, "atr_len"))
    last = len(bars) - 1
    if len(bars) < max(atr_len + 1, 6 * pl + 10):
        return None
    cur_atr = atr(bars, atr_len)
    if not cur_atr or cur_atr <= 0:
        return None
    highs, lows = pivots(bars, pl, pl)       # confirmed pivots only, never repaint
    highs, lows = _merge(highs, pl, True), _merge(lows, pl, False)
    if len(highs) < 2 or len(lows) < int(_p(cfg, "min_hl")) + 1:
        return None
    # Most recent lower-high structure whose break is being retested right now.
    setup = next((s for s in (_candidate(bars, highs, lows, n, cur_atr, cfg)
                              for n in range(len(highs) - 1, 0, -1)) if s), None)
    if setup is None:
        return None
    pp, ih, ph, ib = setup["pp"], setup["ih"], setup["ph"], setup["ib"]
    last_hl, n_hl, min_low = setup["last_hl"], setup["n_hl"], setup["min_low"]

    cur = bars[-1]
    h, l, c = float(cur["h"]), float(cur["l"]), float(cur["c"])
    entry = c
    stop = min(min_low, ph) - 0.1 * cur_atr
    risk = entry - stop
    leg = ph - last_hl
    if risk <= 0 or leg <= 0:
        return None
    target1 = pp if entry + 0.5 * risk <= pp <= entry + 3.0 * risk else entry + 1.5 * risk
    target2 = max(ph + leg, target1 + 1.0 * risk)
    break_atr = (float(bars[ib]["c"]) - ph) / cur_atr
    retest_dist = abs(min_low - ph) / cur_atr
    close_pos = (c - l) / (h - l) if h > l else 0.5

    hl_score = _clamp((n_hl - 1) / 2.0)
    break_score = _clamp(break_atr / 0.5)
    retest_score = _clamp(1.0 - retest_dist / max(float(_p(cfg, "touch_atr")),
                                                   float(_p(cfg, "tol_atr"))))
    confidence = round(30 + 20 * hl_score + 20 * break_score + 15 * retest_score
                       + 15 * close_pos, 1)
    reasons = [
        f"Lower high {ph:.2f} formed {last - ih} bars ago, below the prior pivot high {pp:.2f}",
        f"{n_hl} consecutive higher pivot lows preceded the break, the last at {last_hl:.2f}",
        f"Break of structure {last - ib} bars ago closed {break_atr:.2f} ATR above {ph:.2f}",
        f"Retest low {min_low:.2f} held within {retest_dist:.2f} ATR of the broken level",
        f"Confirmation close {c:.2f} is above {ph:.2f} at {close_pos * 100:.0f}% of its bar range",
        f"Stop {stop:.2f} sits below the retest low",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close below the retest low {min_low:.2f} puts price back under the "
                      f"broken level {ph:.2f} and voids the structure shift."),
        expected_bars=15,
        trailing={"type": "swing"},
        features={"lower_high": ph, "prior_high": pp, "last_higher_low": last_hl,
                  "n_higher_lows": n_hl, "break_bars_ago": last - ib, "break_atr": break_atr,
                  "retest_low": min_low, "retest_dist_atr": retest_dist,
                  "close_pos": close_pos, "atr": cur_atr, "hl_score": hl_score,
                  "break_score": break_score, "retest_score": retest_score},
    )
