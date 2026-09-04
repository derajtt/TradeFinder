#!/usr/bin/env python
"""
probe_session_autocorrelation.py

MAP: where do intraday 5-minute returns CONTINUE and where do they REVERT?

Two measurements, both cut by session bucket (ET clock) and by intraday
volatility state (day's realised vol so far vs the SAME CLOCK WINDOW on the
symbol's trailing 20 sessions):

  PART A  lag-1 autocorrelation of consecutive 5-minute returns
  PART B  mean forward 30-minute return conditioned on the sign and size of
          the trailing 15-minute move  (the tradeable form of the same thing)

Rules obeyed:
  * split by DATE (TRAIN < 2025-01-01, TEST >= 2025-01-01); nothing tuned on TEST
  * every cell reports n, mean, median, sd, se, DISTINCT SESSION DATES
  * SEs are date-clustered (per-date mean first, then sd/sqrt(#dates))
  * outlier check: mean recomputed with top/bottom 5% of observations dropped
  * all scaling is ex-ante (trailing 20 sessions, strictly prior dates only)
  * cost threshold 0.25% per trade

Run:  /Users/blackbox/TradeFinder/.venv/bin/python \
        scripts/research/probe_session_autocorrelation.py     (from backend/)
"""
import glob
import json
import math
import os
from collections import defaultdict

DATA = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT = "2025-01-01"
COST = 0.25          # % per round trip, slippage + spread proxy
BAR = 300            # seconds
TRIM = 0.05          # 5% each tail for the outlier-robustness recompute

# ---------------------------------------------------------------- buckets ---
# ET minute-of-day. The cache's `date` string is already ET wall clock
# (verified: 2026-09-01 04:00:00 == epoch 1788249600 == 08:00 UTC == 04:00 EDT).
BUCKETS = [
    ("PRE      04:00-09:29", 240, 569),
    ("OPEN     09:30-09:59", 570, 599),
    ("MORNING  10:00-11:29", 600, 689),
    ("MIDDAY   11:30-13:59", 690, 839),
    ("AFTN     14:00-14:59", 840, 899),
    ("CLOSE    15:00-15:59", 900, 959),
]
BORDER = [b[0] for b in BUCKETS]

# trailing-15-minute move measured in ex-ante sigmas
SIZE_EDGES = [-2.0, -1.0, -0.33, 0.33, 1.0, 2.0]
SIZE_NAMES = ["s<=-2", "-2..-1", "-1..-0.33", "-0.33..0.33",
              "0.33..1", "1..2", "s>=2"]

VOL_NAMES = ["LOWvol", "MIDvol", "HIGHvol"]


def bucket_of(mod):
    for name, lo, hi in BUCKETS:
        if lo <= mod <= hi:
            return name
    return None


def size_bucket(s):
    i = 0
    while i < len(SIZE_EDGES) and s >= SIZE_EDGES[i]:
        i += 1
    return SIZE_NAMES[i]


# ------------------------------------------------------------------ stats ---
def mean(v):
    return sum(v) / len(v) if v else float("nan")


def median(v):
    if not v:
        return float("nan")
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def sd(v):
    n = len(v)
    if n < 2:
        return float("nan")
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))


def trimmed_mean(v, frac=TRIM):
    if len(v) < 20:
        return float("nan")
    s = sorted(v)
    k = int(len(s) * frac)
    core = s[k:len(s) - k] if k else s
    return mean(core) if core else float("nan")


def clustered_se(vals, dates):
    """Per-date mean first, then sd/sqrt(#dates). Robust to same-day clustering."""
    by = defaultdict(list)
    for x, d in zip(vals, dates):
        by[d].append(x)
    per = [mean(v) for v in by.values()]
    if len(per) < 2:
        return float("nan")
    return sd(per) / math.sqrt(len(per))


class Cell(object):
    """Accumulator for one (bucket, condition) cell."""

    def __init__(self):
        self.v = []
        self.d = []

    def add(self, x, date):
        self.v.append(x)
        self.d.append(date)

    def n(self):
        return len(self.v)

    def ndates(self):
        return len(set(self.d))

    def summary(self):
        v, d = self.v, self.d
        m = mean(v)
        se = clustered_se(v, d)
        return dict(n=len(v), mean=m, median=median(v), sd=sd(v), se=se,
                    ndates=len(set(d)),
                    tstat=(m / se if se and se == se and se > 0 else float("nan")),
                    trim=trimmed_mean(v),
                    top_date_share=self._top_date_share())

    def _top_date_share(self):
        """Share of the total sum contributed by the single biggest date.
        A cell where one session carries the effect is not an effect."""
        if not self.v:
            return float("nan")
        tot = sum(self.v)
        if abs(tot) < 1e-12:
            return float("nan")
        by = defaultdict(float)
        for x, dd in zip(self.v, self.d):
            by[dd] += x
        return max(by.values(), key=abs) / tot


