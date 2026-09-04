#!/usr/bin/env python
"""
probe_range_position.py -- does close-location-value (CLV) within the daily range
predict the NEXT session, and does it depend on range width relative to ATR?

Signal (measured on day t, daily bars):
    CLV      = (close - low) / (high - low)            in [0, 1]
    RR       = (high - low) / ATR14(through t-1)       range vs its own recent normal

Outcomes (day t+1, two separate trades):
    GAP      = (open_{t+1}  - close_t)   / close_t     * 100   -- hold overnight only
    O2C      = (close_{t+1} - open_{t+1})/ open_{t+1}  * 100   -- buy the open, sell the close
    C2C      = close_t -> close_{t+1}                  * 100   -- reference only

Splits by DATE of the signal day t:
    TRAIN   t <  2025-01-01   (long-history universe)
    TEST-A  t >= 2025-01-01   (SAME symbols -- clean out-of-sample)
    TEST-B  t >= 2025-01-01   (movers universe, 2026 files -- different universe AND few dates)

Nothing is tuned on TEST. The only data-derived parameters (the RR tercile cuts)
come from TRAIN and are then applied verbatim to TEST.

Run:  /Users/blackbox/TradeFinder/.venv/bin/python scripts/research/probe_range_position.py
      (from /Users/blackbox/TradeFinder/backend)
"""
import json
import glob
import os
import sys
import math
import datetime
import statistics
from collections import defaultdict
from typing import Optional, List, Dict, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from app.strategy.indicators import atr_series  # noqa: E402

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT_DATE = "2025-01-01"
CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}
MIN_PRICE = 1.00          # sub-dollar tape is a different (untradeable) animal
MAX_CAL_GAP_DAYS = 5      # t -> t+1 must be calendar-contiguous (kills the 2025-08 -> 2026-05 hole)
ATR_N = 14
COST_PCT = 0.25           # slippage + spread proxy, one leg, one round trip

CLV_EDGES = [0.0, 0.20, 0.40, 0.60, 0.80, 1.0001]
CLV_NAMES = ["CLV 0.0-0.2", "CLV 0.2-0.4", "CLV 0.4-0.6", "CLV 0.6-0.8", "CLV 0.8-1.0"]


# ----------------------------------------------------------------------------- load
def load_observations():
    """Return list of dicts, one per (symbol, signal-day t) with next-day outcomes."""
    obs = []
    for path in sorted(glob.glob(os.path.join(CACHE, "*_1day.json"))):
        sym = os.path.basename(path).split("_")[0]
        if sym in CRYPTO:
            continue
        bars = sorted(json.load(open(path))["bars"].values(), key=lambda b: b["time"])
        if len(bars) < ATR_N + 5:
            continue
        atrs = atr_series(bars, ATR_N)          # atrs[i] uses bars[:i+1]
        first = bars[0]["date"]
        universe = "long" if first < "2023-01-01" else "movers"
        for i in range(len(bars) - 1):
            b, nb = bars[i], bars[i + 1]
            prev_atr = atrs[i - 1] if i >= 1 else None      # strictly before day t
            if prev_atr is None or prev_atr <= 0:
                continue
            hi, lo, cl = b["h"], b["l"], b["c"]
            if hi <= lo or cl < MIN_PRICE or nb["o"] <= 0:
                continue
            d0 = datetime.date.fromisoformat(b["date"])
            d1 = datetime.date.fromisoformat(nb["date"])
            if not (0 < (d1 - d0).days <= MAX_CAL_GAP_DAYS):
                continue
            obs.append({
                "sym": sym,
                "date": b["date"],
                "universe": universe,
                "clv": (cl - lo) / (hi - lo),
                "rr": (hi - lo) / prev_atr,
                "gap": (nb["o"] - cl) / cl * 100.0,
                "o2c": (nb["c"] - nb["o"]) / nb["o"] * 100.0,
                "c2c": (nb["c"] - cl) / cl * 100.0,
            })
    return obs


# ----------------------------------------------------------------------------- stats
def _trimmed_mean(vals: Sequence[float], frac: float = 0.05) -> Optional[float]:
    v = sorted(vals)
    k = int(len(v) * frac)
    v = v[k:len(v) - k] if len(v) - 2 * k >= 5 else []
    return statistics.mean(v) if v else None


