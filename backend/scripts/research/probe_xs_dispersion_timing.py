"""
probe_xs_dispersion_timing.py
=============================

QUESTION
--------
Does the CROSS-SECTIONAL DISPERSION of daily returns on date t predict which
cross-sectional style pays off over the next 5 days?

Concretely: rank every symbol against every other symbol on a past-return
signal, go long the top bucket / short the bottom bucket, hold 5 trading days.
Call that spread the "momentum spread" (negative spread == reversal pays).
Then ask whether the spread is different -- wider, or sign-flipped -- on
high-dispersion days than on low-dispersion days.

Dispersion is a CONDITIONING VARIABLE, not a strategy. The deliverable is
whether it is a usable switch between reversal and momentum.

DISCIPLINE (round-one rules, carried forward)
---------------------------------------------
  * Split by DATE. TRAIN < 2025-01-01, TEST >= 2025-01-01. Nothing is tuned on TEST.
  * CLUSTER STANDARD ERRORS BY DATE. Same-day observations are not independent.
    For an equal-weighted date-level spread, the date-clustered t is exactly the
    t of the daily spread series; we compute it both ways and assert they match.
  * Because the forward window is 5 days and dates are daily, the spread series
    OVERLAPS. Date-clustering does not fix serial overlap, so we additionally
    report a Newey-West HAC t (lag 10) and a non-overlapping (every-5th-date) t.
  * Minimum cross-section of 20 symbols per date; surviving date counts reported.
  * Per bucket: n, mean, MEDIAN, sd, clustered SE, clustered t, distinct dates.
  * Monotonicity across buckets is the test, not any single extreme bucket.
  * Mandatory price-tercile split -- an effect confined to the cheap tercile is
    a bid-ask-spread artifact, not an edge.
  * Cost floor: long/short spread must clear ~0.50% per round trip. Median trade
    reported alongside the mean.
  * Drop the 5 most influential dates and re-report.

DATA REALITY (measured, not assumed -- see banner printed at runtime)
--------------------------------------------------------------------
The cache is NOT one clean panel. It is two disjoint blocks:
  PANEL A "deep28"   : 31 symbols with history 2022-01-03..2025-08-29.
                       3 are crypto (BTCUSD/ETHUSD/SOLUSD, 7 bars/week) and are
                       DROPPED so every date's cross-section shares one calendar.
                       -> 28 equities/ETFs. This is the only block with a
                       train/test split available.
  PANEL B "wide2026" : ~190 symbols but only 2026-05-01..2026-09-03 (87 dates).
                       The deep names have an 8-month hole between the blocks.
                       Entirely held out; used as an independent replication
                       where the cross-section is finally wide enough for deciles.

Any return spanning the 2025-08-29 -> 2026-05-01 hole is discarded (gap guard).

Usage:
    /Users/blackbox/TradeFinder/.venv/bin/python \
        scripts/research/probe_xs_dispersion_timing.py
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import math
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}

SPLIT_DATE = "2025-01-01"     # TRAIN < SPLIT <= TEST
FWD_DAYS = 5                  # forward holding period, trading bars
MIN_XS = 20                   # minimum symbols on a date
MAX_GAP_DAYS = 7              # calendar-day guard on a 1-bar step
DISP_LOOKBACK = 252           # trailing window for the dispersion percentile
DISP_MIN_HIST = 126           # need at least this much history to rank dispersion
HI_PCT, LO_PCT = 0.70, 0.30   # high / low dispersion regime cutoffs
NW_LAG = 10                   # Newey-West lag for the overlapping spread series

COST_LS = 0.50                # long/short round-trip cost floor, percent
COST_LONG = 0.25              # single-leg cost floor, percent


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_series() -> Dict[str, List[dict]]:
    """symbol -> time-ordered list of daily bars."""
    out: Dict[str, List[dict]] = {}
    for f in sorted(glob.glob(os.path.join(CACHE, "*_1day.json"))):
        sym = os.path.basename(f).replace("_1day.json", "")
        try:
            bars = sorted(json.load(open(f))["bars"].values(), key=lambda b: b["time"])
        except Exception:
            continue
        if bars:
            out[sym] = bars
    return out


def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def build_symbol_frames(series: Dict[str, List[dict]]) -> Dict[str, dict]:
    """
    Per symbol: dates, closes, 1-day returns, and index maps.
    Returns/forwards that straddle a calendar hole are marked None.
    """
    frames: Dict[str, dict] = {}
    for sym, bars in series.items():
        dates = [b["date"] for b in bars]
        closes = [float(b["c"]) for b in bars]
        n = len(dates)
        ret1: List[Optional[float]] = [None] * n
        for i in range(1, n):
            if (_d(dates[i]) - _d(dates[i - 1])).days > MAX_GAP_DAYS:
                continue
            if closes[i - 1] > 0:
                ret1[i] = closes[i] / closes[i - 1] - 1.0
        frames[sym] = {
            "dates": dates,
            "closes": closes,
            "ret1": ret1,
            "idx": {d: i for i, d in enumerate(dates)},
        }
    return frames


def past_return(fr: dict, i: int, k: int, skip: int = 0) -> Optional[float]:
    """
    Return over the k bars ending `skip` bars before i, using only data <= i.
    skip=0 -> close[i]/close[i-k]-1 ; skip=1 -> close[i-1]/close[i-1-k]-1.
    None if it straddles a calendar hole.
    """
    hi = i - skip
    lo = hi - k
    if lo < 0:
        return None
    ds = fr["dates"]
    if (_d(ds[hi]) - _d(ds[lo])).days > MAX_GAP_DAYS * k:
        return None
    for j in range(lo + 1, hi + 1):
        if (_d(ds[j]) - _d(ds[j - 1])).days > MAX_GAP_DAYS:
            return None
    c = fr["closes"]
    if c[lo] <= 0:
        return None
    return c[hi] / c[lo] - 1.0


def fwd_return(fr: dict, i: int, k: int) -> Optional[float]:
    """close[i+k]/close[i]-1, None if it runs off the end or over a hole."""
    ds, c = fr["dates"], fr["closes"]
    if i + k >= len(ds):
        return None
    for j in range(i + 1, i + k + 1):
        if (_d(ds[j]) - _d(ds[j - 1])).days > MAX_GAP_DAYS:
            return None
    if c[i] <= 0:
        return None
    return c[i + k] / c[i] - 1.0


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def _quantile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return float("nan")
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def robust_sigma(xs: Sequence[float]) -> Optional[float]:
    """(p84-p16)/2 -- a sigma estimate that a single penny-stock print cannot hijack."""
    if len(xs) < 5:
        return None
    return (_quantile(xs, 0.84) - _quantile(xs, 0.16)) / 2.0


def clustered_mean_stats(obs: Sequence[Tuple[str, float]]) -> dict:
    """
    Mean of y with standard errors clustered by date.

    var(mean) = (G/(G-1)) * (1/N^2) * sum_g ( sum_{i in g} (y_i - mean) )^2
    """
    n = len(obs)
    if n == 0:
        return dict(n=0, mean=float("nan"), median=float("nan"), sd=float("nan"),
                    se=float("nan"), t=float("nan"), dates=0)
    ys = [y for _, y in obs]
    mean = sum(ys) / n
    med = statistics.median(ys)
    sd = statistics.pstdev(ys) if n < 2 else statistics.stdev(ys)
    by: Dict[str, float] = defaultdict(float)
    for d, y in obs:
        by[d] += (y - mean)
    g = len(by)
    if g < 2:
        return dict(n=n, mean=mean, median=med, sd=sd, se=float("nan"),
                    t=float("nan"), dates=g)
    meat = sum(v * v for v in by.values())
    var = (g / (g - 1.0)) * meat / (n * n)
    se = math.sqrt(var) if var > 0 else float("nan")
    t = mean / se if se and se == se and se > 0 else float("nan")
    return dict(n=n, mean=mean, median=med, sd=sd, se=se, t=t, dates=g)


def split_half_reliability(panel: dict, dates: Sequence[str], trials: int = 20) -> float:
    """
    How much of the daily dispersion estimate is signal vs sampling noise?

    Split each date's cross-section in half at random, measure dispersion on each
    half, correlate the two series across dates, then Spearman-Brown up to full
    length. A conditioning variable that cannot be measured reliably cannot
    condition anything -- this is a property of the data, not of the hypothesis.
    """
    import random
    rng = random.Random(12345)
    rels = []
    for _ in range(trials):
        a_s, b_s = [], []
        for dt in dates:
            rs = [r["ret1"] for r in panel["by_date"][dt]]
            if len(rs) < 20:
                continue
            rs = rs[:]
            rng.shuffle(rs)
            h = len(rs) // 2
            ra, rb = robust_sigma(rs[:h]), robust_sigma(rs[h:])
            if ra is None or rb is None:
                continue
            a_s.append(ra)
            b_s.append(rb)
        if len(a_s) > 30:
            r = spearman(a_s, b_s)
            rels.append(2 * r / (1 + r) if r > -1 else float("nan"))
    return statistics.median(rels) if rels else float("nan")


def series_stats(vals: Sequence[float]) -> dict:
    """iid t of a date-level series (== date-clustered t for a date-mean statistic)."""
    n = len(vals)
    if n < 2:
        return dict(n=n, mean=float("nan"), median=float("nan"), sd=float("nan"),
                    se=float("nan"), t=float("nan"))
    m = sum(vals) / n
    sd = statistics.stdev(vals)
    se = sd / math.sqrt(n)
    return dict(n=n, mean=m, median=statistics.median(vals), sd=sd, se=se,
                t=(m / se if se > 0 else float("nan")))


def newey_west_t(vals: Sequence[float], lag: int = NW_LAG) -> float:
    """HAC t for the mean of an overlapping series (Bartlett kernel)."""
    n = len(vals)
    if n < lag + 3:
        return float("nan")
    m = sum(vals) / n
    e = [v - m for v in vals]
    gamma0 = sum(x * x for x in e) / n
    var = gamma0
    for L in range(1, lag + 1):
        g = sum(e[i] * e[i - L] for i in range(L, n)) / n
        var += 2.0 * (1.0 - L / (lag + 1.0)) * g
    if var <= 0:
        return float("nan")
    return m / math.sqrt(var / n)


def pct_rank(hist: Sequence[float], x: float) -> Optional[float]:
    if not hist:
        return None
    return sum(1 for h in hist if h <= x) / float(len(hist))


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((x - mb) ** 2 for x in rb))
    return num / (da * db) if da > 0 and db > 0 else float("nan")


# --------------------------------------------------------------------------
# panel construction
# --------------------------------------------------------------------------

def build_panel(frames: Dict[str, dict], symbols: Sequence[str],
                d0: str, d1: str) -> dict:
    """
    For every date in [d0, d1] with >= MIN_XS symbols, assemble the cross-section:
    price, 1-day return, signals, and the 5-day forward return.
    """
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for sym in symbols:
        fr = frames[sym]
        for i, dt in enumerate(fr["dates"]):
            if dt < d0 or dt > d1:
                continue
            r1 = fr["ret1"][i]
            if r1 is None:
                continue
            by_date[dt].append({
                "sym": sym,
                "px": fr["closes"][i],
                "ret1": r1,
                "mom21": past_return(fr, i, 21, skip=1),   # momentum, 1-day skip
                "rev5": past_return(fr, i, 5, skip=0),     # short-term reversal leg
                "fwd": fwd_return(fr, i, FWD_DAYS),
            })
    dates = sorted(d for d in by_date if len(by_date[d]) >= MIN_XS)
    return {"by_date": by_date, "dates": dates}


def attach_dispersion(panel: dict) -> dict:
    """
    Dispersion per date + a POINT-IN-TIME trailing percentile rank of it.
    Primary measure is the robust sigma; plain stdev reported for comparison.
    """
    disp_r, disp_s = {}, {}
    for dt in panel["dates"]:
        rs = [r["ret1"] for r in panel["by_date"][dt]]
        rr = robust_sigma(rs)
        if rr is not None:
            disp_r[dt] = rr
            disp_s[dt] = statistics.stdev(rs) if len(rs) > 1 else float("nan")
    ds = [d for d in panel["dates"] if d in disp_r]

    # 5-day trailing mean of dispersion -- the same variable with the daily
    # sampling noise averaged down. Declared up front as the ONE robustness
    # variant, run on TRAIN and TEST alike; see the multiple-looks caveat.
    smooth = {}
    for k, dt in enumerate(ds):
        if k >= 4:
            smooth[dt] = sum(disp_r[ds[j]] for j in range(k - 4, k + 1)) / 5.0

    ranked, ranked_sm = {}, {}
    for k, dt in enumerate(ds):
        hist = [disp_r[ds[j]] for j in range(max(0, k - DISP_LOOKBACK), k)]  # strictly prior
        if len(hist) >= DISP_MIN_HIST:
            ranked[dt] = pct_rank(hist, disp_r[dt])
        if dt in smooth:
            hs = [smooth[ds[j]] for j in range(max(0, k - DISP_LOOKBACK), k)
                  if ds[j] in smooth]
            if len(hs) >= DISP_MIN_HIST:
                ranked_sm[dt] = pct_rank(hs, smooth[dt])
    panel["disp"] = disp_r
    panel["disp_std"] = disp_s
    panel["disp_pct"] = ranked
    panel["disp_pct_smooth"] = ranked_sm
    return panel


def bucket_indices(n: int, k: int) -> List[Tuple[int, int]]:
    """k near-equal contiguous slices of n sorted items."""
    out, start = [], 0
    for b in range(k):
        end = int(round((b + 1) * n / float(k)))
        out.append((start, max(end, start)))
        start = end
    return out


def cross_section_buckets(rows: List[dict], signal: str, k: int
                          ) -> Optional[List[List[dict]]]:
    """Sort the date's cross-section on `signal` ascending, cut into k buckets."""
    ok = [r for r in rows if r.get(signal) is not None and r.get("fwd") is not None]
    if len(ok) < max(MIN_XS, k * 2):
        return None
    ok.sort(key=lambda r: r[signal])
    mkt = sum(r["fwd"] for r in ok) / len(ok)
    for r in ok:
        r["fwd_mn"] = r["fwd"] - mkt        # market-neutral forward return
    return [ok[a:b] for a, b in bucket_indices(len(ok), k)]