# ------------------------------------------------------------------- load ---
def load(path):
    """-> {date_str: [bar,...]} sorted by time, each bar gets 'mod' (ET min)."""
    raw = json.load(open(path))["bars"]
    days = defaultdict(list)
    for b in raw.values():
        ds = b["date"]
        try:
            mod = int(ds[11:13]) * 60 + int(ds[14:16])
        except (ValueError, IndexError):
            continue
        if b.get("c", 0) <= 0 or b.get("o", 0) <= 0:
            continue
        b = dict(b)
        b["mod"] = mod
        days[ds[:10]].append(b)
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return days


def day_rets(bars):
    """[(idx, logret, mod_end)] for consecutive 5-min bars only."""
    out = []
    for i in range(1, len(bars)):
        if bars[i]["time"] - bars[i - 1]["time"] != BAR:
            continue
        c0, c1 = bars[i - 1]["c"], bars[i]["c"]
        if c0 <= 0 or c1 <= 0:
            continue
        out.append((i, math.log(c1 / c0), bars[i]["mod"]))
    return out


# --------------------------------------------------------------- analysis ---
def analyse(symbols, suffix, label, session_only=True, do_split=True):
    """Returns (partA, partB, partBvol, meta) keyed by TRAIN/TEST."""
    # cells
    A = defaultdict(lambda: defaultdict(Cell))    # partition -> bucket -> products
    Az = defaultdict(lambda: defaultdict(Cell))   # partition -> bucket -> z_t^2
    Az2 = defaultdict(lambda: defaultdict(Cell))  # partition -> bucket -> z_{t+1}^2
    Av = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}       # (bucket,vol) -> products
    Avz = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}
    Avz2 = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}
    B = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}        # (bucket,size) -> fwd%
    Bv = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}       # (bucket,size,vol) -> fwd%
    M = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}        # (bucket,vol) -> mom pnl, |s|>=1
    F = {p: defaultdict(Cell) for p in ("TRAIN", "TEST")}        # PART C: lag-1 fade P&L
    meta = dict(symbols=0, bars=0, dates=set(), skipped=[], symlist=[])

    for sym in symbols:
        path = os.path.join(DATA, "%s_%s.json" % (sym, suffix))
        if not os.path.exists(path):
            meta["skipped"].append(sym)
            continue
        days = load(path)
        dates = sorted(days)
        if len(dates) < 3:
            meta["skipped"].append(sym)
            continue
        meta["symbols"] += 1
        meta["symlist"].append(sym)

        # ---- ex-ante per-day return pools (for sigma20 and clock-window rv)
        rets_by_date = {}
        rv_win_by_date = {}       # date -> {mod_end: cumulative sum of r^2 from open}
        for d in dates:
            bars = days[d]
            if session_only:
                bars = [b for b in bars if bucket_of(b["mod"]) is not None]
                days[d] = bars
            meta["bars"] += len(bars)
            rr = day_rets(bars)
            rets_by_date[d] = [x[1] for x in rr]
            cum, s2, k = {}, 0.0, 0
            for _, r, mod in rr:
                s2 += r * r
                k += 1
                cum[mod] = (s2, k)
            rv_win_by_date[d] = cum
        meta["dates"] |= set(dates)

        for di, d in enumerate(dates):
            if di < 20:
                continue
            prior = dates[di - 20:di]
            pool = []
            for pd_ in prior:
                pool.extend(rets_by_date[pd_])
            if len(pool) < 60:
                continue
            sig = sd(pool)
            if not sig or sig != sig or sig <= 0:
                continue
            part = "TEST" if (do_split and d >= SPLIT) else "TRAIN"
            # extra partitions: per-symbol, and a half-split INSIDE train, so we
            # can see whether any cell is carried by one name or one stretch.
            subs = [part, part + "|" + sym]
            if part == "TRAIN":
                subs.append("TRAIN-H1" if d < "2024-07-01" else "TRAIN-H2")

            bars = days[d]
            rr = day_rets(bars)
            if len(rr) < 8:
                continue
            idx_of = {}
            for j, (i, r, mod) in enumerate(rr):
                idx_of[i] = j

            # ---- vol state at a given bar: today's rv from the open up to
            # this clock time vs the SAME clock window on the prior sessions.
            def vol_state(mod, kmin=3):
                cur = rv_win_by_date[d].get(mod)
                if not cur or cur[1] < kmin:
                    return None
                today = math.sqrt(cur[0] / cur[1])
                hist = []
                for pd_ in prior:
                    c = rv_win_by_date[pd_].get(mod)
                    if c and c[1] >= kmin:
                        hist.append(math.sqrt(c[0] / c[1]))
                if len(hist) < 10:
                    return None
                rank = sum(1 for h in hist if h < today) / float(len(hist))
                if rank < 1.0 / 3:
                    return VOL_NAMES[0]
                if rank < 2.0 / 3:
                    return VOL_NAMES[1]
                return VOL_NAMES[2]

            # ---------------- PART A : lag-1 autocorrelation ----------------
            for j in range(len(rr) - 1):
                i0, r0, m0 = rr[j]
                b0 = bucket_of(m0)
                if b0 is None:
                    continue
                z0 = r0 / sig
                vs = vol_state(m0)
                # lags 1..3: bid-ask bounce lives at lag 1 only, genuine
                # reversion decays smoothly. This is the discriminator.
                for L in (1, 2, 3):
                    if j + L >= len(rr):
                        continue
                    iL, rL, mL = rr[j + L]
                    if iL != i0 + L:            # returns must be truly adjacent
                        continue
                    if bucket_of(mL) != b0:     # both legs in the same bucket
                        continue
                    zL = rL / sig
                    key = (b0, L)
                    for p_ in subs:
                        A[p_][key].add(z0 * zL, d)
                        Az[p_][key].add(z0 * z0, d)
                        Az2[p_][key].add(zL * zL, d)
                    if vs and L == 1:
                        Av[part][(b0, vs)].add(z0 * zL, d)
                        Avz[part][(b0, vs)].add(z0 * z0, d)
                        Avz2[part][(b0, vs)].add(zL * zL, d)
                    if L == 1:
                        # PART C: the trade the lag-1 rho actually implies --
                        # fade the last 5-min bar, hold exactly one bar.
                        pnl = -(1.0 if r0 > 0 else -1.0) * (
                            100.0 * (math.exp(rL) - 1.0))
                        F[part][(b0, "ALL", "all")].add(pnl, d)
                        if abs(z0) >= 1.0:
                            F[part][(b0, "ALL", "1sig")].add(pnl, d)
                        if vs:
                            F[part][(b0, vs, "all")].add(pnl, d)
                            if abs(z0) >= 1.0:
                                F[part][(b0, vs, "1sig")].add(pnl, d)

            # -------- PART B : forward 30m | trailing 15m sign and size -----
            n = len(bars)
            for i in range(3, n - 6):
                if bars[i]["time"] - bars[i - 3]["time"] != 3 * BAR:
                    continue
                if bars[i + 6]["time"] - bars[i]["time"] != 6 * BAR:
                    continue
                c_m3, c_0, c_p6 = bars[i - 3]["c"], bars[i]["c"], bars[i + 6]["c"]
                if min(c_m3, c_0, c_p6) <= 0:
                    continue
                bk = bucket_of(bars[i]["mod"])
                if bk is None or bucket_of(bars[i + 6]["mod"]) is None:
                    continue
                trail_log = math.log(c_0 / c_m3)
                s = trail_log / (sig * math.sqrt(3.0))
                fwd = 100.0 * (c_p6 / c_0 - 1.0)
                sb = size_bucket(s)
                B[part][(bk, sb)].add(fwd, d)
                vs = vol_state(bars[i]["mod"])
                if vs:
                    Bv[part][(bk, sb, vs)].add(fwd, d)
                if abs(s) >= 1.0:
                    sgn = 1.0 if trail_log > 0 else -1.0
                    M[part][(bk, "ALL")].add(sgn * fwd, d)
                    if vs:
                        M[part][(bk, vs)].add(sgn * fwd, d)

    return dict(A=A, Az=Az, Az2=Az2, Av=Av, Avz=Avz, Avz2=Avz2,
                B=B, Bv=Bv, M=M, F=F, meta=meta, label=label)


