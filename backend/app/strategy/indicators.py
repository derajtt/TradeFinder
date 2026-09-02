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


def rsi_series(closes: Sequence[float], n: int = 14) -> List[Optional[float]]:
    """Wilder RSI at every index. out[i] uses only closes[:i+1] — no lookahead."""
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def stdev(vals: Sequence[float]) -> Optional[float]:
    """Population standard deviation — matches TradingView's Bollinger basis."""
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def bollinger(closes: Sequence[float], n: int = 20,
              k: float = 3.0) -> Optional[dict]:
    """Bollinger bands on the final n closes. Population sigma, TV-compatible."""
    if len(closes) < n:
        return None
    win = list(closes[-n:])
    basis = sum(win) / n
    sd = stdev(win)
    if sd is None:
        return None
    upper, lower = basis + k * sd, basis - k * sd
    return {"basis": basis, "upper": upper, "lower": lower, "sd": sd,
            "width": (upper - lower) / basis if basis else None}


def bollinger_series(closes: Sequence[float], n: int = 20,
                     k: float = 3.0) -> List[Optional[dict]]:
    """Bollinger at every index using only closes[:i+1].

    Rolling sums rather than re-slicing the history at each bar: the naive form
    is O(N²) and takes minutes on a multi-year minute series. Values are
    identical to bollinger() on the same window.
    """
    out: List[Optional[dict]] = [None] * len(closes)
    if len(closes) < n:
        return out
    s = sum(closes[:n])
    s2 = sum(c * c for c in closes[:n])
    for i in range(n - 1, len(closes)):
        if i >= n:
            drop = closes[i - n]
            s += closes[i] - drop
            s2 += closes[i] * closes[i] - drop * drop
        basis = s / n
        var = max(0.0, s2 / n - basis * basis)   # clamp float noise at zero
        sd = var ** 0.5
        upper, lower = basis + k * sd, basis - k * sd
        out[i] = {"basis": basis, "upper": upper, "lower": lower, "sd": sd,
                  "width": (upper - lower) / basis if basis else None}
    return out


def true_range(bars: Sequence[dict], i: int) -> float:
    b = bars[i]
    if i == 0:
        return float(b["h"]) - float(b["l"])
    pc = float(bars[i - 1]["c"])
    return max(float(b["h"]) - float(b["l"]),
               abs(float(b["h"]) - pc), abs(float(b["l"]) - pc))


def atr_series(bars: Sequence[dict], n: int = 14) -> List[Optional[float]]:
    """Wilder ATR at every index — out[i] uses only bars[:i+1]."""
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) <= n:
        return out
    trs = [true_range(bars, i) for i in range(len(bars))]
    cur = sum(trs[1:n + 1]) / n
    out[n] = cur
    for i in range(n + 1, len(bars)):
        cur = (cur * (n - 1) + trs[i]) / n
        out[i] = cur
    return out


def adx_series(bars: Sequence[dict], n: int = 14) -> List[Optional[float]]:
    """Wilder ADX at every index. Needs ~2n bars to produce the first value."""
    out: List[Optional[float]] = [None] * len(bars)
    if len(bars) < 2 * n + 1:
        return out
    plus_dm, minus_dm, trs = [0.0], [0.0], [0.0]
    for i in range(1, len(bars)):
        up = float(bars[i]["h"]) - float(bars[i - 1]["h"])
        dn = float(bars[i - 1]["l"]) - float(bars[i]["l"])
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(true_range(bars, i))
    tr_s = sum(trs[1:n + 1])
    p_s, m_s = sum(plus_dm[1:n + 1]), sum(minus_dm[1:n + 1])
    dxs: List[float] = []
    for i in range(n + 1, len(bars)):
        tr_s = tr_s - tr_s / n + trs[i]
        p_s = p_s - p_s / n + plus_dm[i]
        m_s = m_s - m_s / n + minus_dm[i]
        if tr_s <= 0:
            continue
        pdi, mdi = 100 * p_s / tr_s, 100 * m_s / tr_s
        denom = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / denom if denom else 0.0)
        if len(dxs) == n:
            out[i] = sum(dxs) / n
        elif len(dxs) > n:
            out[i] = (out[i - 1] * (n - 1) + dxs[-1]) / n if out[i - 1] else None
    return out


def session_vwap(bars: Sequence[dict]) -> Optional[float]:
    """Volume-weighted average price across the supplied bars."""
    pv = vol = 0.0
    for b in bars:
        v = float(b.get("v") or 0)
        if v <= 0:
            continue
        typical = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3
        pv += typical * v
        vol += v
    return pv / vol if vol > 0 else None


def percentile_rank(vals: Sequence[float], x: float) -> Optional[float]:
    """Fraction of vals at or below x, 0-100."""
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return 100.0 * sum(1 for v in clean if v <= x) / len(clean)
