"""OBV accumulation divergence.

Large buyers who want size without moving price work orders inside a tight
range, so on-balance volume climbs to new highs while price stays flat:
up-bars carry more volume than down-bars because the buyer lifts offers on
strength and lets price drift back on thin volume. When the range finally
breaks upward on volume the remaining float is already thin, so markup is
fast. Falsified if OBV-confirmed range breaks fail back inside the range at
the same rate as breaks without the OBV divergence.
"""
from typing import Any, Dict, List, Optional

from app.lab.base import Signal, StrategyMeta
from app.strategy.indicators import atr

META = StrategyMeta(
    id="s23_obv_accumulation_divergence",
    name="OBV Accumulation Divergence",
    family="volume",
    category="accumulation_breakout",
    hypothesis=__doc__.strip(),
    markets=["stocks", "etf", "crypto"],
    timeframes=["30min", "1hour", "4hour", "1day"],
    hold="swing",
    stop_method="structure",
    params={"obv_lookback": 20, "range_bars": 10, "band_pct": 2.0,
            "min_obv_rise": 1.0, "vol_mult": 1.0, "atr_len": 14},
    param_grid={"obv_lookback": [15, 20, 30], "range_bars": [8, 10, 15],
                "band_pct": [1.5, 2.0, 3.0]},
    regimes_on=["trend_up", "range", "low_vol"],
    max_hold_bars=30,
    version="1.0.0",
)

MAX_BARS = 300


def _p(cfg: Dict[str, Any], k: str) -> Any:
    return cfg.get(k, META.params[k])


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _obv(closes: List[float], vols: List[float]) -> List[float]:
    """Cumulative OBV from the first supplied bar. Only differences are used,
    so the arbitrary starting point never matters."""
    out = [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        out.append(out[-1] + (vols[i] if d > 0 else -vols[i] if d < 0 else 0.0))
    return out


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    bars = ctx["bars"][-MAX_BARS:]
    obv_lookback, range_bars = int(_p(cfg, "obv_lookback")), int(_p(cfg, "range_bars"))
    band_pct, min_obv_rise = float(_p(cfg, "band_pct")), float(_p(cfg, "min_obv_rise"))
    vol_mult, atr_len = float(_p(cfg, "vol_mult")), int(_p(cfg, "atr_len"))
    if len(bars) < max(obv_lookback, range_bars, atr_len) + range_bars + 3:
        return None

    closes = [float(b["c"]) for b in bars]
    vols = [float(b.get("v") or 0) for b in bars]
    window = bars[-1 - range_bars:-1]                 # the box, before this bar
    range_high = max(float(b["h"]) for b in window)
    range_low = min(float(b["l"]) for b in window)
    if range_low <= 0:
        return None
    box_pct = (range_high - range_low) / range_low * 100
    if box_pct > band_pct:
        return None

    obv = _obv(closes, vols)
    obv_end = obv[-2]                                  # OBV at the box's last bar
    if obv_end < max(obv[-1 - obv_lookback:-1]):       # not at a lookback high
        return None
    avg_vol = sum(vols[-1 - range_bars:-1]) / range_bars
    if avg_vol <= 0:
        return None
    obv_rise = (obv[-2] - obv[-2 - range_bars]) / avg_vol   # in bars of avg volume
    if obv_rise < min_obv_rise:
        return None

    cur = bars[-1]
    h, l, close = float(cur["h"]), float(cur["l"]), closes[-1]
    height = range_high - range_low
    vol_ratio = vols[-1] / avg_vol
    if close <= range_high or vol_ratio < vol_mult or close >= range_high + height:
        return None
    cur_atr = atr(bars, atr_len)
    if not cur_atr or cur_atr <= 0:
        return None
    close_pos = (close - l) / (h - l) if h > l else 0.5
    break_atr = (close - range_high) / cur_atr

    entry = close
    stop = range_low - 0.1 * cur_atr
    risk = entry - stop
    target1 = max(range_high + height, entry + 1.0 * risk)
    target2 = max(range_high + 2.5 * height, entry + 2.0 * risk)

    obv_score = _clamp(obv_rise / 3.0)
    vol_score = _clamp(vol_ratio - 1.0)
    tight_score = _clamp((band_pct - box_pct) / band_pct)
    confidence = round(30 + 25 * obv_score + 20 * vol_score + 15 * tight_score
                       + 10 * close_pos, 1)
    reasons = [
        f"OBV closed the box at a {obv_lookback}-bar high while price held a "
        f"{box_pct:.2f}% range for {range_bars} bars",
        f"Net OBV accumulation over the box equals {obv_rise:.1f} bars of average "
        f"volume ({avg_vol:,.0f})",
        f"Close {close:.2f} broke the box high {range_high:.2f} by {break_atr:.2f} ATR",
        f"Breakout volume {vols[-1]:,.0f} is {vol_ratio:.1f}x the box average",
        f"Stop {stop:.2f} sits below the box low {range_low:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=target1, target2=target2,
        confidence=confidence, reasons=reasons,
        invalidation=(f"A close back inside the box below {range_high:.2f} negates the "
                      f"break; a close below {range_low:.2f} means the accumulation read was wrong."),
        expected_bars=range_bars,
        trailing={"type": "swing"},
        features={"range_high": range_high, "range_low": range_low, "box_pct": box_pct,
                  "obv_rise_bars": obv_rise, "avg_vol": avg_vol, "vol_ratio": vol_ratio,
                  "break_atr": break_atr, "close_pos": close_pos, "atr": cur_atr,
                  "obv_score": obv_score, "vol_score": vol_score,
                  "tight_score": tight_score},
    )