# ------------------------------------------------------------------ print ---
def rho_row(prod, den1, den2):
    """Pooled lag-1 autocorrelation + date-clustered SE."""
    if prod.n() < 30:
        return None
    v = math.sqrt(mean(den1.v) * mean(den2.v))
    if v <= 0:
        return None
    c = mean(prod.v)
    se_c = clustered_se(prod.v, prod.d)
    rho = c / v
    se = se_c / v if se_c == se_c else float("nan")
    tm = trimmed_mean(prod.v)
    return dict(rho=rho, se=se, n=prod.n(), ndates=prod.ndates(),
                t=(rho / se if se == se and se > 0 else float("nan")),
                trim=(tm / v if tm == tm else float("nan")),
                med=median(prod.v) / v)


def f(x, w=8, p=3):
    if x is None or x != x:
        return "   n/a  ".rjust(w)
    return ("%*.*f" % (w, p, x))


def print_partA(res, part):
    print("\n%s  PART A - lag-1 autocorrelation of consecutive 5-min returns [%s]"
          % (res["label"], part))
    print("  (pooled over ex-ante-sigma-scaled returns; SE clustered by date;"
          " |t|>2 == more than 2 SEs from zero)")
    print("  %-22s %7s %7s %7s %7s %6s %7s  %s" %
          ("bucket", "rho", "se", "t", "trim", "dates", "pairs", "read"))
    for bk in BORDER:
        k1 = (bk, 1)
        r = rho_row(res["A"][part][k1], res["Az"][part][k1],
                    res["Az2"][part][k1])
        if not r:
            print("  %-22s %s" % (bk, "  (no data)"))
            continue
        read = "REVERSION" if r["rho"] < 0 else "MOMENTUM"
        if abs(r["t"]) < 2:
            read = "flat (<2se)"
        print("  %-22s %s %s %s %s %6d %7d  %s" %
              (bk, f(r["rho"], 7, 4), f(r["se"], 7, 4), f(r["t"], 7, 2),
               f(r["trim"], 7, 4), r["ndates"], r["n"], read))
    print("  -- by intraday volatility state (rv so far vs same clock window,"
          " trailing 20 sessions) --")
    for bk in BORDER:
        line = []
        any_ = False
        for vs in VOL_NAMES:
            r = rho_row(res["Av"][part][(bk, vs)], res["Avz"][part][(bk, vs)],
                        res["Avz2"][part][(bk, vs)])
            if r:
                any_ = True
                line.append("%s rho=%s t=%s n=%-6d" %
                            (vs, f(r["rho"], 7, 4), f(r["t"], 6, 2), r["n"]))
            else:
                line.append("%s      --" % vs)
        if any_:
            print("  %-22s %s" % (bk, "  ".join(line)))


