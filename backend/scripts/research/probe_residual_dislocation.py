"""Probe: residual_dislocation.

Question
--------
Estimate each symbol's 60-day rolling beta to SPY on daily bars, take the daily
residual return (actual - beta * SPY), standardise it into a z-score, and ask
whether extreme residuals revert or continue over the next 1 / 3 / 5 days.
Specifically: do extreme NEGATIVE residuals behave differently from extreme
POSITIVE ones, and does the answer depend on the sign of SPY that day?

Method notes (all deliberately conservative)
--------------------------------------------
* Beta is estimated STRICTLY CAUSALLY: the 60 returns used for day t end at
  t-1, so day t's own return never contaminates its own beta.
* z is the standardised regression residual: residuals are recomputed inside
  the 60-day estimation window with that same beta, and day t's residual is
  demeaned / divided by that window's residual sd.
* Forward returns are close-to-close from the signal day's close (an MOC fill
  on the signal day). A next-open-entry variant is printed as a robustness
  check because it changes tradeability.
* Standard errors are reported two ways. The NAIVE se = sd/sqrt(n) treats every
  (symbol, day) as independent, which is badly wrong here: on any given day all
  names share the market move. The DATE-CLUSTERED se is the standard
  cluster-robust variance of a sample mean, clustered on session date, and is
  the one used for the "more than 2 standard errors from zero" call. For the
  3d/5d horizons the observation windows overlap, so a further sqrt(k)
  inflation is also printed.
* Date concentration is checked directly: distinct session dates, plus the mean
  recomputed after dropping the 1 and 3 dates that contribute most to it.
* Outlier dependence is checked by dropping the top and bottom 5% of
  observations.

Split
-----
TRAIN = signal dates before 2025-01-01.  TEST = 2025-01-01 onward.
Nothing was tuned on TEST; bucket edges and windows were fixed up front.

Universe A ("core") = the 27 non-crypto, non-SPY symbols with >= 900 daily bars
(2022-01 .. 2025-08 or 2026-09). This is the only universe that has any TRAIN
data at all, so it carries the train/test comparison.
Universe B ("broad 2026") = the 178 movers-style names that only have 87 daily
bars (2026-05-01 .. 2026-09-03). With a 60-day warmup these can only produce
signals in the last ~27 sessions of the sample, so they are reported separately
and treated as a narrow-window side check, never as the headline.

Run:
    cd /Users/blackbox/TradeFinder/backend
    ../.venv/bin/python scripts/research/probe_residual_dislocation.py
"""
from __future__ import annotations

import glob
import json
import math
import os
from collections import defaultdict
from datetime import date as _date
from typing import Dict, List, Optional, Sequence, Tuple

DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "rev_cache")
)

BETA_N = 60           # trailing days for beta + residual sd (ends at t-1)
HORIZONS = (1, 3, 5)
SPLIT_DATE = "2025-01-01"
COST_PCT = 0.25       # round-trip slippage + spread proxy, in percent
CORE_MIN_BARS = 900

# Fixed up front, never tuned.
BUCKETS: List[Tuple[str, float, float]] = [
    ("z <= -2.0", -99.0, -2.0),
    ("-2.0 < z <= -1.0", -2.0, -1.0),
    ("-1.0 < z <= -0.5", -1.0, -0.5),
    ("-0.5 < z <  0.5", -0.5, 0.5),
    ("0.5 <= z <  1.0", 0.5, 1.0),
    ("1.0 <= z <  2.0", 1.0, 2.0),
    ("z >= 2.0", 2.0, 99.0),
]


# ---------------------------------------------------------------- data loading
def load_daily(path: str) -> List[dict]:
    with open(path) as fh:
        raw = json.load(fh)["bars"]
    bars = sorted(raw.values(), key=lambda b: b["time"])
    return [b for b in bars if b.get("c") and b["c"] > 0]


def to_ord(d: str) -> int:
    y, m, dd = d.split("-")
    return _date(int(y), int(m), int(dd)).toordinal()


