#!/usr/bin/env python
"""
probe_xs_volume_surprise.py -- CROSS-SECTIONAL volume surprise, split by day sign.

THE EFFECT
  On each date, rank every symbol against every other symbol by VOLUME SURPRISE
      surprise = v_t / median(v_{t-20..t-1})          (strictly causal, own-symbol)
  a unit-free ratio, so a $400 name and a $2 name are comparable.  Then read
  forward returns by quintile/decile of that ranking, SPLIT BY THE SIGN OF THE
  DAY'S OWN RETURN.

  HYPOTHESIS (asymmetry): a volume spike on a DOWN day and a volume spike on an
  UP day resolve differently.  A symmetric result is a null.

  HEADLINE TRADE: on date t, long the top-surprise names that closed DOWN,
  short the top-surprise names that closed UP.  Self-financing, same-date,
  market-neutral by construction.  Must clear ~0.50% to be worth anything.

METHOD (round-one discipline, plus what cross-sectional work requires)
  * Split by DATE.  TRAIN = signal dates < 2025-01-01, TEST >= 2025-01-01.
    Nothing is fit on either sample -- the ranking is a pure cross-sectional
    percentile with fixed a-priori bucket edges -- so TEST is never tuned on.
  * STANDARD ERRORS CLUSTERED BY DATE everywhere.  Same-day observations across
    symbols are not independent; the naive t is reported next to it only to show
    the size of the lie.
  * MINIMUM CROSS-SECTION of 20 usable symbols per date; dates below that are
    dropped and the surviving count is reported.
  * Per bucket: n, mean, MEDIAN, sd, clustered SE, clustered t, distinct dates.
  * Bucket returns are also reported cross-sectionally DEMEANED (raw minus that
    date's cross-sectional mean), which is the natural market-neutralisation for
    a cross-sectional test and needs no beta estimate.
  * MONOTONICITY across buckets is the test, not any single extreme bucket.
    A Fama-MacBeth slope on the fractional rank measures the gradient directly.
  * PRICE-TIER CHECK: every headline number re-run inside each within-date price
    tercile.  An effect that lives only in the cheap tercile is a spread
    artifact, not an edge.
  * Cost floor: 0.25% for a long-only single leg, 0.50% for a long/short spread.
  * DROP THE 5 MOST INFLUENTIAL DATES and re-report.

UNIVERSE
  Individual equities only.  Crypto (BTCUSD/ETHUSD/SOLUSD -- weekend sessions,
  different asset) and index/sector ETFs (SPY/QQQ/IWM/SMH/XLE/XLF -- a baskets'
  volume spike is not a single-name event) are excluded from the ranked
  cross-section.  A sensitivity run puts the ETFs back.

DATA SHAPE (measured, see the header the script prints)
  The daily cache is bimodal: 22 individual names span 2022-01..2025-08 with a
  cross-section of exactly 22/day, then a gap, then ~183 names over
  2026-05..2026-09.  So TEST contains two structurally different regimes and
  they are reported separately.  Deciles are only meaningful in the wide regime;
  QUINTILES are the primary bucketing and deciles are reported where breadth
  allows.

Read-only from data/rev_cache.  No network.
Run:  /Users/blackbox/TradeFinder/.venv/bin/python scripts/research/probe_xs_volume_surprise.py
      (from /Users/blackbox/TradeFinder/backend)
"""
from __future__ import print_function

import glob
import json
import math
import os
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT_DATE = "2025-01-01"

VOL_WIN = 20            # trailing sessions for the median-volume baseline
VOL_MIN_OBS = 15        # need this many positive-volume bars inside the window
MIN_XS = 20             # minimum cross-section per date (mandatory)
N_QUINTILES = 5
N_DECILES = 10
DECILE_MIN_XS = 50      # only cut deciles when the cross-section can support it

COST_SINGLE = 0.25      # % round-trip floor, long-only single leg
COST_SPREAD = 0.50      # % round-trip floor, long/short spread

CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}
ETFS = {"SPY", "QQQ", "IWM", "SMH", "XLE", "XLF"}

MAX_ABS_RET = 3.0       # sanity guard: drop |daily ret| > 300% (bad cache bars)


# --------------------------------------------------------------------- stats
def mean(v: Sequence[float]) -> float:
    return sum(v) / float(len(v)) if v else float("nan")


def median(v: Sequence[float]) -> float:
    if not v:
        return float("nan")
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def sdev(v: Sequence[float]) -> float:
    if len(v) < 2:
        return float("nan")
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def clustered_se(vals: Sequence[float], dates: Sequence[str]) -> float:
    """Cluster-robust se of a sample mean, clusters = session dates.

    Var(xbar) = (1/n^2) * sum_over_clusters ( sum_{i in cluster} (x_i - xbar) )^2
    """
    n = len(vals)
    if n < 2:
        return float("nan")
    m = mean(vals)
    per = defaultdict(float)  # type: Dict[str, float]
    for x, d in zip(vals, dates):
        per[d] += x - m
    if len(per) < 2:
        return float("nan")
    return math.sqrt(sum(s * s for s in per.values())) / n