def print_partB(res, part, vol=False):
    tag = " x volatility state" if vol else ""
    print("\n%s  PART B - mean forward 30-min return (%%) by trailing 15-min move%s [%s]"
          % (res["label"], tag, part))
    print("  trailing move measured in ex-ante sigmas; costs = %.2f%%/trade" % COST)
    hdr = ("  %-22s %-12s %6s %8s %8s %8s %8s %6s %6s %8s" %
           ("bucket", "trail size", "n", "mean%", "median%", "sd", "se", "t",
            "dates", "trim%"))
    print(hdr)
    src = res["Bv"][part] if vol else res["B"][part]
    for bk in BORDER:
        printed = False
        for sb in SIZE_NAMES:
            keys = ([(bk, sb, vs) for vs in VOL_NAMES] if vol else [(bk, sb)])
            for k in keys:
                cell = src.get(k)
                if cell is None or cell.n() < 30:
                    continue
                s = cell.summary()
                nm = sb + ("/" + k[2] if vol else "")
                print("  %-22s %-12s %6d %s %s %s %s %s %6d %s" %
                      (bk if not printed else "", nm, s["n"],
                       f(s["mean"], 8, 3), f(s["median"], 8, 3), f(s["sd"], 8, 3),
                       f(s["se"], 8, 3), f(s["tstat"], 6, 2), s["ndates"],
                       f(s["trim"], 8, 3)))
                printed = True
        if printed:
            print("")