# ------------------------------------------------------------------ statistics
def mean(v: Sequence[float]) -> float:
    return sum(v) / len(v) if v else float("nan")


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
    per: Dict[str, float] = defaultdict(float)
    for x, d in zip(vals, dates):
        per[d] += x - m
    if len(per) < 2:
        return float("nan")
    return math.sqrt(sum(s * s for s in per.values())) / n


def trimmed_mean(vals: Sequence[float], pct: float = 0.05) -> float:
    n = len(vals)
    k = int(n * pct)
    if n - 2 * k < 2:
        return float("nan")
    s = sorted(vals)
    return mean(s[k: n - k])


def drop_top_dates(vals: Sequence[float], dates: Sequence[str], k: int) -> Tuple[float, int]:
    """Mean after removing the k dates that contribute most to the mean."""
    m = mean(vals)
    contrib: Dict[str, float] = defaultdict(float)
    for x, d in zip(vals, dates):
        contrib[d] += x - m
    worst = sorted(contrib, key=lambda d: abs(contrib[d]), reverse=True)[:k]
    bad = set(worst)
    keep = [x for x, d in zip(vals, dates) if d not in bad]
    return (mean(keep) if len(keep) > 1 else float("nan")), len(keep)


class Stat:
    def __init__(self, vals: Sequence[float], dates: Sequence[str], horizon: int):
        self.n = len(vals)
        self.horizon = horizon
        self.dates = sorted(set(dates))
        self.n_dates = len(self.dates)
        if self.n == 0:
            self.mean = self.median = self.sd = self.se = self.cse = float("nan")
            self.trim = self.drop1 = self.drop3 = float("nan")
            return
        self.mean = mean(vals) * 100.0
        self.median = median(vals) * 100.0
        self.sd = sdev(vals) * 100.0
        self.se = (self.sd / math.sqrt(self.n)) if self.n > 1 else float("nan")
        self.cse = clustered_se(vals, dates) * 100.0
        self.trim = trimmed_mean(vals) * 100.0
        self.drop1 = drop_top_dates(vals, dates, 1)[0] * 100.0
        self.drop3 = drop_top_dates(vals, dates, 3)[0] * 100.0

    @property
    def cse_overlap(self) -> float:
        return self.cse * math.sqrt(self.horizon)

    @property
    def t_clustered(self) -> float:
        if not self.cse or self.cse != self.cse or self.cse == 0:
            return float("nan")
        return self.mean / self.cse_overlap


def fmt(x: float, w: int = 7, p: int = 3) -> str:
    if x != x:
        return "n/a".rjust(w)
    return f"{x:{w}.{p}f}"


