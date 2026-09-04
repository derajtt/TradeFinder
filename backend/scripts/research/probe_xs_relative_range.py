"""
probe_xs_relative_range.py — CROSS-SECTIONAL probe of trailing-20d range position,
crossed with whether the 20-day range is widening or narrowing.

Question
--------
On each date, rank every symbol by where its close sits inside its own trailing
20-day high/low range (rp = (C - LL20) / (HH20 - LL20), unit-free, 0..1).
Trade the spread between the top and bottom of that cross-sectional ranking.
Cross the ranking with volatility context: is the 20-day range WIDENING or
NARROWING relative to 5 sessions ago?

Focal cells (declared before looking at TEST):
    Q1 (bottom of own range) x NARROWING    vs    Q1 x WIDENING
Same location, opposite volatility context.

Discipline (round-one lessons)
------------------------------
  * Split by DATE: TRAIN < 2025-01-01, TEST >= 2025-01-01. Nothing tuned on TEST.
  * Standard errors CLUSTERED BY DATE (same-day obs across symbols are not iid).
  * Minimum cross-section of 20 symbols per date; dates surviving are reported.
  * Per bucket: n, mean, MEDIAN, sd, clustered SE, clustered t, distinct dates.
  * Monotonicity across quintiles matters more than any single extreme bucket.
  * Mandatory PRICE-TERCILE split (round one caught two spread artifacts that way).
  * Cost floor: long-only 0.25%, long/short spread 0.50%.
  * Drop the 5 most influential dates and re-report.
  * Overlapping h-day windows -> Newey-West on the date-level portfolio series,
    plus a non-overlapping (every h-th date) subsample.

Data hygiene discovered while surveying the cache (both matter a lot here):
  * The daily cache has an 8-MONTH HOLE: 2025-08-29 -> 2026-05-01. Returns and
    20-day windows are computed inside contiguous SEGMENTS only (calendar gap
    <= 5 days), never across the hole.
  * Cross-sectional breadth is 27 names before the hole and ~189 after it. The
    post-hole names are shallow and are in the cache because they were recent
    movers, so TEST is reported SPLIT into the clean deep continuation (2025)
    and the wide but selection-biased segment (2026).
  * Unadjusted split artifacts exist (e.g. SION 2026-08-07 c=51.04 -> 2026-08-10
    o=4.85 on 50x volume). Medians are reported everywhere and a winsorized /
    split-guarded robustness variant is run.

Read-only. Never calls the network.
"""

import json
import glob
import math
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}
MARKET_PROXY = "SPY"          # held out of the tradeable cross-section
ETFS = {"SPY", "QQQ", "IWM", "SMH", "XLE", "XLF", "BITO", "ETHA", "EWZ"}

RANGE_N = 20                  # trailing high/low window
WIDTH_LAG = 5                 # range width compared to this many bars ago
MIN_XS = 20                   # minimum symbols per date
HORIZONS = (1, 5, 10)
PRIMARY_H = 5                 # declared a priori
TRAIN_END = "2025-01-01"      # TRAIN is strictly before this
SEG_GAP_DAYS = 5              # calendar gap that starts a new segment
N_QUINT = 5
INFLUENTIAL_DROP = 5


# ----------------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------------

def days_between(d1: str, d2: str) -> int:
    from datetime import date
    a = date(int(d1[0:4]), int(d1[5:7]), int(d1[8:10]))
    b = date(int(d2[0:4]), int(d2[5:7]), int(d2[8:10]))
    return (b - a).days


def load_symbol(path: str) -> List[dict]:
    bars = sorted(json.load(open(path))["bars"].values(), key=lambda b: b["time"])
    return [b for b in bars if b.get("c") and b["c"] > 0 and b.get("h") and b.get("l")]


def segments(bars: List[dict], split_guard: bool) -> List[List[dict]]:
    """Contiguous runs. A calendar gap > SEG_GAP_DAYS breaks the run; with
    split_guard, so does a >60% one-day close-to-close move (unadjusted splits)."""
    out: List[List[dict]] = []
    cur: List[dict] = []
    for i, b in enumerate(bars):
        if cur:
            gap = days_between(cur[-1]["date"], b["date"])
            broke = gap > SEG_GAP_DAYS
            if split_guard and not broke:
                r = b["c"] / cur[-1]["c"] - 1.0
                if abs(r) > 0.60:
                    broke = True
            if broke:
                out.append(cur)
                cur = []
        cur.append(b)
    if cur:
        out.append(cur)
    return out


