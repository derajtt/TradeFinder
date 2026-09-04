"""probe_xs_reversal_vs_momentum.py

CROSS-SECTIONAL reversal-vs-momentum probe (round two, daily bars only).

Question
--------
On each date, rank every symbol against every other symbol by trailing return over
L in {1, 5, 21, 63} sessions. Measure forward returns over h in {1, 5, 21} days by
quintile. Where does the horizon flip from reversal (past winners underperform) to
momentum (past winners outperform)? Is that crossover stable out of sample, and does
it move with the market regime (SPY above/below its 50d SMA; SPY trailing-vol tercile)?

Design decisions locked BEFORE looking at TEST
----------------------------------------------
1. Split by DATE. TRAIN < 2025-01-01, TEST >= 2025-01-01. The headline cell is chosen
   on TRAIN by largest |clustered t| and then evaluated once on TEST.
2. Standard errors CLUSTERED BY DATE (CR0 with G/(G-1) finite-cluster correction).
   For overlapping forward windows (h=5, 21) we additionally report a Newey-West t on
   the date-level spread series with lag h-1, because date clustering fixes
   within-day correlation but NOT the overlap across days.
3. Minimum cross-section of 20 symbols with a valid signal AND a valid forward return.
4. Forward return is TRADEABLE and BOUNCE-FREE: signal uses closes through date t,
   entry at the OPEN of t+1, exit at the OPEN of t+1+h. The signal's terminal price
   (close of t) never appears in the forward return, so a noisy close cannot
   manufacture reversal. A close-to-close variant that DOES share close_t is computed
   as a diagnostic, to size the bid-ask-bounce contamination round one flagged.
5. Contiguity guard: every lookback and every holding window must be contiguous in
   calendar time. Five symbols (SPY, PLUG, SOFI, MARA, PLTR) have a real 245-day hole
   between 2025-08-29 and 2026-05-01; without this guard that hole becomes a fake
   +98% "1-day" return.
6. Universe excludes crypto (BTC/ETH/SOL trade weekends, so an "L-session" lookback
   spans a different amount of calendar time) and, in the primary cut, excludes ETFs
   (SPY/QQQ/IWM/SMH/XLE/XLF are baskets, not cross-sectional stock observations).
   An all-names robustness cut is reported.
7. Price-tier check is mandatory and is run WITHIN tier: on each date each price
   tercile is ranked on its own and the top-half-minus-bottom-half spread is taken,
   so the three tiers are constructed identically and are comparable.

Read-only. Never touches the network. Writes nothing.
"""

import json
import math
import os
import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"

TRAIN_END = "2025-01-01"          # TRAIN is strictly before this
PANEL_B_START = "2026-05-01"      # wide shallow panel begins here

LOOKBACKS = [1, 5, 21, 63]
HORIZONS = [1, 5, 21]
MIN_XS = 20                        # minimum symbols in a date's cross-section
N_BUCKETS = 5                      # quintiles (deep panel has only ~22 names/day)

CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}
ETFS = {"SPY", "QQQ", "IWM", "SMH", "XLE", "XLF"}

COST_LS = 0.50     # long/short spread must clear ~0.50% round trip
COST_LONG = 0.25   # long-only single leg must clear ~0.25%


# --------------------------------------------------------------------------
# small stats helpers (no numpy in this venv)
# --------------------------------------------------------------------------

def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def median(xs: Sequence[float]) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def clustered_mean_t(pairs: Sequence[Tuple[str, float]]) -> Tuple[float, float, float, int, int]:
    """Mean of y with CR0 standard errors clustered on the first tuple element.

    Returns (mean, clustered_se, t, n_obs, n_clusters).
    Var(beta) = (1/n^2) * sum_g (sum_{i in g} e_i)^2 * G/(G-1), e_i = y_i - beta.
    """
    n = len(pairs)
    if n < 2:
        return (float("nan"),) * 3 + (n, 0)
    beta = sum(v for _, v in pairs) / n
    by_g: Dict[str, float] = defaultdict(float)
    for g, v in pairs:
        by_g[g] += (v - beta)
    G = len(by_g)
    if G < 2:
        return beta, float("nan"), float("nan"), n, G
    meat = sum(s * s for s in by_g.values())
    var = meat / (n * n) * (G / (G - 1.0))
    se = math.sqrt(var) if var > 0 else float("nan")
    t = beta / se if se and se == se and se > 0 else float("nan")
    return beta, se, t, n, G


def newey_west_t(series: Sequence[float], lag: int) -> Tuple[float, float]:
    """Newey-West (Bartlett) SE and t for the mean of a time-ordered series."""
    T = len(series)
    if T < 3:
        return float("nan"), float("nan")
    xb = mean(series)
    d = [x - xb for x in series]
    q = max(0, min(lag, T - 2))
    s = sum(v * v for v in d) / T
    for j in range(1, q + 1):
        g = sum(d[t] * d[t - j] for t in range(j, T)) / T
        s += 2.0 * (1.0 - j / (q + 1.0)) * g
    if s <= 0:
        return float("nan"), float("nan")
    se = math.sqrt(s / T)
    return se, (xb / se if se > 0 else float("nan"))


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 3:
        return float("nan")

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = rank(a), rank(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else float("nan")


def is_monotone(vals: Sequence[float]) -> bool:
    up = all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1))
    dn = all(vals[i + 1] <= vals[i] for i in range(len(vals) - 1))
    return up or dn


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------