# --------------------------------------------------------------- signal engine
def build_signals(sym: str, bars: List[dict], spy_by_date: Dict[str, dict]) -> List[dict]:
    """Return one record per (symbol, day) with a defined residual z-score."""
    rows = [b for b in bars if b["date"] in spy_by_date]
    if len(rows) < BETA_N + 2:
        return []
    dates = [b["date"] for b in rows]
    ords = [to_ord(d) for d in dates]
    closes = [float(b["c"]) for b in rows]
    opens = [float(b["o"]) for b in rows]
    spy_c = [float(spy_by_date[d]["c"]) for d in dates]

    # simple daily returns, index i is the return from i-1 to i (r[0] = nan)
    r_sym: List[Optional[float]] = [None]
    r_spy: List[Optional[float]] = [None]
    for i in range(1, len(rows)):
        r_sym.append(closes[i] / closes[i - 1] - 1.0)
        r_spy.append(spy_c[i] / spy_c[i - 1] - 1.0)

    out: List[dict] = []
    for t in range(BETA_N + 1, len(rows)):
        # window of 60 returns ending at t-1 -> strictly causal
        ys = r_sym[t - BETA_N: t]
        xs = r_spy[t - BETA_N: t]
        if any(v is None for v in ys) or any(v is None for v in xs):
            continue
        # calendar sanity: 60 trading days should not straddle a huge gap
        if ords[t] - ords[t - BETA_N] > 140:
            continue
        mx = mean(xs)
        my = mean(ys)
        var = sum((a - mx) ** 2 for a in xs)
        if var <= 0:
            continue
        beta = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / var
        resid_win = [b - beta * a for a, b in zip(xs, ys)]
        s_e = sdev(resid_win)
        if not s_e or s_e != s_e or s_e <= 0:
            continue
        m_e = mean(resid_win)
        resid = r_sym[t] - beta * r_spy[t]
        z = (resid - m_e) / s_e

        rec = {
            "sym": sym,
            "date": dates[t],
            "z": z,
            "resid": resid,
            "beta": beta,
            "r_sym": r_sym[t],
            "r_spy": r_spy[t],
            "spy_up": r_spy[t] > 0,
            "close": closes[t],
        }
        ok = False
        for k in HORIZONS:
            if t + k < len(rows) and (ords[t + k] - ords[t]) <= (2 * k + 6):
                rec["fwd%d" % k] = closes[t + k] / closes[t] - 1.0
                ok = True
        # next-open entry variant (1 bar later), same exit closes
        if t + 1 < len(rows) and (ords[t + 1] - ords[t]) <= 6:
            for k in HORIZONS:
                if t + k < len(rows) and (ords[t + k] - ords[t]) <= (2 * k + 6):
                    rec["op%d" % k] = closes[t + k] / opens[t + 1] - 1.0
        if ok:
            out.append(rec)
    return out


def bucket_of(z: float) -> str:
    if z <= -2.0:
        return BUCKETS[0][0]
    if z <= -1.0:
        return BUCKETS[1][0]
    if z <= -0.5:
        return BUCKETS[2][0]
    if z < 0.5:
        return BUCKETS[3][0]
    if z < 1.0:
        return BUCKETS[4][0]
    if z < 2.0:
        return BUCKETS[5][0]
    return BUCKETS[6][0]


# ------------------------------------------------------------------- reporting
HDR = ("bucket                 |     n | dates |    mean |  median |      sd |"
       "  se_nv | se_dt | t_dt |  trim5 |  -1dt |  -3dt")


def table(recs: List[dict], key: str, horizon: int,
          title: str) -> Tuple[List[str], List[float]]:
    lines = [title, HDR, "-" * len(HDR)]
    means = []
    for name, _, _ in BUCKETS:
        sub = [r for r in recs if r["bucket"] == name and key in r]
        vals = [r[key] for r in sub]
        dts = [r["date"] for r in sub]
        st = Stat(vals, dts, horizon)
        means.append(st.mean if st.n else float("nan"))
        lines.append(
            "%-22s | %5d | %5d | %s | %s | %s | %s | %s | %s | %s | %s | %s" % (
                name, st.n, st.n_dates,
                fmt(st.mean), fmt(st.median), fmt(st.sd),
                fmt(st.se, 6, 3), fmt(st.cse, 5, 3), fmt(st.t_clustered, 4, 1),
                fmt(st.trim, 6, 3), fmt(st.drop1, 6, 3), fmt(st.drop3, 6, 3),
            )
        )
    return lines, means


def monotone(means: List[float]) -> str:
    m = [x for x in means if x == x]
    if len(m) < 3:
        return "n/a"
    inc = all(m[i] <= m[i + 1] + 1e-12 for i in range(len(m) - 1))
    dec = all(m[i] >= m[i + 1] - 1e-12 for i in range(len(m) - 1))
    if inc:
        return "MONOTONE increasing in z (continuation)"
    if dec:
        return "MONOTONE decreasing in z (reversal)"
    # count direction changes
    signs = [1 if m[i + 1] > m[i] else -1 for i in range(len(m) - 1)]
    flips = sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])
    return "NOT monotone (%d direction flips across 7 buckets)" % flips