# ----------------------------------------------------------------------------
# panel construction
# ----------------------------------------------------------------------------

def build_panel(split_guard: bool = False) -> List[dict]:
    """One row per (symbol, date) with a valid signal. Forward returns attached
    for each horizon; None where the segment ends first."""
    rows: List[dict] = []
    need = RANGE_N + WIDTH_LAG          # bars of history before the first signal
    for path in sorted(glob.glob(os.path.join(CACHE, "*_1day.json"))):
        sym = os.path.basename(path).replace("_1day.json", "")
        if sym in CRYPTO or sym == MARKET_PROXY:
            continue
        bars = load_symbol(path)
        if len(bars) < need + 2:
            continue
        for seg in segments(bars, split_guard):
            if len(seg) < need + 2:
                continue
            highs = [b["h"] for b in seg]
            lows = [b["l"] for b in seg]
            closes = [b["c"] for b in seg]
            opens = [b["o"] for b in seg]
            n = len(seg)
            for i in range(need, n):
                hh = max(highs[i - RANGE_N + 1:i + 1])
                ll = min(lows[i - RANGE_N + 1:i + 1])
                if hh <= ll:
                    continue
                c = closes[i]
                rp = (c - ll) / (hh - ll)
                width_now = (hh - ll) / c
                j = i - WIDTH_LAG
                hh0 = max(highs[j - RANGE_N + 1:j + 1])
                ll0 = min(lows[j - RANGE_N + 1:j + 1])
                if hh0 <= ll0 or closes[j] <= 0:
                    continue
                width_prev = (hh0 - ll0) / closes[j]
                if width_prev <= 0:
                    continue
                wratio = width_now / width_prev
                row = {
                    "sym": sym,
                    "date": seg[i]["date"],
                    "price": c,
                    "dollar_vol": c * (seg[i].get("v") or 0.0),
                    "rp": rp,
                    "width": width_now,
                    "wratio": wratio,
                    "widening": wratio > 1.0,
                    "is_etf": sym in ETFS,
                }
                for h in HORIZONS:
                    row["fwd%d" % h] = (closes[i + h] / c - 1.0) if i + h < n else None
                    # tradeable version: enter next open, exit close of t+h
                    if i + h < n and opens[i + 1] > 0:
                        row["twd%d" % h] = closes[i + h] / opens[i + 1] - 1.0
                    else:
                        row["twd%d" % h] = None
                rows.append(row)
    return rows