def describe(rows: List[dict], key: str) -> dict:
    """n, mean, median, sd, naive se, distinct dates, date-clustered se, robustness."""
    vals = [r[key] for r in rows]
    n = len(vals)
    out = {"n": n}
    if n < 2:
        return out
    out["mean"] = statistics.mean(vals)
    out["median"] = statistics.median(vals)
    out["sd"] = statistics.pstdev(vals) if n < 2 else statistics.stdev(vals)
    out["se"] = out["sd"] / math.sqrt(n)
    out["t"] = out["mean"] / out["se"] if out["se"] else 0.0

    by_date: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r[key])
    dmeans = {d: statistics.mean(v) for d, v in by_date.items()}
    out["ndates"] = len(dmeans)
    dv = list(dmeans.values())
    if len(dv) >= 2:
        out["dmean"] = statistics.mean(dv)
        out["dse"] = statistics.stdev(dv) / math.sqrt(len(dv))
        out["dt"] = out["dmean"] / out["dse"] if out["dse"] else 0.0
        # drop the 5 dates that most SUPPORT the sign of the effect
        sgn = 1 if out["dmean"] >= 0 else -1
        keep = sorted(dv, key=lambda x: -sgn * x)[5:]
        out["drop5"] = statistics.mean(keep) if len(keep) >= 5 else None
    out["trim"] = _trimmed_mean(vals)
    return out


def fmt(s: dict) -> str:
    if s.get("n", 0) < 2:
        return "%6d  %s" % (s.get("n", 0), "-- too few --")
    star = "*" if abs(s.get("dt", 0)) > 2 else " "
    return ("%6d %8.3f %8.3f %8.3f %8.4f %7.2f %6d %8.3f %8.4f %7.2f%s %8.3f %8.3f"
            % (s["n"], s["mean"], s["median"], s["sd"], s["se"], s["t"],
               s["ndates"], s.get("dmean", float("nan")),
               s.get("dse", float("nan")), s.get("dt", float("nan")), star,
               s["trim"] if s["trim"] is not None else float("nan"),
               s["drop5"] if s.get("drop5") is not None else float("nan")))


HDR = ("%-28s %6s %8s %8s %8s %8s %7s %6s %8s %8s %8s %8s %8s"
       % ("bucket", "n", "mean%", "med%", "sd", "se", "t", "dates", "dMean%",
          "dSE", "dT", "trim5%", "drop5d"))


def table(title: str, rows: List[dict], key: str, groups) -> Dict[str, dict]:
    print("\n" + title)
    print(HDR)
    print("-" * len(HDR))
    res = {}
    for name, sel in groups:
        sub = [r for r in rows if sel(r)]
        s = describe(sub, key)
        res[name] = s
        print("%-28s %s" % (name, fmt(s)))
    return res


def monotone(means: List[Optional[float]]) -> str:
    m = [x for x in means if x is not None]
    if len(m) < 3:
        return "n/a"
    up = all(b >= a for a, b in zip(m, m[1:]))
    dn = all(b <= a for a, b in zip(m, m[1:]))
    if up:
        return "YES (rising)"
    if dn:
        return "YES (falling)"
    flips = sum(1 for a, b, c in zip(m, m[1:], m[2:]) if (b - a) * (c - b) < 0)
    return "NO (%d direction flips across %d buckets)" % (flips, len(m))


def clv_bucket(r) -> int:
    for k in range(5):
        if CLV_EDGES[k] <= r["clv"] < CLV_EDGES[k + 1]:
            return k
    return 4


def spread_series(rows: List[dict], key: str, rr_sel) -> dict:
    """Per-date (top CLV bucket mean - bottom CLV bucket mean). Date-clustered by design."""
    per_date_hi, per_date_lo = defaultdict(list), defaultdict(list)
    for r in rows:
        if not rr_sel(r):
            continue
        b = clv_bucket(r)
        if b == 4:
            per_date_hi[r["date"]].append(r[key])
        elif b == 0:
            per_date_lo[r["date"]].append(r[key])
    dates = sorted(set(per_date_hi) & set(per_date_lo))
    vals = [statistics.mean(per_date_hi[d]) - statistics.mean(per_date_lo[d]) for d in dates]
    if len(vals) < 5:
        return {"n": len(vals)}
    sd = statistics.stdev(vals)
    se = sd / math.sqrt(len(vals))
    sgn = 1 if statistics.mean(vals) >= 0 else -1
    keep = sorted(vals, key=lambda x: -sgn * x)[5:]
    return {"n": len(vals), "mean": statistics.mean(vals), "median": statistics.median(vals),
            "sd": sd, "se": se, "t": statistics.mean(vals) / se if se else 0.0,
            "trim": _trimmed_mean(vals), "drop5": statistics.mean(keep) if keep else None}