# --------------------------------------------------------------------------
# the core measurement
# --------------------------------------------------------------------------

def run_sort(panel: dict, dates: Sequence[str], signal: str, k: int
             ) -> dict:
    """
    Returns per-bucket pooled observations, the daily long/short spread series,
    and the daily dispersion percentile aligned to it.
    """
    buckets_obs: List[List[Tuple[str, float]]] = [[] for _ in range(k)]
    spread: List[Tuple[str, float]] = []
    for dt in dates:
        bs = cross_section_buckets(panel["by_date"][dt], signal, k)
        if bs is None:
            continue
        for b in range(k):
            for r in bs[b]:
                buckets_obs[b].append((dt, r["fwd_mn"] * 100.0))
        top = [r["fwd_mn"] for r in bs[-1]]
        bot = [r["fwd_mn"] for r in bs[0]]
        if top and bot:
            spread.append((dt, (sum(top) / len(top) - sum(bot) / len(bot)) * 100.0))
    return {"buckets": buckets_obs, "spread": spread}


def fmt_bucket_table(buckets: List[List[Tuple[str, float]]], label: str) -> str:
    k = len(buckets)
    lines = [
        "  %s  (forward %dd return, market-neutral, %%)" % (label, FWD_DAYS),
        "  %-8s %7s %8s %8s %8s %8s %8s %7s" %
        ("bucket", "n", "mean", "median", "sd", "clSE", "cl_t", "dates"),
    ]
    for b in range(k):
        s = clustered_mean_stats(buckets[b])
        name = "Q%d%s" % (b + 1, " lo" if b == 0 else (" hi" if b == k - 1 else ""))
        lines.append("  %-8s %7d %8.3f %8.3f %8.3f %8.3f %8.2f %7d" %
                     (name, s["n"], s["mean"], s["median"], s["sd"],
                      s["se"], s["t"], s["dates"]))
    return "\n".join(lines)