def clustered_se_diff(va: Sequence[float], da: Sequence[str],
                      vb: Sequence[float], db: Sequence[str]) -> float:
    """Date-clustered se of mean(a) - mean(b), a and b sharing dates."""
    na, nb = len(va), len(vb)
    if na < 2 or nb < 2:
        return float("nan")
    ma, mb = mean(va), mean(vb)
    per = defaultdict(float)  # type: Dict[str, float]
    for x, d in zip(va, da):
        per[d] += (x - ma) / na
    for x, d in zip(vb, db):
        per[d] -= (x - mb) / nb
    if len(per) < 2:
        return float("nan")
    return math.sqrt(sum(s * s for s in per.values()))


def drop_influential(vals: Sequence[float], dates: Sequence[str], k: int
                     ) -> Tuple[float, int, int, List[str]]:
    """Mean after removing the k dates contributing most to the mean."""
    if not vals:
        return float("nan"), 0, 0, []
    m = mean(vals)
    contrib = defaultdict(float)  # type: Dict[str, float]
    for x, d in zip(vals, dates):
        contrib[d] += x - m
    worst = sorted(contrib, key=lambda d: abs(contrib[d]), reverse=True)[:k]
    bad = set(worst)
    keep = [(x, d) for x, d in zip(vals, dates) if d not in bad]
    if len(keep) < 2:
        return float("nan"), 0, 0, worst
    kv = [x for x, _ in keep]
    return mean(kv), len(kv), len(set(d for _, d in keep)), worst


def fmt(x: float, w: int = 7, p: int = 3) -> str:
    if x != x:
        return "n/a".rjust(w)
    return ("%*.*f" % (w, p, x))


class Cell(object):
    """Pooled symbol-day statistics with date-clustered inference."""

    def __init__(self, vals: Sequence[float], dates: Sequence[str], horizon: int = 1):
        self.n = len(vals)
        self.horizon = horizon
        self.n_dates = len(set(dates))
        if self.n == 0:
            self.mean = self.median = self.sd = self.se = self.cse = float("nan")
            return
        self.mean = mean(vals) * 100.0
        self.median = median(vals) * 100.0
        self.sd = sdev(vals) * 100.0
        self.se = (self.sd / math.sqrt(self.n)) if self.n > 1 else float("nan")
        self.cse = clustered_se(vals, dates) * 100.0 * math.sqrt(horizon)

    @property
    def t_naive(self) -> float:
        if not self.se or self.se != self.se or self.se == 0:
            return float("nan")
        return self.mean / self.se

    @property
    def t_clustered(self) -> float:
        if not self.cse or self.cse != self.cse or self.cse == 0:
            return float("nan")
        return self.mean / self.cse


class DateSeries(object):
    """A date-level portfolio series.  Its plain t IS the date-clustered t."""

    def __init__(self, by_date: Dict[str, float], horizon: int = 1):
        self.dates = sorted(by_date)
        self.vals = [by_date[d] for d in self.dates]
        self.n = len(self.vals)
        if self.n == 0:
            self.mean = self.median = self.sd = self.se = float("nan")
            return
        self.mean = mean(self.vals) * 100.0
        self.median = median(self.vals) * 100.0
        self.sd = sdev(self.vals) * 100.0
        self.se = (self.sd / math.sqrt(self.n)) * math.sqrt(horizon) if self.n > 1 else float("nan")

    @property
    def t(self) -> float:
        if not self.se or self.se != self.se or self.se == 0:
            return float("nan")
        return self.mean / self.se

    @property
    def hit(self) -> float:
        if not self.n:
            return float("nan")
        return 100.0 * sum(1 for v in self.vals if v > 0) / self.n


# ---------------------------------------------------------------- data layer
def load_symbol(path: str) -> List[dict]:
    with open(path) as fh:
        blob = json.load(fh)
    bars = list(blob.get("bars", {}).values())
    bars.sort(key=lambda b: b["time"])
    return bars


def build_records(sym: str, bars: List[dict]) -> List[dict]:
    """One record per usable signal day t."""
    out = []  # type: List[dict]
    n = len(bars)
    if n < VOL_WIN + 3:
        return out
    for i in range(VOL_WIN, n - 1):
        b = bars[i]
        prev = bars[i - 1]
        nxt = bars[i + 1]
        try:
            c = float(b["c"]); pc = float(prev["c"]); v = float(b["v"])
            no = float(nxt["o"]); nc = float(nxt["c"])
        except (TypeError, ValueError, KeyError):
            continue
        if c <= 0 or pc <= 0 or no <= 0 or nc <= 0 or v <= 0:
            continue
        win = [float(x["v"]) for x in bars[i - VOL_WIN:i] if x.get("v") and float(x["v"]) > 0]
        if len(win) < VOL_MIN_OBS:
            continue
        base = median(win)
        if base <= 0:
            continue
        ret = c / pc - 1.0
        if abs(ret) > MAX_ABS_RET:
            continue
        if ret == 0.0:
            continue                      # no branch; drop
        fwd1cc = nc / c - 1.0
        fwd1oc = nc / no - 1.0
        gap = no / c - 1.0
        if abs(fwd1cc) > MAX_ABS_RET:
            continue
        fwd5cc = float("nan")
        if i + 5 < n:
            c5 = float(bars[i + 5]["c"])
            if c5 > 0:
                r5 = c5 / c - 1.0
                if abs(r5) <= MAX_ABS_RET:
                    fwd5cc = r5
        out.append({
            "sym": sym, "date": b["date"], "close": c, "vol": v,
            "surprise": v / base, "ret": ret, "up": ret > 0,
            "fwd1cc": fwd1cc, "fwd1oc": fwd1oc, "fwd5cc": fwd5cc, "gap": gap,
        })
    return out