def print_spread(label: str, s: dict):
    if s.get("n", 0) < 5:
        print("%-34s  (only %d overlapping dates)" % (label, s.get("n", 0)))
        return
    print("%-34s dates=%4d  mean=%+7.3f%%  med=%+7.3f%%  sd=%6.3f  se=%6.4f  t=%+6.2f%s  trim5=%+7.3f%%  drop5d=%+7.3f%%"
          % (label, s["n"], s["mean"], s["median"], s["sd"], s["se"], s["t"],
             "*" if abs(s["t"]) > 2 else " ", s["trim"], s["drop5"]))



# ------------------------------------------------------------------- stability checks
def per_year(rows: List[dict], key: str, sel, label: str):
    """Date-weighted mean per calendar year -- does the sign hold across regimes?"""
    years = sorted(set(r["date"][:4] for r in rows))
    parts = []
    for y in years:
        sub = [r for r in rows if r["date"][:4] == y and sel(r)]
        st = describe(sub, key)
        if st.get("n", 0) < 50:
            parts.append("%s: n=%d --" % (y, st.get("n", 0)))
        else:
            parts.append("%s: %+6.3f%% (n=%4d, d=%3d)" % (y, st["dmean"], st["n"], st["ndates"]))
    print("  %-42s %s" % (label, "  |  ".join(parts)))


def per_year_spread(rows: List[dict], key: str, rr_sel, label: str):
    years = sorted(set(r["date"][:4] for r in rows))
    parts = []
    for y in years:
        sub = [r for r in rows if r["date"][:4] == y]
        st = spread_series(sub, key, rr_sel)
        if st.get("n", 0) < 20:
            parts.append("%s: d=%d --" % (y, st.get("n", 0)))
        else:
            parts.append("%s: %+6.3f%% (d=%3d)" % (y, st["mean"], st["n"]))
    print("  %-42s %s" % (label, "  |  ".join(parts)))


def symbol_concentration(rows: List[dict], key: str, sel, label: str):
    """Is the cell carried by a few tickers? Drop the 5 most favourable symbols."""
    sub = [r for r in rows if sel(r)]
    st = describe(sub, key)
    if st.get("n", 0) < 50:
        print("  %-42s n=%d -- too few" % (label, st.get("n", 0)))
        return
    by_sym: Dict[str, List[float]] = defaultdict(list)
    for r in sub:
        by_sym[r["sym"]].append(r[key])
    sm = {k: statistics.mean(v) for k, v in by_sym.items()}
    sgn = 1 if st["dmean"] >= 0 else -1
    ranked = sorted(sm, key=lambda k: -sgn * sm[k])
    drop = set(ranked[:5])
    kept = [r for r in sub if r["sym"] not in drop]
    st2 = describe(kept, key)
    share_right_sign = sum(1 for v in sm.values() if sgn * v > 0) / float(len(sm))
    print("  %-42s syms=%3d  all=%+6.3f%%  drop-5-best-syms=%+6.3f%%  syms with correct sign=%.0f%%"
          % (label, len(sm), st["dmean"], st2["dmean"], 100 * share_right_sign))


def outlier_sanity(rows: List[dict], key: str, label: str, k: int = 8):
    vals = sorted(rows, key=lambda r: -abs(r[key]))[:k]
    print("  %-24s largest |%s|: %s" % (label, key,
          ", ".join("%s %s %+.1f%%" % (r["sym"], r["date"], r[key]) for r in vals)))