def print_mom(res, part):
    print("\n%s  TRADEABLE FORM - momentum P&L = sign(trailing 15m) x forward 30m,"
          " |move| >= 1 sigma  [%s]" % (res["label"], part))
    print("  positive => CONTINUATION pays, negative => FADING pays."
          " Needs |mean| > %.2f%% to clear costs." % COST)
    print("  %-22s %-8s %7s %8s %8s %8s %7s %6s %8s %8s %s" %
          ("bucket", "volstate", "n", "mean%", "median%", "sd", "se", "t",
           "dates", "trim%", "top-date-share"))
    for bk in BORDER:
        for vs in ["ALL"] + VOL_NAMES:
            cell = res["M"][part].get((bk, vs))
            if cell is None or cell.n() < 30:
                continue
            s = cell.summary()
            net = abs(s["mean"]) - COST
            flag = ""
            if abs(s["tstat"]) >= 2 and s["tstat"] == s["tstat"]:
                flag += " 2se"
            if net > 0:
                flag += " CLEARS-COST"
            print("  %-22s %-8s %7d %s %s %s %s %s %6d %s %s%s" %
                  (bk if vs == "ALL" else "", vs, s["n"],
                   f(s["mean"], 8, 3), f(s["median"], 8, 3), f(s["sd"], 8, 3),
                   f(s["se"], 7, 3), f(s["tstat"], 6, 2), s["ndates"],
                   f(s["trim"], 8, 3), f(s["top_date_share"], 6, 2), flag))
        print("")



def print_partA_stability(res):
    """Is any Part-A cell carried by one symbol or one stretch of TRAIN?"""
    syms = res["meta"]["symlist"]
    print("\n%s  PART A STABILITY - lag-1 rho per symbol and per TRAIN half"
          % res["label"])
    print("  %-22s %9s %9s | %9s %9s | %s" %
          ("bucket", "TRAIN", "TEST", "TRAIN-H1", "TRAIN-H2",
           " ".join("%9s" % x for x in syms)))
    for bk in BORDER:
        cells = []
        for p_ in ["TRAIN", "TEST", "TRAIN-H1", "TRAIN-H2"] + \
                  ["TRAIN|" + s2 for s2 in syms]:
            k1 = (bk, 1)
            r = rho_row(res["A"][p_][k1], res["Az"][p_][k1],
                        res["Az2"][p_][k1])
            cells.append(r["rho"] if r else None)
        if all(c is None for c in cells):
            continue
        def fmt(x):
            return ("%9.4f" % x) if x is not None else "     --  "
        print("  %-22s %s %s | %s %s | %s" %
              (bk, fmt(cells[0]), fmt(cells[1]), fmt(cells[2]), fmt(cells[3]),
               " ".join(fmt(c) for c in cells[4:])))
    print("  (TRAIN-H1 = 2024-01..2024-06, TRAIN-H2 = 2024-07..2024-12;"
          " per-symbol columns are TRAIN only)")



def print_lag_decay(res, part):
    """Bid-ask bounce is a lag-1-only artifact; economic reversion decays
    smoothly across lags. This table tells the two apart."""
    print("\n%s  PART A LAG DECAY - rho at lag 1 / 2 / 3 (5-min returns) [%s]"
          % (res["label"], part))
    print("  a lag-1-only negative rho is bid-ask bounce (untradeable);"
          " smooth decay would be real reversion")
    print("  %-22s %19s %19s %19s" % ("bucket", "lag1 (rho, t)",
                                      "lag2 (rho, t)", "lag3 (rho, t)"))
    for bk in BORDER:
        cells = []
        for L in (1, 2, 3):
            k = (bk, L)
            r = rho_row(res["A"][part][k], res["Az"][part][k], res["Az2"][part][k])
            cells.append(r)
        if all(c is None for c in cells):
            continue
        txt = []
        for r in cells:
            txt.append("%9.4f %8.2f" % (r["rho"], r["t"]) if r else "       --       ")
        print("  %-22s %s" % (bk, " ".join("%19s" % t for t in txt)))