def bucketise(sorted_recs: List[dict], key: str, nb: int) -> None:
    """Assign fractional rank and bucket index in-place (0 = lowest)."""
    n = len(sorted_recs)
    for i, r in enumerate(sorted_recs):
        fr = (i + 0.5) / n
        r[key + "_fr"] = fr
        r[key] = min(nb - 1, int(fr * nb))


def prepare_cross_sections(recs_by_date: Dict[str, List[dict]]) -> List[dict]:
    """Rank within date; attach quintile, decile, price tercile, xs-excess rets."""
    kept = []  # type: List[dict]
    for date in sorted(recs_by_date):
        day = recs_by_date[date]
        if len(day) < MIN_XS:
            continue
        day.sort(key=lambda r: (r["surprise"], r["sym"]))
        bucketise(day, "q", N_QUINTILES)
        if len(day) >= DECILE_MIN_XS:
            bucketise(day, "d", N_DECILES)
        else:
            for r in day:
                r["d"] = -1
                r["d_fr"] = float("nan")
        by_px = sorted(day, key=lambda r: (r["close"], r["sym"]))
        for i, r in enumerate(by_px):
            r["ptier"] = min(2, int(((i + 0.5) / len(by_px)) * 3))
        for fld in ("fwd1cc", "fwd1oc", "fwd5cc", "gap"):
            vals = [r[fld] for r in day if r[fld] == r[fld]]
            m = mean(vals) if vals else float("nan")
            for r in day:
                r[fld + "_x"] = (r[fld] - m) if (r[fld] == r[fld] and m == m) else float("nan")
        r_up = [r for r in day if r["up"]]
        r_dn = [r for r in day if not r["up"]]
        for r in day:
            r["n_xs"] = len(day)
            r["n_up"] = len(r_up)
            r["n_dn"] = len(r_dn)
        kept.extend(day)
    return kept


# ------------------------------------------------------------------- tables
def sample_of(date: str) -> str:
    return "TRAIN" if date < SPLIT_DATE else "TEST"


def regime_of(date: str) -> str:
    if date < SPLIT_DATE:
        return "TRAIN 2022-2024 (narrow)"
    if date < "2026-01-01":
        return "TEST-A 2025 (narrow)"
    return "TEST-B 2026 (wide)"


def bucket_table(rows: List[dict], bfield: str, nb: int, fld: str,
                 horizon: int, out: List[str], label: str) -> Dict[Tuple[str, int], Cell]:
    """Per (branch, bucket) table on the cross-sectionally demeaned return."""
    out.append("")
    out.append("  " + label)
    out.append("    %-6s %-8s %6s %6s %8s %8s %8s %8s %8s %8s %7s %7s"
               % ("branch", "bucket", "n", "dates", "mean_x", "med_x", "mean_raw",
                  "sd", "cse", "t_clust", "t_naive", "|ret|%"))
    cells = {}  # type: Dict[Tuple[str, int], Cell]
    for branch, want_up in (("UP", True), ("DOWN", False)):
        for b in range(nb):
            sel = [r for r in rows if r["up"] == want_up and r[bfield] == b
                   and r[fld + "_x"] == r[fld + "_x"]]
            if not sel:
                continue
            vx = [r[fld + "_x"] for r in sel]
            vr = [r[fld] for r in sel]
            ds = [r["date"] for r in sel]
            c = Cell(vx, ds, horizon)
            cells[(branch, b)] = c
            aret = mean([abs(r["ret"]) for r in sel]) * 100.0
            out.append("    %-6s %-8s %6d %6d %8s %8s %8s %8s %8s %8s %7s %7s"
                       % (branch, "B%d" % (b + 1), c.n, c.n_dates,
                          fmt(c.mean), fmt(c.median), fmt(mean(vr) * 100.0),
                          fmt(c.sd, 8, 2), fmt(c.cse), fmt(c.t_clustered, 8, 2),
                          fmt(c.t_naive, 7, 2), fmt(aret, 7, 2)))
    return cells


def top_minus_bottom(rows: List[dict], bfield: str, nb: int, fld: str,
                     want_up: bool, horizon: int) -> Tuple[Cell, float, float]:
    hi = [r for r in rows if r["up"] == want_up and r[bfield] == nb - 1
          and r[fld + "_x"] == r[fld + "_x"]]
    lo = [r for r in rows if r["up"] == want_up and r[bfield] == 0
          and r[fld + "_x"] == r[fld + "_x"]]
    if len(hi) < 2 or len(lo) < 2:
        return None, float("nan"), float("nan")
    vh = [r[fld + "_x"] for r in hi]
    vl = [r[fld + "_x"] for r in lo]
    diff = (mean(vh) - mean(vl)) * 100.0
    se = clustered_se_diff(vh, [r["date"] for r in hi],
                           vl, [r["date"] for r in lo]) * 100.0 * math.sqrt(horizon)
    t = diff / se if se and se == se and se != 0 else float("nan")
    return None, diff, t