# ----------------------------------------------------------------------------- main
def main():
    obs = load_observations()
    train = [r for r in obs if r["date"] < SPLIT_DATE]
    test_a = [r for r in obs if r["date"] >= SPLIT_DATE and r["universe"] == "long"]
    test_b = [r for r in obs if r["date"] >= SPLIT_DATE and r["universe"] == "movers"]

    print("=" * 118)
    print("RANGE POSITION PROBE -- close-location-value x range/ATR -> next session")
    print("=" * 118)
    for nm, rows in (("TRAIN (t < %s)" % SPLIT_DATE, train),
                     ("TEST-A same universe (t >= %s)" % SPLIT_DATE, test_a),
                     ("TEST-B movers universe (t >= %s)" % SPLIT_DATE, test_b)):
        if not rows:
            print("%-34s  EMPTY" % nm)
            continue
        ds = sorted(set(r["date"] for r in rows))
        print("%-34s n=%6d  symbols=%3d  dates=%4d  %s .. %s"
              % (nm, len(rows), len(set(r["sym"] for r in rows)), len(ds), ds[0], ds[-1]))
    print("\ncolumns: mean/med/sd/se/t are per-observation; dSE/dT are DATE-CLUSTERED "
          "(sd of per-date means / sqrt(dates)) --")
    print("         dT is the honest test statistic because same-day observations are "
          "heavily cross-correlated. '*' = |dT| > 2.")
    print("         dMean% = equal-weight-by-DATE mean (what dT tests). trim5% = mean after "
          "dropping top and bottom 5% of observations.")
    print("         drop5d = date-weighted mean after removing the 5 dates most favourable "
          "to the sign of the effect.")
    print("         cost hurdle = %.2f%% per leg." % COST_PCT)

    # ---- RR tercile cuts learned on TRAIN only
    rrs = sorted(r["rr"] for r in train)
    rr_lo = rrs[len(rrs) // 3]
    rr_hi = rrs[2 * len(rrs) // 3]
    print("\nRR (range/ATR14) tercile cuts learned on TRAIN only: narrow < %.3f <= normal < %.3f <= wide"
          % (rr_lo, rr_hi))
    print("TRAIN RR percentiles: p10=%.2f p25=%.2f p50=%.2f p75=%.2f p90=%.2f"
          % tuple(rrs[int(len(rrs) * p)] for p in (.10, .25, .50, .75, .90)))

    rr_groups = [("narrow", lambda r: r["rr"] < rr_lo),
                 ("normal", lambda r: rr_lo <= r["rr"] < rr_hi),
                 ("wide", lambda r: r["rr"] >= rr_hi)]

    clv_only = [(CLV_NAMES[k], (lambda k: lambda r: clv_bucket(r) == k)(k)) for k in range(5)]

    def full_grid():
        g = []
        for rn, rsel in rr_groups:
            for k in range(5):
                g.append(("%-6s %s" % (rn, CLV_NAMES[k]),
                          (lambda k, rsel: lambda r: clv_bucket(r) == k and rsel(r))(k, rsel)))
        return g

    for split_name, rows in (("TRAIN", train), ("TEST-A (same universe)", test_a),
                             ("TEST-B (movers universe)", test_b)):
        if len(rows) < 100:
            continue
        print("\n" + "=" * 118)
        print("### %s" % split_name)
        print("=" * 118)
        for key, human in (("gap", "NEXT-DAY OVERNIGHT GAP  close_t -> open_t+1"),
                           ("o2c", "NEXT-DAY OPEN-TO-CLOSE  open_t+1 -> close_t+1")):
            r1 = table("[%s] %s -- by CLV only" % (split_name, human), rows, key, clv_only)
            print("  monotone across CLV: %s"
                  % monotone([r1[n].get("dmean") for n in CLV_NAMES]))
            r2 = table("[%s] %s -- by range/ATR x CLV" % (split_name, human), rows, key, full_grid())
            for rn, _ in rr_groups:
                print("  monotone across CLV within %-6s: %s"
                      % (rn, monotone([r2["%-6s %s" % (rn, CLV_NAMES[k])].get("dmean")
                                       for k in range(5)])))
            print("\n  LONG top-CLV / SHORT bottom-CLV, per-date spread (%s):" % key)
            print_spread("    all ranges", spread_series(rows, key, lambda r: True))
            for rn, rsel in rr_groups:
                print_spread("    %s ranges" % rn, spread_series(rows, key, rsel))

        # reference: close-to-close
        table("[%s] REFERENCE close_t -> close_t+1 -- by CLV only" % split_name,
              rows, "c2c", clv_only)

    # ---- best TRAIN cell, carried straight to TEST (no re-selection)
    print("\n" + "=" * 118)
    print("### BEST-ON-TRAIN CELL CARRIED TO TEST (cell chosen on TRAIN, never re-picked)")
    print("=" * 118)
    grid = full_grid()
    for key in ("gap", "o2c"):
        best, bstat = None, None
        for name, sel in grid:
            s = describe([r for r in train if sel(r)], key)
            if s.get("n", 0) < 300:
                continue
            if bstat is None or abs(s["dmean"]) > abs(bstat["dmean"]):
                best, bstat = (name, sel), s
        name, sel = best
        print("\n%s: best TRAIN cell = '%s'" % (key.upper(), name.strip()))
        print(HDR)
        print("%-28s %s" % ("  TRAIN", fmt(bstat)))
        for lbl, rows in (("  TEST-A", test_a), ("  TEST-B", test_b)):
            s = describe([r for r in rows if sel(r)], key)
            print("%-28s %s" % (lbl, fmt(s)))
        edge = abs(bstat["dmean"])
        print("  cost check: |TRAIN edge| = %.3f%% vs %.2f%% hurdle -> %s"
              % (edge, COST_PCT, "clears" if edge > COST_PCT else "DOES NOT CLEAR"))

    # ---- stability: does the sign hold across calendar years / across symbols?
    print("\n" + "=" * 118)
    print("### STABILITY -- sign across calendar years, and concentration across symbols")
    print("=" * 118)
    allrows = train + test_a
    print("\nDate-weighted mean by calendar year, long-history universe (TRAIN years then TEST years):")
    for k in range(5):
        per_year(allrows, "gap", (lambda k: lambda r: clv_bucket(r) == k)(k), "GAP  %s" % CLV_NAMES[k])
    for k in range(5):
        per_year(allrows, "o2c", (lambda k: lambda r: clv_bucket(r) == k)(k), "O2C  %s" % CLV_NAMES[k])
    per_year_spread(allrows, "o2c", lambda r: True, "O2C  long-top/short-bottom spread, all RR")
    per_year_spread(allrows, "o2c", lambda r: r["rr"] < rr_lo, "O2C  spread, narrow RR")
    per_year_spread(allrows, "gap", lambda r: True, "GAP  long-top/short-bottom spread, all RR")

    print("\nSymbol concentration (long-history universe, TRAIN):")
    symbol_concentration(train, "o2c", lambda r: clv_bucket(r) == 4, "O2C  CLV 0.8-1.0")
    symbol_concentration(train, "o2c", lambda r: clv_bucket(r) == 0 and r["rr"] >= rr_hi,
                         "O2C  wide CLV 0.0-0.2 (best TRAIN cell)")

    print("\nSymbol concentration (movers universe, TEST-B):")
    symbol_concentration(test_b, "gap", lambda r: clv_bucket(r) == 4, "GAP  CLV 0.8-1.0")
    symbol_concentration(test_b, "gap", lambda r: clv_bucket(r) == 0, "GAP  CLV 0.0-0.2")

    print("\nOutlier sanity (are the big numbers real moves or bad bars?):")
    outlier_sanity(test_b, "gap", "TEST-B")
    outlier_sanity([r for r in test_b if clv_bucket(r) == 4], "gap", "TEST-B CLV 0.8-1.0")
    outlier_sanity(train, "gap", "TRAIN")

    print("\nHit rate of the TEST-B overnight-reversion trade (short CLV>0.8 into next open):")
    sub = [r for r in test_b if clv_bucket(r) == 4]
    wins = sum(1 for r in sub if -r["gap"] > COST_PCT)
    print("  n=%d  trades beating the %.2f%% cost hurdle: %d (%.1f%%)  median P&L after cost = %+.3f%%"
          % (len(sub), COST_PCT, wins, 100.0 * wins / len(sub),
             statistics.median([-r["gap"] for r in sub]) - COST_PCT))

    print("\ndone.")


if __name__ == "__main__":
    main()