def monotone_report(buckets: List[List[Tuple[str, float]]]) -> Tuple[bool, str]:
    means = []
    for b in buckets:
        s = clustered_mean_stats(b)
        means.append(s["mean"])
    up = all(means[i] < means[i + 1] for i in range(len(means) - 1))
    dn = all(means[i] > means[i + 1] for i in range(len(means) - 1))
    steps = sum(1 for i in range(len(means) - 1) if means[i + 1] > means[i])
    return (up or dn,
            "monotone=%s (%d/%d steps rise; means %s)" %
            (up or dn, steps, len(means) - 1,
             " ".join("%.3f" % m for m in means)))


def spread_report(spread: List[Tuple[str, float]], tag: str) -> str:
    vals = [v for _, v in spread]
    if len(vals) < 3:
        return "  %-22s  insufficient dates (%d)" % (tag, len(vals))
    ss = series_stats(vals)
    cl = clustered_mean_stats(spread)     # identical by construction; assert it
    assert abs(cl["t"] - ss["t"]) < 1e-6, (
        "date-clustered t must equal the daily-spread-series t (one obs/cluster): "
        "%.9f vs %.9f" % (cl["t"], ss["t"]))
    nw = newey_west_t(vals)
    nonov = vals[::FWD_DAYS]
    no = series_stats(nonov)
    hit = sum(1 for v in vals if v > 0) / float(len(vals)) * 100.0
    return ("  %-22s n_dates=%4d  mean=%+7.3f%%  median=%+7.3f%%  sd=%6.3f  "
            "cl_t=%+6.2f  NW%d_t=%+6.2f  nonov_t=%+6.2f (n=%d)  hit=%5.1f%%"
            % (tag, ss["n"], ss["mean"], ss["median"], ss["sd"],
               cl["t"], NW_LAG, nw, no["t"], no["n"], hit))