def print_fade(res, part):
    """PART C - P&L of the trade the lag-1 autocorrelation implies."""
    if "F" not in res:
        return
    print("\n%s  PART C - P&L of the lag-1 fade: short the last 5-min bar's"
          " direction, hold ONE bar [%s]" % (res["label"], part))
    print("  This is the strongest form of the measured effect. Needs"
          " |mean| > %.2f%% to clear costs." % COST)
    print("  %-22s %-8s %-6s %8s %8s %8s %8s %7s %6s %6s %8s %s" %
          ("bucket", "volstate", "filt", "n", "mean%", "median%", "sd", "se",
           "t", "dates", "trim%", "verdict"))
    for bk in BORDER:
        shown = False
        for vs in ["ALL"] + VOL_NAMES:
            for filt in ("all", "1sig"):
                c = res["F"][part].get((bk, vs, filt))
                if c is None or c.n() < 30:
                    continue
                s_ = c.summary()
                v = "clears cost" if abs(s_["mean"]) > COST else "under cost"
                if abs(s_["tstat"]) < 2:
                    v += ", <2se"
                else:
                    v += ", >2se"
                print("  %-22s %-8s %-6s %8d %s %s %s %s %s %6d %s  %s" %
                      (bk if not shown else "", vs, filt, s_["n"],
                       f(s_["mean"], 8, 4), f(s_["median"], 8, 4),
                       f(s_["sd"], 8, 3), f(s_["se"], 8, 4),
                       f(s_["tstat"], 7, 2), s_["ndates"],
                       f(s_["trim"], 8, 4), v))
                shown = True
        if shown:
            print("")



def price_tier_fade(symbols):
    """Is the movers-universe 'edge' real, or is it the bid-ask spread?

    If it is the spread, the apparent lag-1 fade edge must grow as price falls
    (a 1c tick is a bigger % of a $2 stock) and must track the share of bars
    that print no change at all. Pooled over all session buckets, RTH only.
    """
    print("\n[MOVERS-3DAY]  SPREAD TEST - lag-1 rho and fade P&L by price tier")
    print("  If the 'edge' is really the spread, it grows as price falls.")
    print("  %-14s %6s %8s %9s %9s %9s %9s %9s" %
          ("price tier", "syms", "pairs", "rho1", "fade%", "fade1sig%",
           "zero-bar%", "med|5m ret|%"))
    tiers = [("$0-3", 0.0, 3.0), ("$3-10", 3.0, 10.0),
             ("$10-30", 10.0, 30.0), ("$30+", 30.0, 1e9)]
    for tname, lo, hi in tiers:
        prod, zz, zz2 = Cell(), Cell(), Cell()
        fade, fade1 = Cell(), Cell()
        nz = [0, 0]
        absr = []
        nsym = 0
        for sym in symbols:
            path = os.path.join(DATA, "%s_5min_ext.json" % sym)
            if not os.path.exists(path):
                continue
            days = load(path)
            closes = [b["c"] for d in days for b in days[d]]
            if not closes:
                continue
            med_px = median(closes)
            if not (lo <= med_px < hi):
                continue
            allr = []
            for d in days:
                days[d] = [b for b in days[d] if bucket_of(b["mod"]) is not None]
                allr.extend(x[1] for x in day_rets(days[d]))
            if len(allr) < 60:
                continue
            sig = sd(allr)
            if not sig or sig != sig or sig <= 0:
                continue
            nsym += 1
            for d in sorted(days):
                rr = day_rets(days[d])
                for j in range(len(rr) - 1):
                    i0, r0, m0 = rr[j]
                    i1, r1, m1 = rr[j + 1]
                    if i1 != i0 + 1 or bucket_of(m0) is None \
                            or bucket_of(m0) != bucket_of(m1):
                        continue
                    nz[1] += 1
                    if r0 == 0.0:
                        nz[0] += 1
                    absr.append(abs(100.0 * (math.exp(r0) - 1.0)))
                    z0, z1 = r0 / sig, r1 / sig
                    prod.add(z0 * z1, d)
                    zz.add(z0 * z0, d)
                    zz2.add(z1 * z1, d)
                    pnl = -(1.0 if r0 > 0 else -1.0) * (100.0 * (math.exp(r1) - 1.0))
                    if r0 != 0.0:
                        fade.add(pnl, d)
                        if abs(z0) >= 1.0:
                            fade1.add(pnl, d)
        r = rho_row(prod, zz, zz2)
        if not r or fade.n() < 30:
            continue
        print("  %-14s %6d %8d %9.4f %9.4f %9.4f %9.1f %9.4f" %
              (tname, nsym, prod.n(), r["rho"], mean(fade.v),
               mean(fade1.v) if fade1.n() else float("nan"),
               100.0 * nz[0] / max(nz[1], 1), median(absr)))