def asym_spread_series(rows: List[dict], bfield: str, nb: int, fld: str,
                       ptier: Optional[int] = None) -> Dict[str, float]:
    """Date-level series: mean(top-bucket & DOWN) - mean(top-bucket & UP)."""
    by_date_dn = defaultdict(list)  # type: Dict[str, List[float]]
    by_date_up = defaultdict(list)  # type: Dict[str, List[float]]
    for r in rows:
        if r[bfield] != nb - 1:
            continue
        if ptier is not None and r["ptier"] != ptier:
            continue
        v = r[fld]
        if v != v:
            continue
        (by_date_up if r["up"] else by_date_dn)[r["date"]].append(v)
    out = {}  # type: Dict[str, float]
    for d in set(by_date_dn) & set(by_date_up):
        out[d] = mean(by_date_dn[d]) - mean(by_date_up[d])
    return out


def leg_series(rows: List[dict], bfield: str, nb: int, fld: str, want_up: bool,
               bucket: Optional[int] = None, ptier: Optional[int] = None
               ) -> Dict[str, float]:
    b = (nb - 1) if bucket is None else bucket
    by_date = defaultdict(list)  # type: Dict[str, List[float]]
    for r in rows:
        if r[bfield] != b or r["up"] != want_up:
            continue
        if ptier is not None and r["ptier"] != ptier:
            continue
        v = r[fld]
        if v == v:
            by_date[r["date"]].append(v)
    return dict((d, mean(vs)) for d, vs in by_date.items())


def fama_macbeth(rows: List[dict], fld: str, want_up: bool,
                 min_n: int = 5) -> Tuple[float, float, int]:
    """Per-date OLS slope of xs-excess return on (fractional rank - 0.5).

    Returns (mean slope in % per unit rank, t, n_dates).  The FM t is
    date-clustered by construction.
    """
    by_date = defaultdict(list)  # type: Dict[str, List[Tuple[float, float]]]
    for r in rows:
        if r["up"] != want_up:
            continue
        y = r[fld + "_x"]
        x = r["q_fr"]
        if y != y or x != x:
            continue
        by_date[r["date"]].append((x - 0.5, y))
    slopes = []
    for d in sorted(by_date):
        pts = by_date[d]
        if len(pts) < min_n:
            continue
        mx = mean([p[0] for p in pts])
        my = mean([p[1] for p in pts])
        num = sum((p[0] - mx) * (p[1] - my) for p in pts)
        den = sum((p[0] - mx) ** 2 for p in pts)
        if den <= 0:
            continue
        slopes.append(num / den)
    if len(slopes) < 5:
        return float("nan"), float("nan"), len(slopes)
    m = mean(slopes) * 100.0
    se = sdev(slopes) * 100.0 / math.sqrt(len(slopes))
    return m, (m / se if se else float("nan")), len(slopes)


def is_monotone(cells: Dict[Tuple[str, int], Cell], branch: str, nb: int) -> Optional[bool]:
    seq = []
    for b in range(nb):
        c = cells.get((branch, b))
        if c is None or c.mean != c.mean:
            return None
        seq.append(c.mean)
    up = all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))
    dn = all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))
    return up or dn