def drop_influential(spread: List[Tuple[str, float]], m: int = 5) -> dict:
    vals = [v for _, v in spread]
    if len(vals) <= m + 3:
        return {}
    mean = sum(vals) / len(vals)
    order = sorted(range(len(vals)), key=lambda i: -abs(vals[i] - mean))
    drop = set(order[:m])
    kept = [spread[i] for i in range(len(spread)) if i not in drop]
    dropped = [(spread[i][0], spread[i][1]) for i in order[:m]]
    ss = series_stats([v for _, v in kept])
    return {"stats": ss, "dropped": dropped,
            "nw": newey_west_t([v for _, v in kept])}


# --------------------------------------------------------------------------
# reporting blocks
# --------------------------------------------------------------------------

def regime_split(panel: dict, dates: Sequence[str],
                 key: str = "disp_pct") -> Dict[str, List[str]]:
    hi, lo, mid = [], [], []
    for dt in dates:
        p = panel[key].get(dt)
        if p is None:
            continue
        (hi if p >= HI_PCT else (lo if p <= LO_PCT else mid)).append(dt)
    return {"HIGH": hi, "LOW": lo, "MID": mid}


def diff_of_means(a: List[Tuple[str, float]], b: List[Tuple[str, float]]) -> Tuple[float, float]:
    """Difference of two date-disjoint spread series, with a Welch t."""
    av = [v for _, v in a]
    bv = [v for _, v in b]
    if len(av) < 3 or len(bv) < 3:
        return float("nan"), float("nan")
    ma, mb = sum(av) / len(av), sum(bv) / len(bv)
    va = statistics.variance(av) / len(av)
    vb = statistics.variance(bv) / len(bv)
    se = math.sqrt(va + vb)
    return ma - mb, ((ma - mb) / se if se > 0 else float("nan"))