def monotone_check(res, part):
    """Is mean forward return monotone across the 7 signed trailing-size buckets?"""
    print("\n%s  MONOTONICITY across trailing-size buckets (mean fwd 30m %%) [%s]"
          % (res["label"], part))
    print("  %-22s %s" % ("bucket", "  ".join("%11s" % s for s in SIZE_NAMES)))
    out = {}
    for bk in BORDER:
        row, ok = [], True
        for sb in SIZE_NAMES:
            c = res["B"][part].get((bk, sb))
            row.append(c.summary()["mean"] if c and c.n() >= 30 else None)
        vals = [x for x in row if x is not None]
        if len(vals) >= 5:
            inc = all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
            dec = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
            ok = inc or dec
        else:
            ok = False
        out[bk] = ok
        if vals:
            print("  %-22s %s   monotone=%s" %
                  (bk, "  ".join(("%11.3f" % x) if x is not None else "     --   "
                                 for x in row), ok))
    return out


def header(res):
    m = res["meta"]
    print("\n" + "=" * 118)
    print("UNIVERSE %s | symbols=%d  bars=%d  distinct sessions=%d  span %s..%s"
          % (res["label"], m["symbols"], m["bars"], len(m["dates"]),
             min(m["dates"]) if m["dates"] else "-",
             max(m["dates"]) if m["dates"] else "-"))
    if m["skipped"]:
        print("  skipped (missing/too short): %s" % ",".join(m["skipped"][:12]))
    print("=" * 118)


# ------------------------------------------------------------------- main ---
def main():
    print("probe_session_autocorrelation.py")
    print("TRAIN = signal dates < %s ; TEST = >= %s ; costs %.2f%%/trade"
          % (SPLIT, SPLIT, COST))

    # ---- what is actually on disk (this constrains everything below) -------
    deep = sorted(os.path.basename(p).split("_")[0]
                  for p in glob.glob(os.path.join(DATA, "*_5min.json")))
    ext = sorted(os.path.basename(p).split("_")[0]
                 for p in glob.glob(os.path.join(DATA, "*_5min_ext.json")))
    ext_days = set()
    for p in glob.glob(os.path.join(DATA, "*_5min_ext.json"))[:40]:
        ext_days |= set(k[:10] for k in json.load(open(p))["bars"])
    print("\nDATA INVENTORY (read-only check, no network)")
    print("  *_5min.json      : %d symbols %s  (deep history, REGULAR SESSION ONLY)"
          % (len(deep), deep))
    print("  *_5min_ext.json  : %d symbols, but only %d distinct dates: %s"
          % (len(ext), len(ext_days), sorted(ext_days)))
    print("  => 5-minute history deep enough for a 2024/2025 date split exists for"
          " %d symbols only." % len(deep))

    eq = [s for s in deep if s not in ("BTCUSD", "ETHUSD", "SOLUSD")]
    cr = [s for s in deep if s in ("BTCUSD", "ETHUSD", "SOLUSD")]

    results = []
    r1 = analyse(eq, "5min", "[EQ%d]" % len(eq))
    results.append(r1)
    r2 = analyse(cr, "5min", "[CRYPTO%d]" % len(cr))
    results.append(r2)

    for res in (r1, r2):
        header(res)
        for part in ("TRAIN", "TEST"):
            print_partA(res, part)
        for part in ("TRAIN", "TEST"):
            print_lag_decay(res, part)
        print_partA_stability(res)
        for part in ("TRAIN", "TEST"):
            print_partB(res, part, vol=False)
        monotone_check(res, "TRAIN")
        monotone_check(res, "TEST")
        for part in ("TRAIN", "TEST"):
            print_fade(res, part)
        for part in ("TRAIN", "TEST"):
            print_mom(res, part)
        print_partB(res, "TRAIN", vol=True)
        print_partB(res, "TEST", vol=True)

    # ---- breadth check on the 191-symbol movers cache (3 dates only) -------
    print("\n\n" + "#" * 118)
    print("# BREADTH CHECK on %d movers symbols from *_5min_ext.json." % len(ext))
    print("# THIS IS NOT EVIDENCE: only %d distinct session dates exist in that"
          " cache. Rule 2 kills it." % len(ext_days))
    print("# It is here only because it is the ONLY source of premarket bars and"
          " the only wide cross-section.")
    print("#" * 118)
    # a 20-session warmup is impossible with 3 days, so sigma is in-sample here
    r3 = analyse_short(ext)
    header(r3)
    print_partA(r3, "TRAIN")
    print_lag_decay(r3, "TRAIN")
    print_fade(r3, "TRAIN")
    price_tier_fade(ext)
    print_partB(r3, "TRAIN", vol=False)
    print_mom(r3, "TRAIN")