def dparse(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def span_ok(d0: str, d1: str, sessions: int) -> bool:
    """Calendar span of `sessions` trading days must look contiguous."""
    return (dparse(d1) - dparse(d0)).days <= (sessions * 7) // 5 + 7


class Series:
    __slots__ = ("sym", "dates", "o", "c", "v", "idx")

    def __init__(self, sym: str, bars: List[dict]):
        self.sym = sym
        self.dates = [b["date"] for b in bars]
        self.o = [float(b["o"]) for b in bars]
        self.c = [float(b["c"]) for b in bars]
        self.v = [float(b.get("v") or 0.0) for b in bars]
        self.idx = {d: i for i, d in enumerate(self.dates)}

    def trailing(self, i: int, L: int) -> Optional[float]:
        j = i - L
        if j < 0:
            return None
        if not span_ok(self.dates[j], self.dates[i], L):
            return None
        p = self.c[j]
        return (self.c[i] / p - 1.0) if p > 0 else None

    def fwd_open(self, i: int, h: int) -> Optional[float]:
        """Enter at open of i+1, exit at open of i+1+h. No shared price with signal."""
        a, b = i + 1, i + 1 + h
        if b >= len(self.dates):
            return None
        if not span_ok(self.dates[i], self.dates[a], 1):
            return None
        if not span_ok(self.dates[a], self.dates[b], h):
            return None
        p = self.o[a]
        return (self.o[b] / p - 1.0) if p > 0 else None

    def fwd_close(self, i: int, h: int) -> Optional[float]:
        """Diagnostic only: shares close_i with the signal -> bounce-contaminated."""
        b = i + h
        if b >= len(self.dates):
            return None
        if not span_ok(self.dates[i], self.dates[b], h):
            return None
        p = self.c[i]
        return (self.c[b] / p - 1.0) if p > 0 else None


def load_all() -> Dict[str, Series]:
    out = {}
    for fn in sorted(os.listdir(CACHE)):
        if not fn.endswith("_1day.json"):
            continue
        sym = fn[: -len("_1day.json")]
        with open(os.path.join(CACHE, fn)) as f:
            raw = json.load(f)
        bars = sorted((raw.get("bars") or {}).values(), key=lambda b: b["time"])
        if len(bars) < 70:
            continue
        out[sym] = Series(sym, bars)
    return out


# --------------------------------------------------------------------------
# observation panel
# --------------------------------------------------------------------------

class Obs:
    __slots__ = ("date", "sym", "sig", "fwd", "fwdcc", "price")

    def __init__(self, date, sym, sig, fwd, fwdcc, price):
        self.date, self.sym, self.sig = date, sym, sig
        self.fwd, self.fwdcc, self.price = fwd, fwdcc, price


def build(series: Dict[str, Series], syms: Sequence[str], L: int, h: int,
          lo: Optional[str] = None, hi: Optional[str] = None
          ) -> Dict[str, List[Obs]]:
    """date -> list of observations with a valid signal and a valid forward return."""
    by_date: Dict[str, List[Obs]] = defaultdict(list)
    for s in syms:
        ser = series[s]
        for i, d in enumerate(ser.dates):
            if lo and d < lo:
                continue
            if hi and d >= hi:
                continue
            sig = ser.trailing(i, L)
            if sig is None:
                continue
            fwd = ser.fwd_open(i, h)
            if fwd is None:
                continue
            by_date[d].append(Obs(d, s, sig, fwd, ser.fwd_close(i, h), ser.c[i]))
    return by_date


def bucketize(obs: List[Obs], k: int) -> List[List[Obs]]:
    """Sort ascending by signal; bucket 0 = worst trailing return (biggest loser)."""
    srt = sorted(obs, key=lambda o: o.sig)
    n = len(srt)
    out: List[List[Obs]] = [[] for _ in range(k)]
    for pos, o in enumerate(srt):
        out[min(k - 1, pos * k // n)].append(o)
    return out


class Result:
    def __init__(self):
        self.bucket_rows = []      # per bucket dicts
        self.spread_dates = []     # ordered dates
        self.spread_vals = []      # per-date top-minus-bottom
        self.n_dates = 0
        self.mean = float("nan")
        self.med = float("nan")
        self.sd = float("nan")
        self.se = float("nan")
        self.t = float("nan")
        self.nw_t = float("nan")
        self.mono = False
        self.rho = float("nan")


def analyse(by_date: Dict[str, List[Obs]], k: int, h: int,
            use_cc: bool = False, min_xs: int = MIN_XS) -> Result:
    R = Result()
    per_bucket_obs: List[List[Tuple[str, float]]] = [[] for _ in range(k)]
    per_bucket_exc: List[List[Tuple[str, float]]] = [[] for _ in range(k)]
    dates = sorted(d for d, v in by_date.items() if len(v) >= min_xs)
    for d in dates:
        obs = by_date[d]
        if use_cc:
            obs = [o for o in obs if o.fwdcc is not None]
            if len(obs) < min_xs:
                continue
        vals = [(o.fwdcc if use_cc else o.fwd) for o in obs]
        xs_mean = mean(vals)
        bs = bucketize(obs, k)
        for bi, b in enumerate(bs):
            for o in b:
                r = o.fwdcc if use_cc else o.fwd
                per_bucket_obs[bi].append((d, r))
                per_bucket_exc[bi].append((d, r - xs_mean))
        top = mean([(o.fwdcc if use_cc else o.fwd) for o in bs[-1]])
        bot = mean([(o.fwdcc if use_cc else o.fwd) for o in bs[0]])
        R.spread_dates.append(d)
        R.spread_vals.append(top - bot)

    for bi in range(k):
        raw = per_bucket_obs[bi]
        exc = per_bucket_exc[bi]
        if not raw:
            continue
        m, se, t, n, G = clustered_mean_t(exc)
        R.bucket_rows.append(dict(
            b=bi + 1, n=n, dates=G,
            raw_mean=mean([v for _, v in raw]) * 100,
            mean=m * 100, med=median([v for _, v in exc]) * 100,
            sd=stdev([v for _, v in exc]) * 100,
            se=se * 100 if se == se else float("nan"),
            t=t,
        ))

    R.n_dates = len(R.spread_vals)
    if R.n_dates >= 3:
        R.mean = mean(R.spread_vals) * 100
        R.med = median(R.spread_vals) * 100
        R.sd = stdev(R.spread_vals) * 100
        R.se = R.sd / math.sqrt(R.n_dates)
        R.t = R.mean / R.se if R.se else float("nan")
        _, R.nw_t = newey_west_t(R.spread_vals, max(0, h - 1))
    means = [r["mean"] for r in R.bucket_rows]
    if len(means) == k:
        R.mono = is_monotone(means)
        R.rho = spearman(list(range(1, k + 1)), means)
    return R


def nonoverlap(R: Result, h: int) -> Tuple[float, float, float, float, int, int, int]:
    """Assumption-free overlap check: keep only every h-th date so holding windows are disjoint.

    Runs all h phase offsets. Returns
    (mean of phase means %, median phase t, min t, max t, phases, n per phase, phases keeping sign).
    """
    if h <= 1:
        m = mean(R.spread_vals) * 100
        se = stdev(R.spread_vals) / math.sqrt(R.n_dates) * 100 if R.n_dates > 2 else float("nan")
        t = m / se if se and se == se else float("nan")
        return m, t, t, t, 1, R.n_dates, 1
    ms, ts = [], []
    full_sign = 1.0 if mean(R.spread_vals) >= 0 else -1.0
    keep_sign = 0
    npp = 0
    for p in range(h):
        v = R.spread_vals[p::h]
        if len(v) < 5:
            continue
        npp = max(npp, len(v))
        m = mean(v) * 100
        se = stdev(v) / math.sqrt(len(v)) * 100
        ms.append(m)
        ts.append(m / se if se else float("nan"))
        if (m >= 0) == (full_sign >= 0):
            keep_sign += 1
    if not ts:
        return (float("nan"),) * 4 + (0, 0, 0)
    return mean(ms), median(ts), min(ts), max(ts), len(ts), npp, keep_sign


def drop_influential(R: Result, h: int, n_drop: int = 5) -> Tuple[float, float, float, int, List[str]]:
    """Drop the n_drop dates whose spread deviates most from the mean; re-report."""
    if R.n_dates <= n_drop + 3:
        return (float("nan"),) * 3 + (0, [])
    m = mean(R.spread_vals)
    order = sorted(range(R.n_dates), key=lambda i: -abs(R.spread_vals[i] - m))
    drop = set(order[:n_drop])
    dropped = [R.spread_dates[i] for i in order[:n_drop]]
    keep = [R.spread_vals[i] for i in range(R.n_dates) if i not in drop]
    kd = [R.spread_dates[i] for i in range(R.n_dates) if i not in drop]
    mm = mean(keep) * 100
    se = stdev(keep) / math.sqrt(len(keep)) * 100
    _, nwt = newey_west_t(keep, max(0, h - 1))
    return mm, (mm / se if se else float("nan")), nwt, len(kd), dropped


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------

def hdr(s: str) -> None:
    print("\n" + "=" * 100)
    print(s)
    print("=" * 100)


def fmt_grid(title: str, grid: Dict[Tuple[int, int], Result], note: str = "") -> None:
    print("\n" + title)
    if note:
        print("  " + note)
    print("  %-4s %-3s %6s %6s %9s %9s %8s %8s %8s %8s %6s %6s"
          % ("L", "h", "dates", "n/day", "mean%", "med%", "sd%", "clSE%", "cl_t", "NW_t", "mono", "rho"))
    print("  " + "-" * 96)
    for L in LOOKBACKS:
        for h in HORIZONS:
            R = grid.get((L, h))
            if R is None or R.n_dates < 3:
                print("  %-4d %-3d %6s" % (L, h, "--"))
                continue
            npd = sum(r["n"] for r in R.bucket_rows) / max(1, R.bucket_rows[0]["dates"])
            print("  %-4d %-3d %6d %6.1f %9.4f %9.4f %8.3f %8.4f %8.2f %8.2f %6s %6.2f"
                  % (L, h, R.n_dates, npd, R.mean, R.med, R.sd, R.se, R.t, R.nw_t,
                     "YES" if R.mono else "no", R.rho))


def print_buckets(title: str, R: Result, k: int) -> None:
    print("\n  " + title)
    print("   %-4s %6s %6s %10s %10s %10s %8s %9s %7s"
          % ("Q", "n", "dates", "raw%", "excess%", "median%", "sd%", "clSE%", "cl_t"))
    print("   " + "-" * 82)
    for r in R.bucket_rows:
        print("   Q%-3d %6d %6d %10.4f %10.4f %10.4f %8.3f %9.4f %7.2f"
              % (r["b"], r["n"], r["dates"], r["raw_mean"], r["mean"], r["med"],
                 r["sd"], r["se"], r["t"]))
    print("   Q%d-Q1 spread: mean %.4f%%  median %.4f%%  clustered t %.2f  NW t %.2f  monotone %s (rho %.2f)"
          % (k, R.mean, R.med, R.t, R.nw_t, "YES" if R.mono else "NO", R.rho))


# --------------------------------------------------------------------------
# regime features from SPY
# --------------------------------------------------------------------------

def spy_regime(series: Dict[str, Series]) -> Tuple[Dict[str, bool], Dict[str, float]]:
    """date -> (SPY above its 50d SMA), date -> SPY trailing 21d annualised vol."""
    ser = series["SPY"]
    above: Dict[str, bool] = {}
    vol: Dict[str, float] = {}
    for i, d in enumerate(ser.dates):
        if i >= 50 and span_ok(ser.dates[i - 50], d, 50):
            sma = sum(ser.c[i - 49:i + 1]) / 50.0
            above[d] = ser.c[i] > sma
        if i >= 21 and span_ok(ser.dates[i - 21], d, 21):
            rs = []
            ok = True
            for j in range(i - 20, i + 1):
                if ser.c[j - 1] <= 0:
                    ok = False
                    break
                rs.append(math.log(ser.c[j] / ser.c[j - 1]))
            if ok and len(rs) > 2:
                vol[d] = stdev(rs) * math.sqrt(252) * 100
    return above, vol


def regime_split(R_dates: List[str], R_vals: List[float], keep: Sequence[str],
                 h: int) -> Tuple[float, float, float, int]:
    ks = set(keep)
    idx = [i for i, d in enumerate(R_dates) if d in ks]
    if len(idx) < 10:
        return (float("nan"),) * 3 + (len(idx),)
    v = [R_vals[i] for i in idx]
    m = mean(v) * 100
    se = stdev(v) / math.sqrt(len(v)) * 100
    _, nwt = newey_west_t(v, max(0, h - 1))
    return m, (m / se if se else float("nan")), nwt, len(v)


# --------------------------------------------------------------------------
# price tier check
# --------------------------------------------------------------------------

def price_tier_spreads(by_date: Dict[str, List[Obs]], h: int,
                       min_xs: int = MIN_XS) -> List[Tuple[str, float, float, float, int, float]]:
    """Within each price tercile, rank on the signal and take top-half minus bottom-half.

    Same construction in every tier, so the three tiers are directly comparable.
    Returns [(tier, mean%, t, NW t, n_dates, median%)].
    """
    tiers: List[List[float]] = [[], [], []]
    tdates: List[List[str]] = [[], [], []]
    prices: List[List[float]] = [[], [], []]
    for d in sorted(by_date):
        obs = by_date[d]
        if len(obs) < min_xs:
            continue
        srt = sorted(obs, key=lambda o: o.price)
        n = len(srt)
        for ti in range(3):
            grp = srt[ti * n // 3:(ti + 1) * n // 3]
            if len(grp) < 4:
                continue
            g = sorted(grp, key=lambda o: o.sig)
            half = len(g) // 2
            lo = mean([o.fwd for o in g[:half]])
            hi = mean([o.fwd for o in g[len(g) - half:]])
            tiers[ti].append(hi - lo)
            tdates[ti].append(d)
            prices[ti].extend([o.price for o in g])
    out = []
    for ti, name in enumerate(["CHEAP (low 1/3)", "MID   (mid 1/3)", "RICH  (top 1/3)"]):
        v = tiers[ti]
        if len(v) < 10:
            out.append((name, float("nan"), float("nan"), float("nan"), len(v), float("nan")))
            continue
        m = mean(v) * 100
        se = stdev(v) / math.sqrt(len(v)) * 100
        _, nwt = newey_west_t(v, max(0, h - 1))
        out.append((name + "  med_px $%.2f" % median(prices[ti]),
                    m, (m / se if se else float("nan")), nwt, len(v), median(v) * 100))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    series = load_all()

    deep = [s for s, ser in series.items() if len(ser.dates) >= 900]
    shallow = [s for s, ser in series.items() if len(ser.dates) < 900]
    stocks_A = sorted([s for s in deep if s not in CRYPTO and s not in ETFS])
    allA = sorted([s for s in deep if s not in CRYPTO])
    stocks_B = sorted([s for s in series if s not in CRYPTO and s not in ETFS])

    hdr("SECTION 0 — DATA INVENTORY")
    print("  files loaded (>=70 bars)      : %d" % len(series))
    print("  deep series (>=900 bars)      : %d  %s" % (len(deep), sorted(deep)))
    print("  shallow series (<900 bars)    : %d (2026-05-01 .. 2026-09-03 window)" % len(shallow))
    print("  crypto excluded (weekend cal) : %s" % sorted(CRYPTO))
    print("  ETFs excluded from primary    : %s" % sorted(ETFS))
    print("  PANEL A primary universe      : %d single stocks  %s" % (len(stocks_A), stocks_A))
    print("  PANEL A all-names robustness  : %d names (stocks + ETFs)" % len(allA))
    print("  PANEL B universe              : %d names, all dates >= %s" % (len(stocks_B), PANEL_B_START))
    print("  TRAIN = dates <  %s      TEST = dates >= %s" % (TRAIN_END, TRAIN_END))
    print("  buckets = %d (quintiles; Panel A has only ~%d names/day so deciles would be ~2/bucket)"
          % (N_BUCKETS, len(stocks_A)))
    print("  forward return = open(t+1) -> open(t+1+h); signal uses closes through t")

    # ---------------- TRAIN grid ----------------
    hdr("SECTION 1 — TRAIN (2022-01 .. 2024-12), PANEL A, Q5-Q1 QUINTILE SPREAD")
    train = {}
    train_bd = {}
    for L in LOOKBACKS:
        for h in HORIZONS:
            bd = build(series, stocks_A, L, h, hi=TRAIN_END)
            train_bd[(L, h)] = bd
            train[(L, h)] = analyse(bd, N_BUCKETS, h)
    fmt_grid("Q5(winners) - Q1(losers), long/short, % per holding period",
             train, "negative = REVERSAL (past winners lose), positive = MOMENTUM")

    print("\n  CROSSOVER MAP on TRAIN (sign of the spread):")
    print("  %-6s %s" % ("L\\h", "  ".join("%8d" % h for h in HORIZONS)))
    for L in LOOKBACKS:
        cells = []
        for h in HORIZONS:
            R = train[(L, h)]
            cells.append("%8s" % ("REV" if R.mean < 0 else "MOM"))
        print("  %-6d %s" % (L, "  ".join(cells)))

    # pick the headline cell on TRAIN only
    cand = [(abs(train[(L, h)].t), L, h) for L in LOOKBACKS for h in HORIZONS
            if train[(L, h)].t == train[(L, h)].t]
    cand.sort(reverse=True)
    best_t, BL, BH = cand[0]
    print("\n  Pre-registered headline cell = largest |clustered t| on TRAIN:  L=%d, h=%d  (|t|=%.2f)"
          % (BL, BH, best_t))
    print("  Multiple-testing bar: %d cells tested (4 L x 3 h). Bonferroni 5%% needs |t| > 2.87."
          % (len(LOOKBACKS) * len(HORIZONS)))

    hdr("SECTION 2 — TRAIN BUCKET DETAIL (monotonicity is the real test)")
    for L in LOOKBACKS:
        print_buckets("TRAIN  L=%d  h=%d   (excess%% = return minus that date's cross-sectional mean)"
                      % (L, BH), train[(L, BH)], N_BUCKETS)
    if BH != 1:
        for L in LOOKBACKS:
            print_buckets("TRAIN  L=%d  h=1" % L, train[(L, 1)], N_BUCKETS)

    # ---------------- TEST grid ----------------
    hdr("SECTION 3 — TEST-A (2025-01 .. 2025-08), PANEL A, SAME 22 STOCKS")
    testA = {}
    testA_bd = {}
    for L in LOOKBACKS:
        for h in HORIZONS:
            bd = build(series, stocks_A, L, h, lo=TRAIN_END)
            testA_bd[(L, h)] = bd
            testA[(L, h)] = analyse(bd, N_BUCKETS, h)
    fmt_grid("Q5-Q1 on TEST-A", testA, "same construction, never tuned on")

    print("\n  CROSSOVER MAP on TEST-A (sign of the spread):")
    print("  %-6s %s" % ("L\\h", "  ".join("%8d" % h for h in HORIZONS)))
    flips = 0
    for L in LOOKBACKS:
        cells = []
        for h in HORIZONS:
            a, b = train[(L, h)].mean, testA[(L, h)].mean
            same = (a < 0) == (b < 0)
            if not same:
                flips += 1
            cells.append("%8s" % (("REV" if b < 0 else "MOM") + ("" if same else "*")))
        print("  %-6d %s" % (L, "  ".join(cells)))
    print("  * = sign flipped vs TRAIN.  %d of %d cells flipped sign out of sample."
          % (flips, len(LOOKBACKS) * len(HORIZONS)))

    print_buckets("TEST-A headline cell  L=%d h=%d" % (BL, BH), testA[(BL, BH)], N_BUCKETS)

    # ---------------- Panel B ----------------
    hdr("SECTION 4 — PANEL B (2026-05-01 .. 2026-09-03), WIDE CROSS-SECTION, ALL TEST")
    print("  This panel has ~180 names/day but only ~87 dates, and the universe is a")
    print("  premarket-mover screener cache: names are present BECAUSE they moved.")
    print("  Treat it as a second, differently-biased out-of-sample check, not as truth.")
    testB = {}
    testB_bd = {}
    for L in LOOKBACKS:
        for h in HORIZONS:
            bd = build(series, stocks_B, L, h, lo=PANEL_B_START)
            testB_bd[(L, h)] = bd
            testB[(L, h)] = analyse(bd, N_BUCKETS, h)
    fmt_grid("Q5-Q1 on PANEL B (quintiles)", testB)

    print("\n  PANEL B with DECILES (breadth allows it): D10-D1")
    dec = {}
    for L in LOOKBACKS:
        for h in HORIZONS:
            dec[(L, h)] = analyse(testB_bd[(L, h)], 10, h)
    fmt_grid("D10-D1 on PANEL B", dec)
    for L in LOOKBACKS:
        print_buckets("PANEL B  L=%d  h=1  deciles" % L, dec[(L, 1)], 10)

    # ---------------- bounce diagnostic ----------------
    hdr("SECTION 5 — BID-ASK BOUNCE DIAGNOSTIC (round one's trap, re-run cross-sectionally)")
    print("  Same ranking, two forward returns:")
    print("    tradeable   : open(t+1) -> open(t+1+h)   [signal's close_t NOT in the return]")
    print("    close-close : close(t) -> close(t+h)     [signal's close_t IS in the return]")
    print("  If the reversal only exists in the close-to-close version it is microstructure, not edge.")
    print("\n  %-6s %-3s %-12s %10s %10s %8s %8s" % ("panel", "L", "fwd", "mean%", "med%", "cl_t", "NW_t"))
    print("  " + "-" * 66)
    for nm, bdmap in (("TRAIN", train_bd), ("TEST-A", testA_bd), ("PANEL B", testB_bd)):
        for L in LOOKBACKS:
            for cc in (False, True):
                R = analyse(bdmap[(L, 1)], N_BUCKETS, 1, use_cc=cc)
                print("  %-6s %-3d %-12s %10.4f %10.4f %8.2f %8.2f"
                      % (nm, L, "close-close" if cc else "open-open",
                         R.mean, R.med, R.t, R.nw_t))

    # ---------------- price tiers ----------------
    hdr("SECTION 6 — PRICE-TIER CHECK (mandatory): within-tier top-half minus bottom-half")
    for nm, bdmap in (("TRAIN", train_bd), ("TEST-A", testA_bd), ("PANEL B", testB_bd)):
        for L in LOOKBACKS:
            rows = price_tier_spreads(bdmap[(L, BH)], BH)
            print("\n  %s   L=%d  h=%d" % (nm, L, BH))
            print("   %-34s %10s %10s %8s %8s %7s" % ("tier", "mean%", "med%", "t", "NW_t", "dates"))
            for name, m, t, nwt, nd, md in rows:
                print("   %-34s %10.4f %10.4f %8.2f %8.2f %7d" % (name, m, md, t, nwt, nd))

    # ---------------- influential dates ----------------
    hdr("SECTION 7 — DROP THE 5 MOST INFLUENTIAL DATES")
    print("  %-8s %-4s %-4s %10s %8s %8s %10s %8s %8s"
          % ("panel", "L", "h", "mean%", "t", "NW_t", "drop5_m%", "drop5_t", "drop5NW"))
    print("  " + "-" * 82)
    for nm, gmap in (("TRAIN", train), ("TEST-A", testA), ("PANEL B", testB)):
        for L in LOOKBACKS:
            R = gmap[(L, BH)]
            mm, tt, nwt, nk, dd = drop_influential(R, BH)
            print("  %-8s %-4d %-4d %10.4f %8.2f %8.2f %10.4f %8.2f %8.2f"
                  % (nm, L, BH, R.mean, R.t, R.nw_t, mm, tt, nwt))
    R = train[(BL, BH)]
    _, _, _, _, dd = drop_influential(R, BH)
    print("\n  TRAIN headline cell L=%d h=%d dropped dates: %s" % (BL, BH, dd))

    # ---------------- overlap / independence ----------------
    hdr("SECTION 7b — HOW MANY INDEPENDENT OBSERVATIONS ARE THERE REALLY?")
    print("  With a %d-day holding period, consecutive daily observations share %d of %d days."
          % (BH, BH - 1, BH))
    print("  eff = dates/h is the count of genuinely non-overlapping holding periods.")
    print("  The non-overlap test keeps every h-th date (all h phase offsets) so windows are disjoint;")
    print("  it needs no Newey-West assumption at all.")
    print("\n  %-8s %-4s %-4s %7s %6s %9s %8s %8s %9s %9s %9s %8s"
          % ("panel", "L", "h", "dates", "eff", "mean%", "cl_t", "NW_t", "no_ov_m%", "med_t", "t_range", "sign_keep"))
    print("  " + "-" * 108)
    for nm, gmap in (("TRAIN", train), ("TEST-A", testA), ("PANEL B", testB)):
        for L in LOOKBACKS:
            for h in HORIZONS:
                R = gmap[(L, h)]
                if R.n_dates < 3:
                    continue
                m, tmed, tmin, tmax, ph, npp, ks = nonoverlap(R, h)
                print("  %-8s %-4d %-4d %7d %6.1f %9.4f %8.2f %8.2f %9.4f %9.2f %9s %5d/%d"
                      % (nm, L, h, R.n_dates, R.n_dates / float(h), R.mean, R.t, R.nw_t,
                         m, tmed, "%.1f/%.1f" % (tmin, tmax), ks, ph))

    # ---------------- regime ----------------
    hdr("SECTION 8 — DOES THE CROSSOVER MOVE WITH THE MARKET REGIME?")
    above, vol = spy_regime(series)
    tr_vols = sorted(v for d, v in vol.items() if d < TRAIN_END)
    if len(tr_vols) > 30:
        lo_cut = tr_vols[len(tr_vols) // 3]
        hi_cut = tr_vols[2 * len(tr_vols) // 3]
    else:
        lo_cut = hi_cut = float("nan")
    print("  SPY 21d annualised vol terciles, breakpoints FIXED on TRAIN: low<%.1f%%  mid  high>%.1f%%"
          % (lo_cut, hi_cut))
    print("  SPY trend = close > 50d SMA, computed causally, contiguity-guarded.")
    print("  SPY regime coverage: %d dates with trend, %d with vol" % (len(above), len(vol)))
    pb_dates = set(testB[(21, 1)].spread_dates)
    pb_tr = sum(1 for d in pb_dates if d in above)
    pb_vo = sum(1 for d in pb_dates if d in vol)
    print("  CAVEAT: SPY itself has the 245-day hole (2025-08-29 -> 2026-05-01), so on PANEL B only")
    print("          %d/%d dates carry a trend label and %d/%d a vol label. Panel B regime cells are"
          % (pb_tr, len(pb_dates), pb_vo, len(pb_dates)))
    print("          too thin to interpret and several are empty (nan).")

    def vol_tier(d):
        v = vol.get(d)
        if v is None:
            return None
        return "LOWVOL" if v < lo_cut else ("HIGHVOL" if v > hi_cut else "MIDVOL")

    cross_maps: Dict[str, Dict[Tuple[int, str], str]] = {}
    for nm, gmap in (("TRAIN", train), ("TEST-A", testA), ("PANEL B", testB)):
        print("\n  %s — Q5-Q1 spread by regime (mean%% / t / NW_t / dates)" % nm)
        print("   %-4s %-3s %-26s %-26s %-26s"
              % ("L", "h", "SPY>50d", "SPY<50d", "spread difference"))
        for L in LOOKBACKS:
            for h in HORIZONS:
                R = gmap[(L, h)]
                up = [d for d in R.spread_dates if above.get(d) is True]
                dn = [d for d in R.spread_dates if above.get(d) is False]
                a = regime_split(R.spread_dates, R.spread_vals, up, h)
                b = regime_split(R.spread_dates, R.spread_vals, dn, h)
                diff = a[0] - b[0] if (a[0] == a[0] and b[0] == b[0]) else float("nan")
                print("   %-4d %-3d %7.4f t%6.2f nw%6.2f n%4d %7.4f t%6.2f nw%6.2f n%4d   %+8.4f"
                      % (L, h, a[0], a[1], a[2], a[3], b[0], b[1], b[2], b[3], diff))

        print("\n   %s — by SPY vol tercile (mean%% / t / dates)" % nm)
        print("   %-4s %-3s %-22s %-22s %-22s" % ("L", "h", "LOWVOL", "MIDVOL", "HIGHVOL"))
        for L in LOOKBACKS:
            for h in HORIZONS:
                R = gmap[(L, h)]
                cells = []
                for tier in ("LOWVOL", "MIDVOL", "HIGHVOL"):
                    ks = [d for d in R.spread_dates if vol_tier(d) == tier]
                    m, t, nwt, n = regime_split(R.spread_dates, R.spread_vals, ks, h)
                    cells.append("%7.4f t%6.2f n%4d" % (m, t, n))
                print("   %-4d %-3d %s" % (L, h, "  ".join(cells)))

        print("\n   %s — CROSSOVER HORIZON by regime (first h where sign turns MOM, per L)" % nm)
        cmap: Dict[Tuple[int, str], str] = {}
        for L in LOOKBACKS:
            line = []
            for lab, sel in (("SPY>50d", lambda d: above.get(d) is True),
                             ("SPY<50d", lambda d: above.get(d) is False),
                             ("LOWVOL", lambda d: vol_tier(d) == "LOWVOL"),
                             ("HIGHVOL", lambda d: vol_tier(d) == "HIGHVOL")):
                cross = "none"
                for h in HORIZONS:
                    R = gmap[(L, h)]
                    ks = [d for d in R.spread_dates if sel(d)]
                    m, _, _, n = regime_split(R.spread_dates, R.spread_vals, ks, h)
                    if m == m and m > 0:
                        cross = "h=%d" % h
                        break
                cmap[(L, lab)] = cross
                line.append("%s:%s" % (lab, cross))
            print("     L=%-3d  %s" % (L, "   ".join(line)))
        cross_maps[nm] = cmap

    print("\n  IS THE REGIME-CONDITIONAL CROSSOVER STABLE? TRAIN vs TEST-A, cell by cell:")
    tr_c, te_c = cross_maps.get("TRAIN", {}), cross_maps.get("TEST-A", {})
    keys = sorted(set(tr_c) & set(te_c))
    agree = [k for k in keys if tr_c[k] == te_c[k]]
    print("   %-6s %-9s %-8s %-8s %s" % ("L", "regime", "TRAIN", "TEST-A", "agree?"))
    for k in keys:
        print("   L=%-4d %-9s %-8s %-8s %s"
              % (k[0], k[1], tr_c[k], te_c[k], "yes" if tr_c[k] == te_c[k] else "NO"))
    print("   AGREEMENT: %d of %d regime cells (%.0f%%). A coin flip is %d/%d."
          % (len(agree), len(keys), 100.0 * len(agree) / max(1, len(keys)), len(keys) // 2, len(keys)))

    # ---------------- robustness: all names incl ETFs ----------------
    hdr("SECTION 9 — ROBUSTNESS: universe including ETFs (28 names, Panel A)")
    rob = {}
    for L in LOOKBACKS:
        for h in HORIZONS:
            rob[(L, h)] = analyse(build(series, allA, L, h, hi=TRAIN_END), N_BUCKETS, h)
    fmt_grid("TRAIN, stocks + ETFs", rob)

    # ---------------- cost floor ----------------
    hdr("SECTION 10 — COST FLOOR VERDICT")
    print("  Long/short spread must clear %.2f%% per round trip; long-only leg %.2f%%."
          % (COST_LS, COST_LONG))
    print("\n  %-8s %-4s %-4s %10s %10s %10s %8s  %s"
          % ("panel", "L", "h", "mean%", "MEDIAN%", "floor%", "cl_t", "clears?"))
    print("  " + "-" * 88)
    for nm, gmap in (("TRAIN", train), ("TEST-A", testA), ("PANEL B", testB)):
        for L in LOOKBACKS:
            for h in HORIZONS:
                R = gmap[(L, h)]
                gross = abs(R.mean)
                gmed = abs(R.med)
                ok = "YES" if (gross > COST_LS and gmed > COST_LS) else "no"
                print("  %-8s %-4d %-4d %10.4f %10.4f %10.2f %8.2f  %s"
                      % (nm, L, h, R.mean, R.med, COST_LS, R.t, ok))

    # long-only legs on the headline cell
    print("\n  Long-only legs (excess vs cross-section) on the headline cell, TEST-A L=%d h=%d:" % (BL, BH))
    for r in testA[(BL, BH)].bucket_rows:
        print("    Q%d  excess mean %+.4f%%  median %+.4f%%  needs |%.2f%%|  -> %s"
              % (r["b"], r["mean"], r["med"], COST_LONG,
                 "clears" if abs(r["mean"]) > COST_LONG and abs(r["med"]) > COST_LONG else "no"))

    hdr("SECTION 11 — SUMMARY NUMBERS FOR THE HEADLINE CELL")
    a, b = train[(BL, BH)], testA[(BL, BH)]
    c = testB[(BL, BH)]
    print("  headline cell chosen on TRAIN: L=%d h=%d" % (BL, BH))
    for nm, R in (("TRAIN", a), ("TEST-A", b), ("PANEL B", c)):
        print("  %-8s dates %4d  mean %+.4f%%  median %+.4f%%  sd %.3f%%  clSE %.4f%%  cl_t %+.2f  NW_t %+.2f  mono %s"
              % (nm, R.n_dates, R.mean, R.med, R.sd, R.se, R.t, R.nw_t, R.mono))
    tot_dates = len(set(a.spread_dates) | set(b.spread_dates) | set(c.spread_dates))
    print("  distinct dates used across TRAIN+TEST-A+PANEL B: %d" % tot_dates)

    print("\n  Non-overlapping resample of the headline cell (disjoint %d-day windows, all phases):" % BH)
    for nm, R in (("TRAIN", a), ("TEST-A", b), ("PANEL B", c)):
        m, tmed, tmin, tmax, ph, npp, ks = nonoverlap(R, BH)
        if ph == 0 or tmed != tmed:
            print("  %-8s eff periods %5.1f   NOT COMPUTABLE - fewer than 5 disjoint %d-day windows per phase"
                  % (nm, R.n_dates / float(BH), BH))
            continue
        print("  %-8s eff periods %5.1f   mean %+.4f%%   median phase t %+.2f   t range %.2f..%.2f   sign kept %d/%d phases"
              % (nm, R.n_dates / float(BH), m, tmed, tmin, tmax, ks, ph))

    print("\n  Headline cell by CALENDAR YEAR (is the crossover stable, or a 2022 bear-market artifact?):")
    print("   %-6s %7s %10s %10s %8s" % ("year", "dates", "mean%", "med%", "t"))
    yr: Dict[str, List[float]] = defaultdict(list)
    for R in (a, b, c):
        for d, v in zip(R.spread_dates, R.spread_vals):
            yr[d[:4]].append(v)
    for y in sorted(yr):
        v = yr[y]
        if len(v) < 10:
            print("   %-6s %7d %10s" % (y, len(v), "(too few)"))
            continue
        mm2 = mean(v) * 100
        se2 = stdev(v) / math.sqrt(len(v)) * 100
        print("   %-6s %7d %10.4f %10.4f %8.2f"
              % (y, len(v), mm2, median(v) * 100, mm2 / se2 if se2 else float("nan")))
    print("   (these t's are NOT overlap-corrected - with h=%d each year holds ~%d independent periods)"
          % (BH, 252 // BH))

    print("\n  PRICE-TIER TABLE for the headline cell (within-tier top-half minus bottom-half, h=%d):" % BH)
    print("   %-8s %-24s %10s %10s %8s %8s %7s"
          % ("panel", "tier", "mean%", "med%", "t", "NW_t", "dates"))
    for nm, bdmap in (("TRAIN", train_bd), ("TEST-A", testA_bd), ("PANEL B", testB_bd)):
        for name, m, t, nwt, nd, md in price_tier_spreads(bdmap[(BL, BH)], BH):
            print("   %-8s %-24s %10.4f %10.4f %8.2f %8.2f %7d"
                  % (nm, name.split("  med_px")[0].strip(), m, md, t, nwt, nd))

    print("\n  SURVIVAL CHECKLIST for the headline cell L=%d h=%d:" % (BL, BH))
    checks = []
    checks.append(("holds on TEST-A with |cl_t|>2 and same sign as TRAIN",
                   (b.mean < 0) == (a.mean < 0) and abs(b.t) > 2))
    checks.append(("clears the 0.50% long/short cost floor on MEDIAN trade (TEST-A)",
                   abs(b.med) > COST_LS))
    checks.append(("monotone across quintiles on TEST-A", b.mono))
    # computed, not asserted: cheap tercile must not be the only tier carrying the sign
    tiers_tr = price_tier_spreads(train_bd[(BL, BH)], BH)
    sgn = -1.0 if a.mean < 0 else 1.0
    same_sign_non_cheap = [nm2 for nm2, m2, t2, _, _, _ in tiers_tr[1:]
                           if m2 == m2 and (m2 < 0) == (sgn < 0)]
    print("    (TRAIN tiers carrying the TRAIN sign, excluding the cheap tercile: %s)"
          % (same_sign_non_cheap or "NONE"))
    checks.append(("not confined to the cheapest price tercile (TRAIN)",
                   len(same_sign_non_cheap) > 0))
    mm, tt, nwt, nk, dd = drop_influential(b, BH)
    checks.append(("survives dropping the 5 most influential dates on TEST-A (|t|>2)",
                   abs(tt) > 2 if tt == tt else False))
    _, tmed_b, _, _, _, _, _ = nonoverlap(b, BH)
    checks.append(("survives the non-overlapping resample on TEST-A (median phase |t|>2)",
                   abs(tmed_b) > 2 if tmed_b == tmed_b else False))
    checks.append(("survives Newey-West on TEST-A (|NW t|>2)", abs(b.nw_t) > 2 if b.nw_t == b.nw_t else False))
    for lab, ok in checks:
        print("    [%s] %s" % ("PASS" if ok else "FAIL", lab))
    print("\n  OVERALL: %s" % ("SURVIVES" if all(o for _, o in checks) else "DOES NOT SURVIVE"))


if __name__ == "__main__":
    main()