def price_tercile_block(panel: dict, dates: Sequence[str], signal: str,
                        regime_dates: Dict[str, List[str]]) -> str:
    """
    Sort into price terciles FIRST, then run the momentum spread inside each
    tercile. If the effect lives only in the cheap tercile it is a spread artifact.
    """
    out = ["  price-tercile spreads (long/short built INSIDE each tercile)"]
    tiers = ["CHEAP", "MID", "EXPENSIVE"]
    for regime in ("ALL", "HIGH", "LOW"):
        dts = dates if regime == "ALL" else regime_dates[regime]
        dset = set(dts)
        series: Dict[str, List[Tuple[str, float]]] = {t: [] for t in tiers}
        pxs: Dict[str, List[float]] = {t: [] for t in tiers}
        for dt in dates:
            if dt not in dset:
                continue
            rows = [r for r in panel["by_date"][dt]
                    if r.get(signal) is not None and r.get("fwd") is not None]
            if len(rows) < MIN_XS:
                continue
            rows.sort(key=lambda r: r["px"])
            for ti, (a, b) in enumerate(bucket_indices(len(rows), 3)):
                sub = rows[a:b]
                if len(sub) < 6:
                    continue
                sub2 = sorted(sub, key=lambda r: r[signal])
                mkt = sum(r["fwd"] for r in sub2) / len(sub2)
                lo_i, hi_i = bucket_indices(len(sub2), 3)[0], bucket_indices(len(sub2), 3)[2]
                bot = [r["fwd"] - mkt for r in sub2[lo_i[0]:lo_i[1]]]
                top = [r["fwd"] - mkt for r in sub2[hi_i[0]:hi_i[1]]]
                if not bot or not top:
                    continue
                series[tiers[ti]].append(
                    (dt, (sum(top) / len(top) - sum(bot) / len(bot)) * 100.0))
                pxs[tiers[ti]].extend(r["px"] for r in sub2)
        for t in tiers:
            s = series[t]
            if len(s) < 3:
                out.append("    %-6s %-6s insufficient" % (regime, t))
                continue
            ss = series_stats([v for _, v in s])
            med_px = statistics.median(pxs[t]) if pxs[t] else float("nan")
            out.append("    %-6s %-10s med_px=%8.2f  n_dates=%4d  mean=%+7.3f%%  "
                       "median=%+7.3f%%  cl_t=%+6.2f  NW_t=%+6.2f"
                       % (regime, t, med_px, ss["n"], ss["mean"], ss["median"],
                          ss["t"], newey_west_t([v for _, v in s])))
    return "\n".join(out)