def spread_test(recs: List[dict], key: str, horizon: int) -> str:
    """mean(z<=-2) - mean(z>=2) with a date-clustered se of the difference."""
    a = [r for r in recs if r["bucket"] == BUCKETS[0][0] and key in r]
    b = [r for r in recs if r["bucket"] == BUCKETS[6][0] and key in r]
    if len(a) < 20 or len(b) < 20:
        return "  long/short spread: too few obs"
    va, da = [r[key] for r in a], [r["date"] for r in a]
    vb, db = [r[key] for r in b], [r["date"] for r in b]
    diff = (mean(va) - mean(vb)) * 100.0
    # clustered se of the difference: stack with +1/-1 weights
    n_a, n_b = len(va), len(vb)
    per: Dict[str, float] = defaultdict(float)
    ma, mb = mean(va), mean(vb)
    for x, d in zip(va, da):
        per[d] += (x - ma) / n_a
    for x, d in zip(vb, db):
        per[d] -= (x - mb) / n_b
    se = math.sqrt(sum(s * s for s in per.values())) * 100.0 * math.sqrt(horizon)
    t = diff / se if se > 0 else float("nan")
    shared = len(set(da) & set(db))
    return ("  long(z<=-2) minus short(z>=2) fwd%dd = %+.3f%%  se_dt=%.3f  t=%.1f"
            "  (n=%d/%d, dates %d/%d, shared %d)"
            % (horizon, diff, se, t, n_a, n_b, len(set(da)), len(set(db)), shared))


def diff_vs_neutral(recs: List[dict], bname: str, key: str, horizon: int) -> str:
    """mean(bucket) - mean(neutral -0.5<z<0.5), date-clustered se of the diff.

    This is the quantity that actually has to be non-zero for the z-score to be
    carrying information. A bucket can look great simply because the whole
    sample drifted that week.
    """
    a = [r for r in recs if r["bucket"] == bname and key in r]
    b = [r for r in recs if r["bucket"] == BUCKETS[3][0] and key in r]
    if len(a) < 20 or len(b) < 20:
        return "    %-18s too few obs" % bname
    va, da = [r[key] for r in a], [r["date"] for r in a]
    vb, db = [r[key] for r in b], [r["date"] for r in b]
    ma, mb = mean(va), mean(vb)
    per: Dict[str, float] = defaultdict(float)
    for x, d in zip(va, da):
        per[d] += (x - ma) / len(va)
    for x, d in zip(vb, db):
        per[d] -= (x - mb) / len(vb)
    se = math.sqrt(sum(s * s for s in per.values())) * 100.0 * math.sqrt(horizon)
    diff = (ma - mb) * 100.0
    t = diff / se if se > 0 else float("nan")
    return ("    %-18s n=%5d dates=%4d  bucket=%+7.3f%%  neutral=%+7.3f%%  "
            "EDGE=%+7.3f%%  se_dt=%5.3f  t=%+5.1f"
            % (bname, len(a), len(set(da)), ma * 100.0, mb * 100.0, diff, se, t))


def report(recs: List[dict], label: str, out: List[str]) -> None:
    out.append("")
    out.append("=" * 118)
    out.append("%s  |  n_signals=%d  distinct dates=%d  symbols=%d  span %s .. %s"
               % (label, len(recs), len(set(r["date"] for r in recs)),
                  len(set(r["sym"] for r in recs)),
                  min(r["date"] for r in recs) if recs else "-",
                  max(r["date"] for r in recs) if recs else "-"))
    out.append("=" * 118)
    if not recs:
        out.append("  (no observations)")
        return
    for k in HORIZONS:
        key = "fwd%d" % k
        lines, means = table(recs, key, k, "\n-- forward %d-day close-to-close return, %% --" % k)
        out.extend(lines)
        out.append("  shape: " + monotone(means))
        out.append(spread_test(recs, key, k))