def analyse_short(symbols):
    """Same measurement on the 3-day movers cache, with in-sample sigma
    (no 20-session warmup is possible). Explicitly a breadth sniff test."""
    A = defaultdict(lambda: defaultdict(Cell))
    Az = defaultdict(lambda: defaultdict(Cell))
    Az2 = defaultdict(lambda: defaultdict(Cell))
    Av = {p: defaultdict(Cell) for p in ("TRAIN",)}
    Avz = {p: defaultdict(Cell) for p in ("TRAIN",)}
    Avz2 = {p: defaultdict(Cell) for p in ("TRAIN",)}
    B = {p: defaultdict(Cell) for p in ("TRAIN",)}
    Bv = {p: defaultdict(Cell) for p in ("TRAIN",)}
    M = {p: defaultdict(Cell) for p in ("TRAIN",)}
    F = {p: defaultdict(Cell) for p in ("TRAIN",)}
    meta = dict(symbols=0, bars=0, dates=set(), skipped=[])
    part = "TRAIN"
    for sym in symbols:
        path = os.path.join(DATA, "%s_5min_ext.json" % sym)
        if not os.path.exists(path):
            continue
        days = load(path)
        dates = sorted(days)
        allr = []
        for d in dates:
            days[d] = [b for b in days[d] if bucket_of(b["mod"]) is not None]
            allr.extend(x[1] for x in day_rets(days[d]))
        if len(allr) < 60:
            meta["skipped"].append(sym)
            continue
        sig = sd(allr)
        if not sig or sig != sig or sig <= 0:
            meta["skipped"].append(sym)
            continue
        meta["symbols"] += 1
        meta["dates"] |= set(dates)
        for d in dates:
            bars = days[d]
            meta["bars"] += len(bars)
            rr = day_rets(bars)
            for j in range(len(rr) - 1):
                i0, r0, m0 = rr[j]
                i1, r1, m1 = rr[j + 1]
                if i1 != i0 + 1:
                    continue
                b0, b1 = bucket_of(m0), bucket_of(m1)
                if b0 is None or b0 != b1:
                    continue
                z0, z1 = r0 / sig, r1 / sig
                A[part][(b0, 1)].add(z0 * z1, d)
                Az[part][(b0, 1)].add(z0 * z0, d)
                Az2[part][(b0, 1)].add(z1 * z1, d)
                pnl = -(1.0 if r0 > 0 else -1.0) * (100.0 * (math.exp(r1) - 1.0))
                F[part][(b0, "ALL", "all")].add(pnl, d)
                if abs(z0) >= 1.0:
                    F[part][(b0, "ALL", "1sig")].add(pnl, d)
                for L in (2, 3):
                    if j + L < len(rr) and rr[j + L][0] == i0 + L \
                            and bucket_of(rr[j + L][2]) == b0:
                        zL = rr[j + L][1] / sig
                        A[part][(b0, L)].add(z0 * zL, d)
                        Az[part][(b0, L)].add(z0 * z0, d)
                        Az2[part][(b0, L)].add(zL * zL, d)
            n = len(bars)
            for i in range(3, n - 6):
                if bars[i]["time"] - bars[i - 3]["time"] != 3 * BAR:
                    continue
                if bars[i + 6]["time"] - bars[i]["time"] != 6 * BAR:
                    continue
                c_m3, c_0, c_p6 = bars[i - 3]["c"], bars[i]["c"], bars[i + 6]["c"]
                if min(c_m3, c_0, c_p6) <= 0:
                    continue
                bk = bucket_of(bars[i]["mod"])
                if bk is None or bucket_of(bars[i + 6]["mod"]) is None:
                    continue
                tl = math.log(c_0 / c_m3)
                s = tl / (sig * math.sqrt(3.0))
                fwd = 100.0 * (c_p6 / c_0 - 1.0)
                B[part][(bk, size_bucket(s))].add(fwd, d)
                if abs(s) >= 1.0:
                    M[part][(bk, "ALL")].add((1.0 if tl > 0 else -1.0) * fwd, d)
    return dict(A=A, Az=Az, Az2=Az2, Av=Av, Avz=Avz, Avz2=Avz2,
                B=B, Bv=Bv, M=M, F=F, meta=meta, label="[MOVERS-3DAY]")


if __name__ == "__main__":
    main()