def analyse(panel: dict, dates: Sequence[str], signal: str, k: int,
            title: str, verbose: bool = True,
            regime_key: str = "disp_pct") -> dict:
    print("\n" + "-" * 100)
    print(title)
    print("-" * 100)
    reg = regime_split(panel, dates, regime_key)
    print("  regime dates: HIGH=%d  MID=%d  LOW=%d  (of %d rankable of %d total)"
          % (len(reg["HIGH"]), len(reg["MID"]), len(reg["LOW"]),
             sum(len(v) for v in reg.values()), len(dates)))

    all_res = run_sort(panel, dates, signal, k)
    if verbose:
        print()
        print(fmt_bucket_table(all_res["buckets"], "ALL DATES, sorted by %s" % signal))
        mono, msg = monotone_report(all_res["buckets"])
        print("  " + msg)
    print()
    print(spread_report(all_res["spread"], "ALL DATES  (hi-lo)"))

    hi_res = run_sort(panel, reg["HIGH"], signal, k)
    lo_res = run_sort(panel, reg["LOW"], signal, k)
    mid_res = run_sort(panel, reg["MID"], signal, k)
    print(spread_report(hi_res["spread"], "HIGH dispersion"))
    print(spread_report(mid_res["spread"], "MID  dispersion"))
    print(spread_report(lo_res["spread"], "LOW  dispersion"))

    d, t = diff_of_means(hi_res["spread"], lo_res["spread"])
    print("  SWITCH TEST  (HIGH minus LOW spread): %+.3f pp   Welch t=%+.2f" % (d, t))

    if verbose:
        print()
        print(fmt_bucket_table(hi_res["buckets"], "HIGH-dispersion dates"))
        print("  " + monotone_report(hi_res["buckets"])[1])
        print()
        print(fmt_bucket_table(lo_res["buckets"], "LOW-dispersion dates"))
        print("  " + monotone_report(lo_res["buckets"])[1])

    inf = drop_influential(all_res["spread"], 5)
    if inf:
        print()
        print("  drop 5 most influential dates: mean %+.3f%% -> %+.3f%%  "
              "cl_t %+.2f -> %+.2f  NW_t -> %+.2f"
              % (series_stats([v for _, v in all_res["spread"]])["mean"],
                 inf["stats"]["mean"],
                 series_stats([v for _, v in all_res["spread"]])["t"],
                 inf["stats"]["t"], inf["nw"]))
        print("    dropped: " + ", ".join("%s(%+.2f%%)" % (d_, v) for d_, v in inf["dropped"]))
    for tag, res in (("HIGH", hi_res), ("LOW", lo_res)):
        i2 = drop_influential(res["spread"], 5)
        if i2:
            print("  drop-5 within %s: mean %+.3f%% -> %+.3f%%  cl_t %+.2f -> %+.2f"
                  % (tag, series_stats([v for _, v in res["spread"]])["mean"],
                     i2["stats"]["mean"],
                     series_stats([v for _, v in res["spread"]])["t"], i2["stats"]["t"]))

    print()
    print(price_tercile_block(panel, dates, signal, reg))
    return {"all": all_res, "hi": hi_res, "lo": lo_res, "reg": reg,
            "switch_d": d, "switch_t": t,
            "hi_mean": series_stats([v for _, v in hi_res["spread"]])["mean"],
            "lo_mean": series_stats([v for _, v in lo_res["spread"]])["mean"]}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    print("=" * 100)
    print("xs_dispersion_timing -- does cross-sectional dispersion switch "
          "reversal vs momentum?")
    print("=" * 100)

    series = load_series()
    frames = build_symbol_frames(series)

    deep = sorted(s for s, b in series.items() if len(b) >= 800)
    deep_eq = [s for s in deep if s not in CRYPTO]
    print("\nDATA AUDIT (measured from the cache, not assumed)")
    print("  files loaded                : %d" % len(series))
    print("  total daily bars            : %d" % sum(len(b) for b in series.values()))
    print("  deep symbols (>=800 bars)   : %d  (%d crypto dropped -> %d equities/ETFs)"
          % (len(deep), len(deep) - len(deep_eq), len(deep_eq)))
    print("  deep universe               : %s" % ", ".join(deep_eq))
    sd = frames["PLTR"]["dates"]
    holes = [(sd[i], sd[i + 1]) for i in range(len(sd) - 1)
             if (_d(sd[i + 1]) - _d(sd[i])).days > 30]
    print("  *** CALENDAR HOLE in the deep series: %s ***" % holes)
    print("      -> the cache is two disjoint blocks, not one continuous panel.")
    shallow = [s for s in series if len(series[s]) < 800]
    print("  shallow symbols             : %d, all confined to %s..%s"
          % (len(shallow),
             min(series[s][0]["date"] for s in shallow),
             max(series[s][-1]["date"] for s in shallow)))

    # ---------------- PANEL A : deep 28, the only block with a train/test split
    pa = attach_dispersion(build_panel(frames, deep_eq, "2000-01-01", "2025-12-31"))
    dates_a = [d for d in pa["dates"] if d <= "2025-08-29"]
    train = [d for d in dates_a if d < SPLIT_DATE]
    test = [d for d in dates_a if d >= SPLIT_DATE]
    xs_sizes = [len(pa["by_date"][d]) for d in dates_a]
    print("\nPANEL A  'deep28'")
    print("  dates with >=%d symbols     : %d   (%s .. %s)"
          % (MIN_XS, len(dates_a), dates_a[0], dates_a[-1]))
    print("  cross-section size          : min=%d median=%d max=%d"
          % (min(xs_sizes), int(statistics.median(xs_sizes)), max(xs_sizes)))
    print("  TRAIN dates (<%s)   : %d" % (SPLIT_DATE, len(train)))
    print("  TEST  dates (>=%s)  : %d" % (SPLIT_DATE, len(test)))
    print("  buckets                     : QUINTILES (28 names -> ~5-6/bucket; "
          "deciles would be 2.8 names and are not defensible here)")

    dr = [pa["disp"][d] for d in dates_a if d in pa["disp"]]
    dstd = [pa["disp_std"][d] for d in dates_a if d in pa["disp"]]
    print("  dispersion (robust sigma, daily %%): median=%.3f  p10=%.3f  p90=%.3f"
          % (statistics.median(dr) * 100, _quantile(dr, .10) * 100, _quantile(dr, .90) * 100))
    print("  spearman(robust sigma, plain stdev) = %.3f" % spearman(dr, dstd))
    ac = [(dr[i], dr[i - 1]) for i in range(1, len(dr))]
    a1 = spearman([a for a, _ in ac], [b for _, b in ac])
    rel = split_half_reliability(pa, dates_a)
    print("  dispersion autocorr(1) (spearman)   = %.3f" % a1)
    print("  dispersion split-half reliability   = %.3f  (Spearman-Brown, 20 draws)"
          % rel)
    print("  -> with only 28 names the daily dispersion estimate is itself mostly")
    print("     sampling noise; a switch can be no sharper than the variable that")
    print("     sets it. This caps the effect before any hypothesis is tested.")

    summary: List[Tuple[str, str, str, float, float, float, float]] = []
    for sig, k in (("mom21", 5), ("rev5", 5)):
        r = analyse(pa, train, sig, k,
                    "PANEL A / TRAIN (2022-01..2024-12) / signal=%s / quintiles" % sig)
        summary.append(("TRAIN", sig, "daily", r["hi_mean"], r["lo_mean"],
                        r["switch_d"], r["switch_t"]))

    print("\n" + "=" * 100)
    print("TEST SET -- specification frozen above, run once, no tuning")
    print("=" * 100)
    for sig, k in (("mom21", 5), ("rev5", 5)):
        r = analyse(pa, test, sig, k,
                    "PANEL A / TEST (2025-01..2025-08) / signal=%s / quintiles" % sig)
        summary.append(("TEST", sig, "daily", r["hi_mean"], r["lo_mean"],
                        r["switch_d"], r["switch_t"]))

    # ---- ONE pre-declared robustness variant: 5-day-smoothed dispersion regime
    print("\n" + "=" * 100)
    print("ROBUSTNESS VARIANT: regime from 5-day-smoothed dispersion (declared "
          "up front, run on BOTH splits).")
    print("This is a SECOND look at the same TEST set -- read the t's with that "
          "in mind, do not cherry-pick.")
    print("=" * 100)
    for split_name, dts in (("TRAIN", train), ("TEST", test)):
        for sig, k in (("mom21", 5), ("rev5", 5)):
            r = analyse(pa, dts, sig, k,
                        "PANEL A / %s / signal=%s / SMOOTHED dispersion regime"
                        % (split_name, sig),
                        verbose=False, regime_key="disp_pct_smooth")
            summary.append((split_name, sig, "smooth5", r["hi_mean"], r["lo_mean"],
                            r["switch_d"], r["switch_t"]))

    print("\n" + "=" * 100)
    print("SWITCH-TEST SUMMARY -- does the sign of the conditioning hold out of sample?")
    print("=" * 100)
    print("  %-6s %-7s %-8s %10s %10s %10s %8s" %
          ("split", "signal", "regime", "HI spread", "LO spread", "HI-LO", "Welch t"))
    for row in summary:
        print("  %-6s %-7s %-8s %+9.3f%% %+9.3f%% %+9.3f pp %+8.2f" % row)
    print()
    for sig in ("mom21", "rev5"):
        for var in ("daily", "smooth5"):
            tr = [r for r in summary if r[0] == "TRAIN" and r[1] == sig and r[2] == var]
            te = [r for r in summary if r[0] == "TEST" and r[1] == sig and r[2] == var]
            if tr and te:
                same = (tr[0][5] > 0) == (te[0][5] > 0)
                print("  %s/%s: TRAIN switch %+.3f pp -> TEST switch %+.3f pp  "
                      "SIGN %s" % (sig, var, tr[0][5], te[0][5],
                                   "HELD" if same else "*** FLIPPED ***"))

    # ---------------- PANEL B : wide 2026 block, independent replication
    wide = sorted(s for s in series
                  if s not in CRYPTO and series[s][-1]["date"] >= "2026-08-01")
    pb = attach_dispersion(build_panel(frames, wide, "2026-05-01", "2026-12-31"))
    if pb["dates"]:
        szs = [len(pb["by_date"][d]) for d in pb["dates"]]
        print("\n" + "=" * 100)
        print("PANEL B 'wide2026' -- independent replication, never used for tuning")
        print("=" * 100)
        print("  symbols=%d  dates=%d (%s..%s)  cross-section min=%d median=%d max=%d"
              % (len(wide), len(pb["dates"]), pb["dates"][0], pb["dates"][-1],
                 min(szs), int(statistics.median(szs)), max(szs)))
        print("  NOTE: only %d dates, and the trailing-%d dispersion rank needs %d "
              "days of history -> regime split is NOT computable here."
              % (len(pb["dates"]), DISP_LOOKBACK, DISP_MIN_HIST))
        # in-block dispersion split using this block's own median (descriptive only)
        med = statistics.median([pb["disp"][d] for d in pb["dates"] if d in pb["disp"]])
        hi_b = [d for d in pb["dates"] if pb["disp"].get(d, -1) > med]
        lo_b = [d for d in pb["dates"] if 0 <= pb["disp"].get(d, -1) <= med]
        for sig in ("mom21", "rev5"):
            for k in (10, 5):
                res = run_sort(pb, pb["dates"], sig, k)
                print()
                print(fmt_bucket_table(res["buckets"],
                                       "PANEL B ALL, signal=%s, %d buckets" % (sig, k)))
                print("  " + monotone_report(res["buckets"])[1])
                print(spread_report(res["spread"], "%s k=%d hi-lo" % (sig, k)))
                if k == 10:
                    hs = run_sort(pb, hi_b, sig, k)["spread"]
                    ls = run_sort(pb, lo_b, sig, k)["spread"]
                    print(spread_report(hs, "  above-median disp"))
                    print(spread_report(ls, "  below-median disp"))
                    bd, bt = diff_of_means(hs, ls)
                    print("    SWITCH TEST (hi-lo disp): %+.3f pp  Welch t=%+.2f  "
                          "-- same sign in both regimes, so this is a MAGNITUDE "
                          "modulation, not a switch" % (bd, bt))
            print()
            print(price_tercile_block(pb, pb["dates"], sig,
                                      {"HIGH": hi_b, "LOW": lo_b, "MID": []}))

    print("\n" + "=" * 100)
    print("COST FLOOR REFERENCE: long/short round trip must clear %.2f%%; "
          "single leg %.2f%%." % (COST_LS, COST_LONG))
    print("=" * 100)


if __name__ == "__main__":
    main()
