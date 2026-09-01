"""Pure indicator math for the model engines. Causal only — every function uses
completed bars; nothing repaints."""
import math
from typing import List, Optional, Sequence, Tuple


def sma(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def ema_series(vals: Sequence[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def rsi(closes: Sequence[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains += max(0, d)
        losses += max(0, -d)
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def macd_hist(closes: Sequence[float]) -> Optional[float]:
    if len(closes) < 35:
        return None
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(line, 9)
    return line[-1] - sig[-1]


def atr(bars: Sequence[dict], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def zscore(vals: Sequence[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    w = vals[-n:]
    m = sum(w) / n
    var = sum((x - m) ** 2 for x in w) / n
    sd = math.sqrt(var)
    return (vals[-1] - m) / sd if sd > 0 else None


def rolling_beta(y: Sequence[float], x: Sequence[float], n: int) -> Optional[float]:
    if len(y) < n + 1 or len(x) < n + 1:
        return None
    ry = [math.log(y[i] / y[i - 1]) for i in range(-n, 0)]
    rx = [math.log(x[i] / x[i - 1]) for i in range(-n, 0)]
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / n
    var = sum((a - mx) ** 2 for a in rx) / n
    return cov / var if var > 0 else None


def gaussian_filter(closes: Sequence[float], period: int = 20,
                    poles: int = 3) -> List[float]:
    """Causal N-pole Gaussian filter (Ehlers). Uses only completed bars."""
    if not closes:
        return []
    beta = (1 - math.cos(2 * math.pi / period)) / ((2 ** (1 / poles)) - 1)
    alpha = -beta + math.sqrt(beta * beta + 2 * beta)
    out = list(closes[:poles])
    a = alpha
    if poles == 1:
        for i in range(1, len(closes)):
            out_i = a * closes[i] + (1 - a) * out[i - 1]
            (out.append(out_i) if i >= len(out) else out.__setitem__(i, out_i))
        return out
    # cascade: apply single-pole filter `poles` times
    seq = list(closes)
    for _ in range(poles):
        f = [seq[0]]
        for i in range(1, len(seq)):
            f.append(a * seq[i] + (1 - a) * f[-1])
        seq = f
    return seq


def pivots(bars: Sequence[dict], left: int = 3, right: int = 3
           ) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """Confirmed swing highs/lows — a pivot exists only after `right` later bars,
    so it can never repaint."""
    highs, lows = [], []
    for i in range(left, len(bars) - right):
        h = bars[i]["h"]
        l = bars[i]["l"]
        if all(h >= bars[j]["h"] for j in range(i - left, i + right + 1)):
            highs.append((i, h))
        if all(l <= bars[j]["l"] for j in range(i - left, i + right + 1)):
            lows.append((i, l))
    return highs, lows


def resistance_zones(bars: Sequence[dict], tol_pct: float = 0.6,
                     min_touches: int = 2) -> List[dict]:
    hs, _ = pivots(bars)
    zones: List[dict] = []
    for _, price in hs:
        placed = False
        for z in zones:
            if abs(price - z["level"]) / z["level"] * 100 <= tol_pct:
                z["touches"] += 1
                z["level"] = (z["level"] * (z["touches"] - 1) + price) / z["touches"]
                placed = True
                break
        if not placed:
            zones.append({"level": price, "touches": 1})
    return [z for z in zones if z["touches"] >= min_touches]