def report_spy_split(recs: List[dict], label: str, out: List[str]) -> None:
    out.append("")
    out.append("#" * 118)
    out.append("%s  --  conditioned on the sign of SPY that day" % label)
    out.append("#" * 118)
    for up in (True, False):
        sub = [r for r in recs if r["spy_up"] == up]
        if not sub:
            continue
        tag = "SPY UP" if up else "SPY DOWN"
        out.append("")
        out.append(">>> %s day (n=%d, dates=%d)" % (tag, len(sub), len(set(r["date"] for r in sub))))
        lines, means = table(sub, "fwd1", 1, "-- forward 1-day close-to-close return, %% --")
        out.extend(lines)
        out.append("  shape: " + monotone(means))
        lines, means = table(sub, "fwd5", 5, "-- forward 5-day close-to-close return, %% --")
        out.extend(lines)
        out.append("  shape: " + monotone(means))


def main() -> None:
    spy_bars = load_daily(os.path.join(DATA_DIR, "SPY_1day.json"))
    spy_by_date = {b["date"]: b for b in spy_bars}

    core, broad = [], []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*_1day.json"))):
        sym = os.path.basename(path).replace("_1day.json", "")
        if sym == "SPY" or sym.endswith("USD"):
            continue
        bars = load_daily(path)
        if len(bars) < BETA_N + 3:
            continue
        sigs = build_signals(sym, bars, spy_by_date)
        for s in sigs:
            s["bucket"] = bucket_of(s["z"])
        (core if len(bars) >= CORE_MIN_BARS else broad).extend(sigs)

    out: List[str] = []
    out.append("RESIDUAL DISLOCATION PROBE")
    out.append("beta window = %d trading days ending at t-1 (strictly causal); "
               "z = standardised regression residual" % BETA_N)
    out.append("se_nv = naive sd/sqrt(n) | se_dt = date-clustered se | "
               "t_dt = mean / (se_dt * sqrt(horizon))  <- the honest t")
    out.append("trim5 = mean after dropping top and bottom 5%% of obs | "
               "-1dt / -3dt = mean after dropping the 1 / 3 most influential session dates")
    out.append("cost hurdle = %.2f%% per round trip" % COST_PCT)

    core_syms = sorted(set(r["sym"] for r in core))
    broad_syms = sorted(set(r["sym"] for r in broad))
    out.append("")
    out.append("UNIVERSE A (core, >=%d daily bars): %d symbols -> %s"
               % (CORE_MIN_BARS, len(core_syms), ", ".join(core_syms)))
    out.append("UNIVERSE B (broad 2026, short history): %d symbols" % len(broad_syms))

    train = [r for r in core if r["date"] < SPLIT_DATE]
    test = [r for r in core if r["date"] >= SPLIT_DATE]

    report(train, "UNIVERSE A -- TRAIN (signal date < %s)" % SPLIT_DATE, out)
    report(test, "UNIVERSE A -- TEST (signal date >= %s)" % SPLIT_DATE, out)
    report_spy_split(train, "UNIVERSE A -- TRAIN", out)
    report_spy_split(test, "UNIVERSE A -- TEST", out)
    report(broad, "UNIVERSE B -- broad 2026 movers universe (all TEST-era)", out)

    # ---- robustness: next-open entry on the headline buckets -----------------
    out.append("")
    out.append("=" * 118)
    out.append("ROBUSTNESS -- entry at NEXT day's open instead of the signal close "
               "(same exit closes)")
    out.append("=" * 118)
    for name, recs in (("TRAIN", train), ("TEST", test), ("BROAD-2026", broad)):
        for k in (1, 5):
            key = "op%d" % k
            for bname in (BUCKETS[0][0], BUCKETS[6][0]):
                sub = [r for r in recs if r["bucket"] == bname and key in r]
                if len(sub) < 20:
                    continue
                st = Stat([r[key] for r in sub], [r["date"] for r in sub], k)
                out.append("  %-10s %-18s open-entry fwd%dd: n=%5d dates=%4d "
                           "mean=%s median=%s se_dt=%s t_dt=%s"
                           % (name, bname, k, st.n, st.n_dates, fmt(st.mean),
                              fmt(st.median), fmt(st.cse, 6, 3), fmt(st.t_clustered, 5, 1)))

    # ---- how concentrated in time is each universe? -------------------------
    out.append("")
    out.append("DATE CONCENTRATION CHECK")
    for name, recs in (("TRAIN(A)", train), ("TEST(A)", test), ("BROAD(B)", broad)):
        for bname in (BUCKETS[0][0], BUCKETS[6][0]):
            sub = [r for r in recs if r["bucket"] == bname and "fwd1" in r]
            if not sub:
                continue
            dts = sorted(set(r["date"] for r in sub))
            per = defaultdict(int)
            for r in sub:
                per[r["date"]] += 1
            top = sorted(per.values(), reverse=True)[:3]
            out.append("  %-9s %-18s n=%5d dates=%4d span %s..%s  "
                       "busiest 3 dates hold %d obs (%.0f%% of n)"
                       % (name, bname, len(sub), len(dts), dts[0], dts[-1],
                          sum(top), 100.0 * sum(top) / len(sub)))

    # ---- THE FALSIFICATION TEST -------------------------------------------
    # TRAIN's only cell that clears 2 clustered se is: SPY-DOWN day, z >= 2,
    # fwd 1d. Everything below asks whether that cell is real:
    #   (a) is the edge OVER the neutral bucket, or just the day-after-a-down-
    #       day bounce that every bucket gets?
    #   (b) does it repeat out of sample?
    #   (c) is it stable year by year, or one regime?
    out.append("")
    out.append("=" * 118)
    out.append("EDGE OVER THE NEUTRAL BUCKET  (bucket mean minus the -0.5<z<0.5 mean, "
               "same sample; date-clustered se)")
    out.append("A z-bucket only carries information if THIS is non-zero.")
    out.append("=" * 118)
    for split_name, recs in (("TRAIN", train), ("TEST", test), ("BROAD-2026", broad)):
        for cond_name, sub in (("all days", recs),
                               ("SPY UP", [r for r in recs if r["spy_up"]]),
                               ("SPY DOWN", [r for r in recs if not r["spy_up"]])):
            for k in (1, 5):
                out.append("  %-11s %-9s fwd%dd" % (split_name, cond_name, k))
                for bname in (BUCKETS[0][0], BUCKETS[5][0], BUCKETS[6][0]):
                    out.append(diff_vs_neutral(sub, bname, "fwd%d" % k, k))

    out.append("")
    out.append("=" * 118)
    out.append("YEAR-BY-YEAR STABILITY of the headline TRAIN cell "
               "(SPY DOWN day, z>=2, fwd 1d close-to-close)")
    out.append("=" * 118)
    allrecs = core + broad
    for yr in sorted(set(r["date"][:4] for r in allrecs)):
        sub = [r for r in allrecs
               if r["date"][:4] == yr and not r["spy_up"]
               and r["bucket"] == BUCKETS[6][0] and "fwd1" in r]
        neu = [r for r in allrecs
               if r["date"][:4] == yr and not r["spy_up"]
               and r["bucket"] == BUCKETS[3][0] and "fwd1" in r]
        if len(sub) < 10:
            out.append("  %s  n=%4d  (too few)" % (yr, len(sub)))
            continue
        st = Stat([r["fwd1"] for r in sub], [r["date"] for r in sub], 1)
        nm = mean([r["fwd1"] for r in neu]) * 100.0 if neu else float("nan")
        out.append("  %s  n=%4d dates=%3d  mean=%s  median=%s  se_dt=%s  "
                   "neutral=%s  edge=%s"
                   % (yr, st.n, st.n_dates, fmt(st.mean), fmt(st.median),
                      fmt(st.cse, 6, 3), fmt(nm, 7, 3), fmt(st.mean - nm, 7, 3)))

    text = "\n".join(out)
    print(text)
    dest = os.path.join(os.path.dirname(__file__), "probe_residual_dislocation_out.txt")
    with open(dest, "w") as fh:
        fh.write(text + "\n")
    print("\n[saved] " + dest)


if __name__ == "__main__":
    main()