def rank_buckets(vals: Sequence[float], k: int) -> List[int]:
    """Bucket index 0..k-1 by rank; ties broken by position (stable)."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0] * len(vals)
    n = len(vals)
    for pos, idx in enumerate(order):
        b = int(pos * k / n)
        out[idx] = min(b, k - 1)
    return out


def prepare_dates(rows: List[dict], h: int, ret_key: str = "fwd") -> Dict[str, List[dict]]:
    """Group by date, keep dates with >= MIN_XS valid obs, attach quintile,
    price tercile and date-demeaned return."""
    by_date: Dict[str, List[dict]] = defaultdict(list)
    key = "%s%d" % (ret_key, h)
    for r in rows:
        if r.get(key) is not None:
            by_date[r["date"]].append(r)
    kept: Dict[str, List[dict]] = {}
    for d, obs in by_date.items():
        if len(obs) < MIN_XS:
            continue
        q = rank_buckets([o["rp"] for o in obs], N_QUINT)
        t = rank_buckets([o["price"] for o in obs], 3)
        dec = rank_buckets([o["rp"] for o in obs], 10) if len(obs) >= 50 else [None] * len(obs)
        mu = statistics.fmean([o[key] for o in obs])
        out = []
        for i, o in enumerate(obs):
            o2 = dict(o)
            o2["q"] = q[i]
            o2["tier"] = t[i]
            o2["dec"] = dec[i]
            o2["r"] = o[key]
            o2["rd"] = o[key] - mu          # date-demeaned = market move netted out
            out.append(o2)
        kept[d] = out
    return kept


# ----------------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------------

def cluster_stats(obs: List[dict], field: str) -> dict:
    """Mean of `field` with standard errors clustered by date."""
    ys = [o[field] for o in obs]
    n = len(ys)
    if n < 2:
        return {"n": n, "mean": float("nan"), "median": float("nan"), "sd": float("nan"),
                "se": float("nan"), "t": float("nan"), "ndates": 0}
    mean = statistics.fmean(ys)
    med = statistics.median(ys)
    sd = statistics.pstdev(ys) if n < 2 else statistics.stdev(ys)
    by_d: Dict[str, float] = defaultdict(float)
    for o in obs:
        by_d[o["date"]] += (o[field] - mean)
    g = len(by_d)
    if g < 2:
        return {"n": n, "mean": mean, "median": med, "sd": sd,
                "se": float("nan"), "t": float("nan"), "ndates": g}
    meat = sum(v * v for v in by_d.values())
    var = (g / (g - 1.0)) * meat / (n * n)
    se = math.sqrt(var) if var > 0 else float("nan")
    t = mean / se if se and se == se and se > 0 else float("nan")
    return {"n": n, "mean": mean, "median": med, "sd": sd, "se": se, "t": t, "ndates": g}


def newey_west_t(x: Sequence[float], lag: int) -> Tuple[float, float]:
    """(t, se) for the mean of a serial-correlated series."""
    t_n = len(x)
    if t_n < 3:
        return float("nan"), float("nan")
    mu = statistics.fmean(x)
    dev = [v - mu for v in x]
    g0 = sum(d * d for d in dev) / t_n
    s = g0
    for k in range(1, min(lag, t_n - 1) + 1):
        gk = sum(dev[i] * dev[i - k] for i in range(k, t_n)) / t_n
        s += 2.0 * (1.0 - k / (lag + 1.0)) * gk
    if s <= 0:
        return float("nan"), float("nan")
    se = math.sqrt(s / t_n)
    return mu / se, se


def plain_t(x: Sequence[float]) -> Tuple[float, float]:
    if len(x) < 3:
        return float("nan"), float("nan")
    se = statistics.stdev(x) / math.sqrt(len(x))
    return (statistics.fmean(x) / se, se) if se > 0 else (float("nan"), float("nan"))


def spread_series(by_date: Dict[str, List[dict]], hi, lo,
                  field: str = "r") -> List[Tuple[str, float]]:
    """Per-date long/short return: mean(hi cell) - mean(lo cell).
    Only dates where BOTH cells are populated. This IS the clustered estimator."""
    out = []
    for d in sorted(by_date):
        obs = by_date[d]
        a = [o[field] for o in obs if hi(o)]
        b = [o[field] for o in obs if lo(o)]
        if not a or not b:
            continue
        out.append((d, statistics.fmean(a) - statistics.fmean(b)))
    return out


def report_series(name: str, ser: List[Tuple[str, float]], lag: int,
                  floor_pct: float, indent: str = "  ") -> dict:
    vals = [v for _, v in ser]
    if len(vals) < 3:
        print("%s%-46s  (too few dates: %d)" % (indent, name, len(vals)))
        return {}
    mean = statistics.fmean(vals)
    med = statistics.median(vals)
    t_p, se_p = plain_t(vals)
    t_nw, _ = newey_west_t(vals, lag)
    hit = sum(1 for v in vals if v > 0) / len(vals)
    clears = "YES" if mean * 100 > floor_pct else "no"
    print("%s%-46s mean %+7.3f%%  med %+7.3f%%  t=%6.2f  NW-t=%6.2f  dates=%4d  hit=%4.1f%%  >floor(%.2f%%): %s"
          % (indent, name, mean * 100, med * 100, t_p, t_nw, len(vals), hit * 100, floor_pct, clears))
    return {"mean": mean, "median": med, "t": t_p, "nw_t": t_nw, "n": len(vals), "hit": hit}


def bucket_table(title: str, by_date: Dict[str, List[dict]], field: str,
                 key: str = "q", k: int = N_QUINT) -> List[dict]:
    allobs = [o for d in by_date for o in by_date[d]]
    print("\n  %s   [field=%s]" % (title, field))
    print("    %-8s %7s %9s %9s %8s %9s %8s %7s" %
          ("bucket", "n", "mean%", "median%", "sd%", "clSE%", "cl-t", "dates"))
    rows = []
    for b in range(k):
        sub = [o for o in allobs if o[key] == b]
        st = cluster_stats(sub, field) if sub else None
        if not st or st["n"] < 2:
            print("    %-8s %7d  (empty)" % ("B%d" % (b + 1), len(sub)))
            rows.append(None)
            continue
        print("    %-8s %7d %+9.3f %+9.3f %8.3f %9.4f %+8.2f %7d" %
              ("B%d" % (b + 1), st["n"], st["mean"] * 100, st["median"] * 100,
               st["sd"] * 100, st["se"] * 100, st["t"], st["ndates"]))
        rows.append(st)
    good = [r["mean"] for r in rows if r]
    if len(good) == k:
        inc = all(good[i] < good[i + 1] for i in range(k - 1))
        dec = all(good[i] > good[i + 1] for i in range(k - 1))
        print("    monotone: %s" % ("YES (increasing)" if inc else
                                    "YES (decreasing)" if dec else "NO"))
    return rows


def is_monotone(rows: List[Optional[dict]]) -> bool:
    m = [r["mean"] for r in rows if r]
    if len(m) < 3:
        return False
    return (all(m[i] < m[i + 1] for i in range(len(m) - 1)) or
            all(m[i] > m[i + 1] for i in range(len(m) - 1)))


def drop_influential(ser: List[Tuple[str, float]], k: int) -> Tuple[List[Tuple[str, float]], List[str]]:
    if len(ser) <= k:
        return ser, []
    mu = statistics.fmean([v for _, v in ser])
    ranked = sorted(ser, key=lambda dv: abs(dv[1] - mu), reverse=True)
    drop = set(d for d, _ in ranked[:k])
    return [(d, v) for d, v in ser if d not in drop], [d for d, _ in ranked[:k]]


# ----------------------------------------------------------------------------
# era slicing
# ----------------------------------------------------------------------------

def slice_dates(by_date: Dict[str, List[dict]], lo: str, hi: str) -> Dict[str, List[dict]]:
    return {d: v for d, v in by_date.items() if lo <= d <= hi}


ERAS = [
    ("TRAIN  (2022-01-03..2024-12-31, deep 27-name cross-section)", "0000", "2024-12-31"),
    ("TEST-A (2025-01-02..2025-08-29, same deep names, clean OOS)", "2025-01-01", "2025-12-31"),
    ("TEST-B (2026-05-01..2026-09-03, wide ~189 names, mover-selected)", "2026-01-01", "9999"),
    ("TEST-ALL (both test segments pooled)", "2025-01-01", "9999"),
]


def era_slices(by_date: Dict[str, List[dict]]) -> List[Tuple[str, Dict[str, List[dict]]]]:
    return [(name, slice_dates(by_date, lo, hi)) for name, lo, hi in ERAS]


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def hr(c="=", n=104):
    print(c * n)


def coverage(panel: List[dict]) -> None:
    hr()
    print("PANEL COVERAGE")
    hr()
    dates = sorted(set(r["date"] for r in panel))
    syms = sorted(set(r["sym"] for r in panel))
    print("  signal rows: %d   symbols: %d   dates: %d   (%s .. %s)"
          % (len(panel), len(syms), len(dates), dates[0], dates[-1]))
    for name, lo, hi in ERAS[:3]:
        sub = [r for r in panel if lo <= r["date"] <= hi]
        dd = sorted(set(r["date"] for r in sub))
        ss = sorted(set(r["sym"] for r in sub))
        if dd:
            br = [sum(1 for r in sub if r["date"] == d) for d in dd]
            print("    %-64s rows=%6d syms=%4d dates=%4d breadth med=%d"
                  % (name.split("(")[0].strip(), len(sub), len(ss), len(dd), int(statistics.median(br))))


def run(panel: List[dict], label: str, h: int, ret_key: str = "fwd") -> None:
    by_date_all = prepare_dates(panel, h, ret_key)
    hr()
    print("%s   horizon=%dd   return=%s" %
          (label, h, "close_t -> close_t+h" if ret_key == "fwd" else "open_t+1 -> close_t+h"))
    hr()
    for name, sub in era_slices(by_date_all):
        if not sub:
            continue
        nobs = sum(len(v) for v in sub.values())
        print("\n" + "-" * 104)
        print("%s   dates surviving >=%d cross-section: %d   obs: %d"
              % (name, MIN_XS, len(sub), nobs))
        print("-" * 104)

        rows_raw = bucket_table("range-position quintile, RAW forward return (Q1=bottom of own 20d range)",
                                sub, "r")
        rows_dm = bucket_table("range-position quintile, DATE-DEMEANED return (market netted out)",
                               sub, "rd")

        ser = spread_series(sub, lambda o: o["q"] == N_QUINT - 1, lambda o: o["q"] == 0)
        print("\n  long/short date-level portfolio (cost floor 0.50%):")
        report_series("Q5(top of range) - Q1(bottom)", ser, h, 0.50)
        rev = [(d, -v) for d, v in ser]
        report_series("Q1(bottom) - Q5(top)  [reverse leg]", rev, h, 0.50)
        kept, dropped = drop_influential(ser, INFLUENTIAL_DROP)
        report_series("  ^ dropping %d most influential dates" % INFLUENTIAL_DROP, kept, h, 0.50)
        if dropped:
            print("      dropped: %s" % ", ".join(dropped))
        nonov = [ser[i] for i in range(0, len(ser), h)]
        report_series("  ^ non-overlapping (every %dth date)" % h, nonov, h, 0.50)

        # ---- the focal cross: bottom of range x volatility context ----
        print("\n  FOCAL CROSS: quintile x range-width context (widening = 20d range wider than 5d ago)")
        print("    %-26s %7s %9s %9s %9s %8s %7s" %
              ("cell", "n", "mean%", "median%", "clSE%", "cl-t", "dates"))
        allobs = [o for d in sub for o in sub[d]]
        for b in range(N_QUINT):
            for wide in (False, True):
                cell = [o for o in allobs if o["q"] == b and o["widening"] == wide]
                if len(cell) < 2:
                    continue
                st = cluster_stats(cell, "rd")
                tag = "Q%d x %s" % (b + 1, "WIDENING" if wide else "NARROWING")
                star = "  <<<" if b == 0 else ""
                print("    %-26s %7d %+9.3f %+9.3f %9.4f %+8.2f %7d%s" %
                      (tag, st["n"], st["mean"] * 100, st["median"] * 100,
                       st["se"] * 100, st["t"], st["ndates"], star))

        print("\n  focal long/short (same location, opposite vol context):")
        f1 = spread_series(sub,
                           lambda o: o["q"] == 0 and not o["widening"],
                           lambda o: o["q"] == 0 and o["widening"])
        report_series("Q1-NARROWING - Q1-WIDENING", f1, h, 0.50)
        k2, dr2 = drop_influential(f1, INFLUENTIAL_DROP)
        report_series("  ^ dropping %d most influential dates" % INFLUENTIAL_DROP, k2, h, 0.50)
        f2 = spread_series(sub,
                           lambda o: o["q"] == N_QUINT - 1 and not o["widening"],
                           lambda o: o["q"] == N_QUINT - 1 and o["widening"])
        report_series("Q5-NARROWING - Q5-WIDENING (contrast)", f2, h, 0.50)

        # long-only legs, cost floor 0.25%
        print("\n  long-only legs, RAW return (cost floor 0.25%):")
        for tag, pred in (("Q1 x NARROWING", lambda o: o["q"] == 0 and not o["widening"]),
                          ("Q1 x WIDENING", lambda o: o["q"] == 0 and o["widening"])):
            cell = [o for o in allobs if pred(o)]
            if len(cell) < 3:
                continue
            st = cluster_stats(cell, "r")
            print("    %-26s n=%6d mean %+7.3f%%  med %+7.3f%%  clSE %6.4f%%  cl-t %+6.2f  dates %4d  >0.25%%: %s"
                  % (tag, st["n"], st["mean"] * 100, st["median"] * 100, st["se"] * 100,
                     st["t"], st["ndates"], "YES" if st["mean"] * 100 > 0.25 else "no"))

        # ---- mandatory price-tier check ----
        print("\n  PRICE TERCILE CHECK (tercile assigned cross-sectionally each date):")
        print("    %-10s %8s %9s %11s %11s %11s %8s %7s" %
              ("tier", "medPx$", "n", "Q5-Q1 mean%", "Q5-Q1 med%", "Q1nar-Q1wid%", "t(Q5-Q1)", "dates"))
        for tier in range(3):
            tsub = {d: [o for o in v if o["tier"] == tier] for d, v in sub.items()}
            tsub = {d: v for d, v in tsub.items() if len(v) >= 5}
            if not tsub:
                continue
            pxs = [o["price"] for d in tsub for o in tsub[d]]
            nn = len(pxs)
            # re-rank rp inside the tier so the spread is within-tier
            tr: Dict[str, List[dict]] = {}
            for d, v in tsub.items():
                q = rank_buckets([o["rp"] for o in v], N_QUINT)
                tr[d] = [dict(o, q=q[i]) for i, o in enumerate(v)]
            s5 = spread_series(tr, lambda o: o["q"] == N_QUINT - 1, lambda o: o["q"] == 0)
            sf = spread_series(tr,
                               lambda o: o["q"] == 0 and not o["widening"],
                               lambda o: o["q"] == 0 and o["widening"])
            if len(s5) < 3:
                continue
            v5 = [v for _, v in s5]
            t5, _ = plain_t(v5)
            fmean = statistics.fmean([v for _, v in sf]) * 100 if len(sf) >= 3 else float("nan")
            name_t = ("cheap", "mid", "expensive")[tier]
            print("    %-10s %8.2f %9d %+11.3f %+11.3f %+11.3f %+8.2f %7d" %
                  (name_t, statistics.median(pxs), nn, statistics.fmean(v5) * 100,
                   statistics.median(v5) * 100, fmean, t5, len(s5)))

        # deciles where breadth allows
        wide = {d: v for d, v in sub.items() if len(v) >= 50 and v[0]["dec"] is not None}
        if len(wide) >= 20:
            bucket_table("DECILE view (dates with >=50 names only)", wide, "rd", key="dec", k=10)


def robustness(panel: List[dict]) -> None:
    hr()
    print("ROBUSTNESS — primary spec (h=%dd, Q5-Q1 date-level spread, demeaning irrelevant to a spread)" % PRIMARY_H)
    hr()
    print("  %-52s %10s %10s %8s %8s %7s" % ("variant / era", "mean%", "median%", "t", "NW-t", "dates"))

    def line(tag: str, rows: List[dict], era: Tuple[str, str], h: int,
             ret_key: str = "fwd", hi=None, lo=None, wins: Optional[float] = None):
        bd = prepare_dates(rows, h, ret_key)
        bd = slice_dates(bd, era[0], era[1])
        if wins is not None:
            bd = {d: [dict(o, r=max(-wins, min(wins, o["r"])),
                           rd=max(-wins, min(wins, o["rd"]))) for o in v]
                  for d, v in bd.items()}
        if not bd:
            print("  %-52s (no data)" % tag)
            return
        hi_f = hi or (lambda o: o["q"] == N_QUINT - 1)
        lo_f = lo or (lambda o: o["q"] == 0)
        ser = spread_series(bd, hi_f, lo_f)
        if len(ser) < 3:
            print("  %-52s (too few dates)" % tag)
            return
        vals = [v for _, v in ser]
        t, _ = plain_t(vals)
        nw, _ = newey_west_t(vals, h)
        print("  %-52s %+10.3f %+10.3f %+8.2f %+8.2f %7d"
              % (tag, statistics.fmean(vals) * 100, statistics.median(vals) * 100, t, nw, len(vals)))

    tr = ("0000", "2024-12-31")
    ta = ("2025-01-01", "2025-12-31")
    tb = ("2026-01-01", "9999")

    for h in HORIZONS:
        line("h=%dd close->close  | TRAIN" % h, panel, tr, h)
        line("h=%dd close->close  | TEST-A" % h, panel, ta, h)
        line("h=%dd close->close  | TEST-B" % h, panel, tb, h)
    print()
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd open(t+1)->close (tradeable) | %s" % (PRIMARY_H, nm), panel, era, PRIMARY_H, "twd")
    print()
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd winsorized +/-25%% | %s" % (PRIMARY_H, nm), panel, era, PRIMARY_H, "fwd", wins=0.25)
    print()
    guarded = build_panel(split_guard=True)
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd split-guarded panel | %s" % (PRIMARY_H, nm), guarded, era, PRIMARY_H)
    print("    (split guard removed %d of %d signal rows)" % (len(panel) - len(guarded), len(panel)))
    print()
    px5 = [r for r in panel if r["price"] >= 5.0]
    noetf = [r for r in panel if not r["is_etf"]]
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd price>=$5 only | %s" % (PRIMARY_H, nm), px5, era, PRIMARY_H)
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd ex-ETF only | %s" % (PRIMARY_H, nm), noetf, era, PRIMARY_H)
    print()
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd FOCAL Q1nar-Q1wid | %s" % (PRIMARY_H, nm), panel, era, PRIMARY_H,
             hi=lambda o: o["q"] == 0 and not o["widening"],
             lo=lambda o: o["q"] == 0 and o["widening"])
    print()
    # alternative widening definition: cross-sectional median split of wratio
    alt: List[dict] = []
    by_d: Dict[str, List[dict]] = defaultdict(list)
    for r in panel:
        by_d[r["date"]].append(r)
    for d, v in by_d.items():
        if len(v) < MIN_XS:
            alt.extend(v)
            continue
        med = statistics.median([o["wratio"] for o in v])
        alt.extend(dict(o, widening=(o["wratio"] > med)) for o in v)
    for era, nm in ((tr, "TRAIN"), (ta, "TEST-A"), (tb, "TEST-B")):
        line("h=%dd FOCAL, XS-median widening def | %s" % (PRIMARY_H, nm), alt, era, PRIMARY_H,
             hi=lambda o: o["q"] == 0 and not o["widening"],
             lo=lambda o: o["q"] == 0 and o["widening"])


def forensics(panel: List[dict]) -> None:
    """Chase the only two numbers in the report that look alive:
    (1) TEST-A focal Q1nar-Q1wid t=3.10, (2) TEST-B headline Q5-Q1 t=-3.83."""
    hr()
    print("FORENSICS")
    hr()
    bd = prepare_dates(panel, PRIMARY_H, "fwd")

    # --- naive (iid) vs clustered t, to show the round-one inflation factor ---
    tot_dates = len(bd)
    print("\n  [F0] distinct dates used at h=%dd after the >=%d cross-section filter: %d"
          % (PRIMARY_H, MIN_XS, tot_dates))
    for nm, lo, hi in ERAS[:3]:
        print("        %-12s %d" % (nm.split()[0], len(slice_dates(bd, lo, hi))))

    print("\n  [F1] naive-iid t vs date-clustered t, range-position quintiles (h=5d)")
    print("    %-14s %-6s %-9s %8s %9s %9s %9s %8s" %
          ("era", "bucket", "field", "n", "mean%", "naive-t", "clust-t", "inflate"))
    for nm, lo, hi in ERAS[:3]:
        sub = slice_dates(bd, lo, hi)
        if not sub:
            continue
        allobs = [o for d in sub for o in sub[d]]
        for field, flab in (("r", "RAW"), ("rd", "demeaned")):
            for b in (0, N_QUINT - 1):
                cell = [o for o in allobs if o["q"] == b]
                st = cluster_stats(cell, field)
                naive = st["mean"] / (st["sd"] / math.sqrt(st["n"]))
                print("    %-14s %-6s %-9s %8d %+9.3f %+9.2f %+9.2f %8.2fx" %
                      (nm.split()[0], "Q%d" % (b + 1), flab, st["n"], st["mean"] * 100,
                       naive, st["t"], abs(naive / st["t"]) if st["t"] else float("nan")))

    # --- reconcile pooled cell difference vs date-level spread (TEST-A focal) ---
    print("\n  [F2] TEST-A focal reconciliation (pooled cell means vs equal-weighted date spread)")
    sub = slice_dates(bd, "2025-01-01", "2025-12-31")
    allobs = [o for d in sub for o in sub[d]]
    nar = [o for o in allobs if o["q"] == 0 and not o["widening"]]
    wid = [o for o in allobs if o["q"] == 0 and o["widening"]]
    print("      pooled  Q1-narrowing mean %+7.3f%% (n=%d, %d dates)" %
          (statistics.fmean([o["rd"] for o in nar]) * 100, len(nar), len(set(o["date"] for o in nar))))
    print("      pooled  Q1-widening  mean %+7.3f%% (n=%d, %d dates)" %
          (statistics.fmean([o["rd"] for o in wid]) * 100, len(wid), len(set(o["date"] for o in wid))))
    print("      pooled difference                %+7.3f%%" %
          ((statistics.fmean([o["rd"] for o in nar]) - statistics.fmean([o["rd"] for o in wid])) * 100))
    both = set(o["date"] for o in nar) & set(o["date"] for o in wid)
    n2 = [o for o in nar if o["date"] in both]
    w2 = [o for o in wid if o["date"] in both]
    print("      pooled diff on the %d dates where BOTH cells exist  %+7.3f%%" %
          (len(both), (statistics.fmean([o["rd"] for o in n2]) -
                       statistics.fmean([o["rd"] for o in w2])) * 100))
    ser = spread_series(sub, lambda o: o["q"] == 0 and not o["widening"],
                        lambda o: o["q"] == 0 and o["widening"])
    print("      equal-weighted date-level spread (what was reported)  %+7.3f%%  over %d dates"
          % (statistics.fmean([v for _, v in ser]) * 100, len(ser)))
    npd_n = len(n2) / max(1, len(both))
    npd_w = len(w2) / max(1, len(both))
    print("      names per date in each leg: narrowing %.1f, widening %.1f  <- portfolio thinness"
          % (npd_n, npd_w))

    # --- how concentrated is TEST-A focal in time? ---
    print("\n  [F3] TEST-A focal Q1nar-Q1wid, concentration in time")
    vals = sorted(ser, key=lambda dv: abs(dv[1]), reverse=True)
    tot = sum(v for _, v in ser)
    for k in (1, 3, 5, 10):
        share = sum(v for _, v in vals[:k]) / tot if tot else float("nan")
        print("      top %2d dates by |spread| contribute %6.1f%% of the total sum" % (k, share * 100))
    bymon: Dict[str, List[float]] = defaultdict(list)
    for d, v in ser:
        bymon[d[:7]].append(v)
    print("      by month: " + "  ".join("%s %+0.2f%%(n=%d)" % (m, statistics.fmean(bymon[m]) * 100, len(bymon[m]))
                                         for m in sorted(bymon)))
    ex = [(d, v) for d, v in ser if not ("2025-03" <= d[:7] <= "2025-04")]
    report_series("TEST-A focal EXCLUDING Mar+Apr 2025", ex, PRIMARY_H, 0.50, indent="      ")

    # --- TEST-B headline: how much is cheap-name skew? ---
    print("\n  [F4] TEST-B headline Q5-Q1: cheap-name / skew decomposition")
    subb = slice_dates(bd, "2026-01-01", "9999")
    for lab, pred in (("all names", lambda o: True),
                      ("price >= $5", lambda o: o["price"] >= 5.0),
                      ("price >= $10", lambda o: o["price"] >= 10.0),
                      ("price >= $20", lambda o: o["price"] >= 20.0)):
        f = {d: [o for o in v if pred(o)] for d, v in subb.items()}
        f = {d: v for d, v in f.items() if len(v) >= MIN_XS}
        if len(f) < 5:
            print("      %-14s (too few dates after filter: %d)" % (lab, len(f)))
            continue
        rr: Dict[str, List[dict]] = {}
        for d, v in f.items():
            q = rank_buckets([o["rp"] for o in v], N_QUINT)
            rr[d] = [dict(o, q=q[i]) for i, o in enumerate(v)]
        s = spread_series(rr, lambda o: o["q"] == N_QUINT - 1, lambda o: o["q"] == 0)
        report_series("%-14s Q5-Q1" % lab, s, PRIMARY_H, 0.50, indent="      ")

    # --- sign stability of the focal across all three eras, side by side ---
    print("\n  [F5] focal Q1nar-Q1wid sign stability (the decision a trader would face)")
    for nm, lo, hi in ERAS[:3]:
        s = slice_dates(bd, lo, hi)
        if not s:
            continue
        ss = spread_series(s, lambda o: o["q"] == 0 and not o["widening"],
                           lambda o: o["q"] == 0 and o["widening"])
        if len(ss) < 3:
            continue
        v = [x for _, x in ss]
        t, _ = plain_t(v)
        nw, _ = newey_west_t(v, PRIMARY_H)
        print("      %-12s mean %+7.3f%%  median %+7.3f%%  clustered-t %+6.2f  NW-t %+6.2f  dates %4d"
              % (nm.split()[0], statistics.fmean(v) * 100, statistics.median(v) * 100, t, nw, len(v)))


def main() -> None:
    panel = build_panel(split_guard=False)
    coverage(panel)

    wid = sum(1 for r in panel if r["widening"])
    print("  width context split: widening %d (%.1f%%) / narrowing %d (%.1f%%)"
          % (wid, 100.0 * wid / len(panel), len(panel) - wid, 100.0 * (len(panel) - wid) / len(panel)))

    run(panel, "PRIMARY SPEC", PRIMARY_H, "fwd")
    robustness(panel)
    forensics(panel)
    hr()
    print("done.")


if __name__ == "__main__":
    main()