# --------------------------------------------------------------------- main
def main() -> None:
    out = []  # type: List[str]
    files = sorted(glob.glob(os.path.join(CACHE, "*_1day.json")))

    universes = {}  # type: Dict[str, List[dict]]
    all_recs_full = []   # includes ETFs, for the sensitivity run
    all_recs = []        # primary universe
    sym_span = {}
    for f in files:
        sym = os.path.basename(f)[: -len("_1day.json")]
        if sym in CRYPTO:
            continue
        bars = load_symbol(f)
        if not bars:
            continue
        recs = build_records(sym, bars)
        if not recs:
            continue
        sym_span[sym] = (recs[0]["date"], recs[-1]["date"], len(recs))
        all_recs_full.extend(recs)
        if sym not in ETFS:
            all_recs.extend(recs)

    out.append("=" * 108)
    out.append("xs_volume_surprise -- CROSS-SECTIONAL volume surprise by decile/quintile, split by day sign")
    out.append("=" * 108)
    out.append("signal : surprise = today_volume / median(prior %d sessions' volume)   [causal, unit-free]" % VOL_WIN)
    out.append("rank   : percentile of surprise against EVERY OTHER SYMBOL on the same date")
    out.append("branch : UP  = own close-to-close return > 0 that day")
    out.append("         DOWN= own close-to-close return < 0 that day")
    out.append("outcome: next-day close-to-close (primary), cross-sectionally DEMEANED (raw minus")
    out.append("         that date's cross-sectional mean) so the market move is netted out")
    out.append("universe: individual equities; crypto and index/sector ETFs excluded (sensitivity run adds ETFs)")
    out.append("split  : TRAIN signal dates < %s, TEST >= %s.  Nothing is fit on either." % (SPLIT_DATE, SPLIT_DATE))
    out.append("se     : CLUSTERED BY DATE everywhere.  t_naive shown only to size the lie.")
    out.append("")

    by_date = defaultdict(list)  # type: Dict[str, List[dict]]
    for r in all_recs:
        by_date[r["date"]].append(r)
    raw_dates = len(by_date)
    rows = prepare_cross_sections(by_date)

    by_date_f = defaultdict(list)  # type: Dict[str, List[dict]]
    for r in all_recs_full:
        by_date_f[r["date"]].append(r)
    rows_full = prepare_cross_sections(by_date_f)

    dates_kept = sorted(set(r["date"] for r in rows))
    out.append("-" * 108)
    out.append("SAMPLE")
    out.append("-" * 108)
    out.append("  symbols with a usable signal series : %d" % len(sym_span))
    out.append("  symbol-days built                   : %d" % len(all_recs))
    out.append("  dates before the cross-section filter: %d" % raw_dates)
    out.append("  DATES SURVIVING min-cross-section>=%d : %d   (%d dropped)"
               % (MIN_XS, len(dates_kept), raw_dates - len(dates_kept)))
    out.append("  symbol-days surviving               : %d" % len(rows))
    out.append("")
    reg = defaultdict(list)  # type: Dict[str, List[str]]
    for d in dates_kept:
        reg[regime_of(d)].append(d)
    out.append("  %-28s %7s %12s %12s %10s" % ("regime", "dates", "first", "last", "med breadth"))
    for k in sorted(reg):
        ds = reg[k]
        br = median([len([r for r in rows if r["date"] == d]) for d in ds[:: max(1, len(ds) // 40)]])
        out.append("  %-28s %7d %12s %12s %10.0f" % (k, len(ds), ds[0], ds[-1], br))
    out.append("")
    out.append("  NOTE the cache is bimodal: a 22-name cross-section from 2022-01 to 2025-08, a data gap,")
    out.append("  then ~183 names from 2026-05 to 2026-09.  Deciles are only cut where breadth >= %d," % DECILE_MIN_XS)
    out.append("  so QUINTILES are primary and deciles are reported for the wide regime only.")
    out.append("  Pre-2026 closes are split-adjusted, so the price-tier check is most trustworthy in TEST-B.")

    samples = [
        ("TRAIN", [r for r in rows if r["date"] < SPLIT_DATE]),
        ("TEST", [r for r in rows if r["date"] >= SPLIT_DATE]),
        ("TEST-A 2025 narrow", [r for r in rows if SPLIT_DATE <= r["date"] < "2026-01-01"]),
        ("TEST-B 2026 wide", [r for r in rows if r["date"] >= "2026-01-01"]),
    ]

    # ------------------------------------------------ 1. quintile tables
    out.append("")
    out.append("=" * 108)
    out.append("1. QUINTILE TABLE -- next-day close-to-close, cross-sectionally demeaned (%)")
    out.append("   B1 = lowest volume surprise ... B5 = highest.  Ranked against the WHOLE cross-section,")
    out.append("   then split by the sign of the day's own return.")
    out.append("=" * 108)
    qcells = {}
    for name, sub in samples:
        if not sub:
            continue
        cells = bucket_table(sub, "q", N_QUINTILES, "fwd1cc", 1, out,
                             "%s   (%d symbol-days, %d dates)"
                             % (name, len(sub), len(set(r["date"] for r in sub))))
        qcells[name] = cells
        for branch in ("UP", "DOWN"):
            mono = is_monotone(cells, branch, N_QUINTILES)
            _, d51, t51 = top_minus_bottom(sub, "q", N_QUINTILES, "fwd1cc", branch == "UP", 1)
            fm, fmt_, nd = fama_macbeth(sub, "fwd1cc", branch == "UP")
            out.append("      %-4s  monotone across B1..B5: %-5s | B5-B1 = %s%% (clustered t %s)"
                       " | Fama-MacBeth slope %s%% per full rank (t %s, %d dates)"
                       % (branch, {True: "YES", False: "no", None: "n/a"}[mono],
                          fmt(d51, 6, 3), fmt(t51, 5, 2), fmt(fm, 6, 3), fmt(fmt_, 5, 2), nd))

    # ------------------------------------------------ 2. decile table (wide only)
    wide = [r for r in rows if r["d"] >= 0]
    out.append("")
    out.append("=" * 108)
    out.append("2. DECILE TABLE -- only dates whose cross-section is >= %d names (the 2026 wide regime)" % DECILE_MIN_XS)
    out.append("=" * 108)
    if wide:
        dcells = bucket_table(wide, "d", N_DECILES, "fwd1cc", 1, out,
                              "wide-regime dates (%d symbol-days, %d dates) -- ALL of these are TEST"
                              % (len(wide), len(set(r["date"] for r in wide))))
        for branch in ("UP", "DOWN"):
            mono = is_monotone(dcells, branch, N_DECILES)
            _, d101, t101 = top_minus_bottom(wide, "d", N_DECILES, "fwd1cc", branch == "UP", 1)
            out.append("      %-4s  monotone across D1..D10: %-5s | D10-D1 = %s%% (clustered t %s)"
                       % (branch, {True: "YES", False: "no", None: "n/a"}[mono],
                          fmt(d101, 6, 3), fmt(t101, 5, 2)))
    else:
        out.append("  no dates with breadth >= %d" % DECILE_MIN_XS)

    # ------------------------------------------------ 3. headline asymmetry trade
    out.append("")
    out.append("=" * 108)
    out.append("3. THE ASYMMETRY TRADE -- long top-quintile-surprise DOWN names, short top-quintile-surprise UP")
    out.append("   names, formed at the close of date t, held to the close of t+1.  Date-level portfolio,")
    out.append("   so the plain t of the series IS the date-clustered t.  Cost floor for a spread: %.2f%%." % COST_SPREAD)
    out.append("=" * 108)
    out.append("  %-22s %6s %9s %9s %9s %9s %8s %9s" %
               ("sample", "dates", "mean%", "median%", "sd", "se", "t", "hit%"))
    head = {}
    for name, sub in samples:
        if not sub:
            continue
        ser = DateSeries(asym_spread_series(sub, "q", N_QUINTILES, "fwd1cc"))
        head[name] = ser
        out.append("  %-22s %6d %9s %9s %9s %9s %8s %9s"
                   % (name, ser.n, fmt(ser.mean, 9, 4), fmt(ser.median, 9, 4),
                      fmt(ser.sd, 9, 3), fmt(ser.se, 9, 4), fmt(ser.t, 8, 2),
                      fmt(ser.hit, 9, 1)))
    out.append("")
    out.append("  Same trade, pooled over symbol-days with a date-clustered se of the difference:")
    out.append("  %-22s %6s %8s %8s %9s %8s %8s" %
               ("sample", "n_dn", "n_up", "diff%", "cse", "t_clust", "t_naive"))
    for name, sub in samples:
        if not sub:
            continue
        hi_dn = [r for r in sub if r["q"] == N_QUINTILES - 1 and not r["up"]]
        hi_up = [r for r in sub if r["q"] == N_QUINTILES - 1 and r["up"]]
        if len(hi_dn) < 2 or len(hi_up) < 2:
            continue
        a = [r["fwd1cc"] for r in hi_dn]
        b = [r["fwd1cc"] for r in hi_up]
        diff = (mean(a) - mean(b)) * 100.0
        cse = clustered_se_diff(a, [r["date"] for r in hi_dn],
                                b, [r["date"] for r in hi_up]) * 100.0
        nse = math.sqrt(sdev(a) ** 2 / len(a) + sdev(b) ** 2 / len(b)) * 100.0
        out.append("  %-22s %6d %8d %8s %9s %8s %8s"
                   % (name, len(hi_dn), len(hi_up), fmt(diff, 8, 4), fmt(cse, 9, 4),
                      fmt(diff / cse if cse else float("nan"), 8, 2),
                      fmt(diff / nse if nse else float("nan"), 8, 2)))

    # ------------------------------------------ 3b. how much clustering costs
    out.append("")
    out.append("  How much the date-clustering actually costs, on RAW (undemeaned) bucket returns --")
    out.append("  this is where same-day correlation lives; the demeaned series above has the common")
    out.append("  date factor already removed, which is why its two t's sit close together.")
    out.append("  %-22s %-20s %7s %7s %9s %9s %8s %8s %7s"
               % ("sample", "bucket", "n", "dates", "mean%", "se_naive", "se_clust", "t_naive", "t_clust"))
    for name, sub in samples:
        if not sub:
            continue
        for lbl, wu in (("Q5 surprise & DOWN", False), ("Q5 surprise & UP", True),
                        ("Q1 surprise & DOWN", False)):
            b = 0 if lbl.startswith("Q1") else N_QUINTILES - 1
            sel = [r for r in sub if r["q"] == b and r["up"] == wu]
            if len(sel) < 2:
                continue
            c = Cell([r["fwd1cc"] for r in sel], [r["date"] for r in sel], 1)
            out.append("  %-22s %-20s %7d %7d %9s %9s %8s %8s %7s"
                       % (name, lbl, c.n, c.n_dates, fmt(c.mean, 9, 4),
                          fmt(c.se, 9, 4), fmt(c.cse, 8, 4),
                          fmt(c.t_naive, 8, 2), fmt(c.t_clustered, 7, 2)))

    # ------------------------------------------------ 4. single legs
    out.append("")
    out.append("=" * 108)
    out.append("4. THE TWO LEGS ALONE (raw next-day return, not demeaned).  Long-only cost floor %.2f%%." % COST_SINGLE)
    out.append("=" * 108)
    out.append("  %-22s %-18s %6s %9s %9s %9s %8s %8s" %
               ("sample", "leg", "dates", "mean%", "median%", "se", "t", "hit%"))
    for name, sub in samples:
        if not sub:
            continue
        for lbl, wu in (("Q5 surprise & DOWN", False), ("Q5 surprise & UP", True)):
            ser = DateSeries(leg_series(sub, "q", N_QUINTILES, "fwd1cc", wu))
            out.append("  %-22s %-18s %6d %9s %9s %9s %8s %8s"
                       % (name, lbl, ser.n, fmt(ser.mean, 9, 4), fmt(ser.median, 9, 4),
                          fmt(ser.se, 9, 4), fmt(ser.t, 8, 2), fmt(ser.hit, 8, 1)))

    # ------------------------------------------------ 5. price tiers
    out.append("")
    out.append("=" * 108)
    out.append("5. PRICE-TIER CHECK (mandatory).  Within-date price terciles; T1 = cheapest.")
    out.append("   If the effect lives only in T1 it is a bid-ask-bounce artifact, not an edge.")
    out.append("=" * 108)
    tier_lines = []
    for name, sub in samples:
        if not sub:
            continue
        px_edges = []
        for t in range(3):
            pv = [r["close"] for r in sub if r["ptier"] == t]
            px_edges.append((min(pv), max(pv), median(pv)) if pv else (float("nan"),) * 3)
        out.append("")
        out.append("  %s  (tier median price: T1 $%.2f | T2 $%.2f | T3 $%.2f)"
                   % (name, px_edges[0][2], px_edges[1][2], px_edges[2][2]))
        out.append("    %-6s %7s %9s %9s %9s %8s %9s %9s %9s"
                   % ("tier", "dates", "spread%", "median%", "se", "t", "legDN%", "legUP%", "medPx$"))
        for t in range(3):
            ser = DateSeries(asym_spread_series(sub, "q", N_QUINTILES, "fwd1cc", ptier=t))
            dn = DateSeries(leg_series(sub, "q", N_QUINTILES, "fwd1cc", False, ptier=t))
            up = DateSeries(leg_series(sub, "q", N_QUINTILES, "fwd1cc", True, ptier=t))
            line = ("    %-6s %7d %9s %9s %9s %8s %9s %9s %9.2f"
                    % ("T%d" % (t + 1), ser.n, fmt(ser.mean, 9, 4), fmt(ser.median, 9, 4),
                       fmt(ser.se, 9, 4), fmt(ser.t, 8, 2), fmt(dn.mean, 9, 4),
                       fmt(up.mean, 9, 4), px_edges[t][2]))
            out.append(line)
            if name in ("TRAIN", "TEST"):
                tier_lines.append("%s T%d: spread %s%% (t %s, %d dates, med px $%.2f)"
                                  % (name, t + 1, fmt(ser.mean, 0, 4).strip(),
                                     fmt(ser.t, 0, 2).strip(), ser.n, px_edges[t][2]))

    # ------------------------------------------------ 6. influential dates
    out.append("")
    out.append("=" * 108)
    out.append("6. DROP THE 5 MOST INFLUENTIAL DATES from the headline spread series")
    out.append("=" * 108)
    out.append("  %-22s %8s %10s %8s %10s %8s   %s"
               % ("sample", "dates", "mean%", "t", "mean_d5%", "t_d5", "dates dropped"))
    robust = {}
    for name, sub in samples:
        if not sub:
            continue
        s = asym_spread_series(sub, "q", N_QUINTILES, "fwd1cc")
        ser = DateSeries(s)
        vals = [s[d] for d in sorted(s)]
        dts = sorted(s)
        m5, nk, ndk, worst = drop_influential(vals, dts, 5)
        keep = [(v, d) for v, d in zip(vals, dts) if d not in set(worst)]
        kv = [v for v, _ in keep]
        se5 = sdev(kv) / math.sqrt(len(kv)) * 100.0 if len(kv) > 1 else float("nan")
        t5 = (m5 * 100.0 / se5) if se5 else float("nan")
        robust[name] = (m5 * 100.0, t5)
        out.append("  %-22s %8d %10s %8s %10s %8s   %s"
                   % (name, ser.n, fmt(ser.mean, 10, 4), fmt(ser.t, 8, 2),
                      fmt(m5 * 100.0, 10, 4), fmt(t5, 8, 2), ",".join(sorted(worst))))

    # ------------------------------------------------ 7. robustness grid
    out.append("")
    out.append("=" * 108)
    out.append("7. ROBUSTNESS GRID -- alternate outcomes, alternate ranking, ETFs back in")
    out.append("=" * 108)
    out.append("  (a) alternate forward windows for the same headline spread")
    out.append("  %-22s %-26s %7s %10s %10s %8s"
               % ("sample", "outcome", "dates", "mean%", "median%", "t"))
    for name, sub in samples:
        if not sub:
            continue
        for fld, lbl, hz in (("fwd1cc", "t close -> t+1 close", 1),
                             ("fwd1oc", "t+1 open -> t+1 close", 1),
                             ("gap", "t close -> t+1 open (gap)", 1),
                             ("fwd5cc", "t close -> t+5 close", 5)):
            ser = DateSeries(asym_spread_series(sub, "q", N_QUINTILES, fld), horizon=hz)
            out.append("  %-22s %-26s %7d %10s %10s %8s"
                       % (name, lbl, ser.n, fmt(ser.mean, 10, 4),
                          fmt(ser.median, 10, 4), fmt(ser.t, 8, 2)))
    out.append("")
    out.append("  (b) alternate construction: rank surprise WITHIN each branch, then top-vs-bottom quintile")
    out.append("  %-22s %-8s %8s %10s %10s %8s"
               % ("sample", "branch", "dates", "B5-B1 %", "median%", "t"))
    for name, sub in samples:
        if not sub:
            continue
        bd = defaultdict(list)  # type: Dict[Tuple[str, bool], List[dict]]
        for r in sub:
            bd[(r["date"], r["up"])].append(r)
        for want_up, lbl in ((False, "DOWN"), (True, "UP")):
            per_date = {}
            for (d, u), grp in bd.items():
                if u != want_up or len(grp) < 10:
                    continue
                grp = sorted(grp, key=lambda r: (r["surprise"], r["sym"]))
                k = max(1, len(grp) // 5)
                hi = mean([r["fwd1cc"] for r in grp[-k:]])
                lo = mean([r["fwd1cc"] for r in grp[:k]])
                per_date[d] = hi - lo
            ser = DateSeries(per_date)
            out.append("  %-22s %-8s %8d %10s %10s %8s"
                       % (name, lbl, ser.n, fmt(ser.mean, 10, 4),
                          fmt(ser.median, 10, 4), fmt(ser.t, 8, 2)))
    out.append("")
    out.append("  (c) ETFs put back into the ranked cross-section (headline spread)")
    out.append("  %-22s %8s %10s %10s %8s" % ("sample", "dates", "mean%", "median%", "t"))
    for name, lo, hi in (("TRAIN", "0000", SPLIT_DATE), ("TEST", SPLIT_DATE, "9999")):
        sub = [r for r in rows_full if lo <= r["date"] < hi]
        ser = DateSeries(asym_spread_series(sub, "q", N_QUINTILES, "fwd1cc"))
        out.append("  %-22s %8d %10s %10s %8s"
                   % (name, ser.n, fmt(ser.mean, 10, 4), fmt(ser.median, 10, 4),
                      fmt(ser.t, 8, 2)))

    # ------------------------------------------------ 8. what the buckets contain
    out.append("")
    out.append("=" * 108)
    out.append("8. WHAT THE BUCKETS ACTUALLY CONTAIN (sanity -- surprise ranks correlate with |move| and price)")
    out.append("=" * 108)
    out.append("  %-22s %-8s %8s %10s %10s %10s %10s"
               % ("sample", "bucket", "n", "med surp", "med |ret|%", "med px$", "med $vol(M)"))
    for name, sub in samples:
        if not sub:
            continue
        for b in range(N_QUINTILES):
            sel = [r for r in sub if r["q"] == b]
            if not sel:
                continue
            out.append("  %-22s %-8s %8d %10.2f %10.2f %10.2f %10.1f"
                       % (name, "B%d" % (b + 1), len(sel),
                          median([r["surprise"] for r in sel]),
                          median([abs(r["ret"]) for r in sel]) * 100.0,
                          median([r["close"] for r in sel]),
                          median([r["close"] * r["vol"] for r in sel]) / 1e6))

    # ------------------------------------------------ verdict
    out.append("")
    out.append("=" * 108)
    out.append("VERDICT INPUTS")
    out.append("=" * 108)
    tr = head.get("TRAIN")
    te = head.get("TEST")
    if tr and te:
        out.append("  headline spread  TRAIN %s%% (t %s, %d dates) -> TEST %s%% (t %s, %d dates)"
                   % (fmt(tr.mean, 0, 4).strip(), fmt(tr.t, 0, 2).strip(), tr.n,
                      fmt(te.mean, 0, 4).strip(), fmt(te.t, 0, 2).strip(), te.n))
        out.append("  spread cost floor %.2f%% : TRAIN %s | TEST %s"
                   % (COST_SPREAD,
                      "CLEARS" if tr.mean > COST_SPREAD else "FAILS",
                      "CLEARS" if te.mean > COST_SPREAD else "FAILS"))
        out.append("  TEST median trade %s%% vs mean %s%% (median is the honest one)"
                   % (fmt(te.median, 0, 4).strip(), fmt(te.mean, 0, 4).strip()))
    for name in ("TRAIN", "TEST"):
        c = qcells.get(name)
        if c:
            out.append("  monotone quintiles %-5s : UP %-4s  DOWN %-4s"
                       % (name,
                          str(is_monotone(c, "UP", N_QUINTILES)),
                          str(is_monotone(c, "DOWN", N_QUINTILES))))
    for ln in tier_lines:
        out.append("  price tier | " + ln)
    for name in ("TRAIN", "TEST"):
        if name in robust:
            out.append("  drop-5-dates %-5s : mean %s%% t %s"
                       % (name, fmt(robust[name][0], 0, 4).strip(),
                          fmt(robust[name][1], 0, 2).strip()))

    text = "\n".join(out)
    print(text)
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "probe_xs_volume_surprise_out.txt")
    with open(dest, "w") as fh:
        fh.write(text + "\n")
    print("\n[saved] " + dest)


if __name__ == "__main__":
    main()
