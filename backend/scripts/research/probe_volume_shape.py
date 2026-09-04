#!/usr/bin/env python
"""
probe_volume_shape.py -- Does the SHAPE of a day's volume distribution predict returns?

Effect under test: participation TIMING (not level).
For each symbol-day we split regular-session (RTH) volume into
    first hour   09:30-10:30
    middle       10:30-15:00
    last hour    15:00-16:00
and form fractions f_first, f_mid, f_last (they sum to 1, so only 2 are free)
plus a tilt = f_last - f_first ("late-heavy" minus "early-heavy").

Outcomes
  (a) NEXT-day open-to-close return, conditioned on TODAY's full-day shape.
  (b) SAME-day 14:00-to-close return, conditioned on shape measured only up to 14:00
      (f14_first = 09:30-10:30 share of 09:30-14:00 volume,
       f14_late  = 13:00-14:00 share of 09:30-14:00 volume, tilt14 = late - first).

Controls
  Volume shape is mechanically related to how big the day's move was, so every
  table is also shown split by the SIGN of the conditioning-window return, and a
  pooled OLS adds the conditioning return and its absolute value plus symbol
  fixed effects, with date-clustered standard errors.

Methodology guardrails
  * Split by DATE. TRAIN = signal dates < 2025-01-01, TEST = >= 2025-01-01.
  * Bucketing uses a STRICTLY CAUSAL trailing z-score (prior 60 same-symbol
    sessions, min 30) against FIXED a-priori thresholds, so nothing is fit on
    either sample and TEST is never tuned on.
  * Every bucket reports n, mean, median, sd, se, distinct session dates,
    a 5%/5% trimmed mean, and the mean after dropping the single most
    influential date.
  * The B5-B1 spread is additionally tested as a DATE-LEVEL series so an effect
    carried by a handful of sessions cannot hide inside a big pooled n.

Data is read-only from data/rev_cache. No network.
Run:  /Users/blackbox/TradeFinder/.venv/bin/python scripts/research/probe_volume_shape.py
      (from /Users/blackbox/TradeFinder/backend)
"""
from __future__ import print_function

import glob
import json
import math
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT_DATE = "2025-01-01"
COST_BPS = 0.25          # per-trade slippage + spread proxy, in percent
TRAIL_WIN = 60           # trailing sessions for the causal z-score
TRAIL_MIN = 30

# fixed, a-priori z buckets (~quintiles of a standard normal). Never tuned.
Z_EDGES = [-1.0, -0.33, 0.33, 1.0]
BUCKET_LABELS = ["B1 z<=-1.0", "B2 -1..-.33", "B3 -.33..33", "B4 .33..1", "B5 z>1.0"]

CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}


# --------------------------------------------------------------------------
# tiny stats library (no numpy in this venv)
# --------------------------------------------------------------------------
def mean(xs):
    return sum(xs) / float(len(xs)) if xs else float("nan")


def median(xs):
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    m = n // 2
    return s[m] if n % 2 else 0.5 * (s[m - 1] + s[m])


def stdev(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    mu = mean(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / float(n - 1))


def sem(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    return stdev(xs) / math.sqrt(n)


def trimmed_mean(xs, frac=0.05):
    n = len(xs)
    if n < 20:
        return float("nan")
    k = int(n * frac)
    if k == 0:
        return mean(xs)
    return mean(sorted(xs)[k:n - k])


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def solve(A, b):
    """Gauss-Jordan with partial pivoting. Returns x with A x = b, or None."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            return None
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        M[c] = [v / pv for v in M[c]]
        for r in range(n):
            if r == c:
                continue
            f = M[r][c]
            if f:
                M[r] = [vr - f * vc for vr, vc in zip(M[r], M[c])]
    return [M[i][n] for i in range(n)]


def inv(A):
    n = len(A)
    cols = []
    for j in range(n):
        e = [1.0 if i == j else 0.0 for i in range(n)]
        x = solve(A, e)
        if x is None:
            return None
        cols.append(x)
    return [[cols[j][i] for j in range(n)] for i in range(n)]


def ols_cluster(X, y, groups):
    """OLS with cluster-robust (by group) SEs. Returns (beta, se) or (None, None)."""
    n = len(y)
    k = len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = solve(XtX, Xty)
    if beta is None:
        return None, None
    Vi = inv(XtX)
    if Vi is None:
        return beta, None
    resid = [y[i] - sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    acc = defaultdict(lambda: [0.0] * k)
    for i in range(n):
        g = acc[groups[i]]
        for a in range(k):
            g[a] += X[i][a] * resid[i]
    meat = [[0.0] * k for _ in range(k)]
    for g in acc.values():
        for a in range(k):
            for b in range(k):
                meat[a][b] += g[a] * g[b]
    G = len(acc)
    if G < 3 or n <= k:
        return beta, None
    scale = (G / float(G - 1)) * ((n - 1) / float(n - k))
    V = [[scale * sum(Vi[a][c] * meat[c][d] * Vi[d][b] for c in range(k) for d in range(k))
          for b in range(k)] for a in range(k)]
    se = [math.sqrt(V[a][a]) if V[a][a] > 0 else float("nan") for a in range(k)]
    return beta, se


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def mod(datestr):
    """ET minute-of-day from 'YYYY-MM-DD HH:MM:SS' (cache stores ET wall clock)."""
    return int(datestr[11:13]) * 60 + int(datestr[14:16])


def load_days(path):
    """-> {date: [bars sorted by epoch time]}"""
    raw = json.load(open(path))["bars"]
    days = defaultdict(list)
    for b in raw.values():
        days[b["date"][:10]].append(b)
    for d in days:
        days[d].sort(key=lambda b: b["time"])
    return days


def symbols_for(suffix):
    out = []
    for p in sorted(glob.glob(os.path.join(CACHE, "*_%s.json" % suffix))):
        sym = os.path.basename(p).split("_")[0]
        if sym in CRYPTO:
            continue          # 24/7, no regular session -> no session shape
        out.append((sym, p))
    return out


# --------------------------------------------------------------------------
# per-symbol-day feature construction : 5-minute panel (exact 14:00 cut)
# --------------------------------------------------------------------------
def build_5min_panel():
    rows = []
    skipped = defaultdict(int)
    for sym, path in symbols_for("5min"):
        days = load_days(path)
        dates = sorted(days)
        feat = {}
        for d in dates:
            bars = days[d]
            if len(bars) != 78:
                skipped["not_full_78_bars"] += 1
                continue      # early-close / partial session
            mods = [mod(b["date"]) for b in bars]
            if mods[0] != 570 or mods[-1] != 955:
                skipped["bad_session_bounds"] += 1
                continue
            vol = [float(b["v"]) for b in bars]
            vtot = sum(vol)
            if vtot <= 0:
                skipped["zero_volume"] += 1
                continue
            v_first = sum(v for m, v in zip(mods, vol) if 570 <= m < 630)      # 09:30-10:30
            v_last = sum(v for m, v in zip(mods, vol) if 900 <= m < 960)       # 15:00-16:00
            v_mid = vtot - v_first - v_last
            o = float(bars[0]["o"])
            c = float(bars[-1]["c"])
            if o <= 0 or c <= 0:
                skipped["bad_price"] += 1
                continue

            # --- pre-14:00 (causal) block ---
            v14 = [(m, v) for m, v in zip(mods, vol) if m < 840]
            v14tot = sum(v for _, v in v14)
            px1400 = None
            for b, m in zip(bars, mods):
                if m == 840:
                    px1400 = float(b["o"])
                    break
            f14_first = f14_late = ret_1400_close = ret_open_1400 = None
            if v14tot > 0 and px1400 and px1400 > 0:
                f14_first = sum(v for m, v in v14 if 570 <= m < 630) / v14tot   # 09:30-10:30
                f14_late = sum(v for m, v in v14 if 780 <= m < 840) / v14tot    # 13:00-14:00
                ret_1400_close = (c / px1400 - 1.0) * 100.0
                ret_open_1400 = (px1400 / o - 1.0) * 100.0

            feat[d] = dict(
                sym=sym, date=d,
                f_first=v_first / vtot, f_mid=v_mid / vtot, f_last=v_last / vtot,
                tilt=(v_last - v_first) / vtot,
                ret_day=(c / o - 1.0) * 100.0,
                o=o, c=c,
                f14_first=f14_first, f14_late=f14_late,
                tilt14=(None if f14_first is None else f14_late - f14_first),
                ret_1400_close=ret_1400_close, ret_open_1400=ret_open_1400,
            )

        # next-session outcome: strictly the immediately following session that
        # is present in this symbol's own date list (both must be full sessions)
        full = sorted(feat)
        alldates = dates
        idx = dict((d, i) for i, d in enumerate(alldates))
        for d in full:
            i = idx[d]
            nxt = alldates[i + 1] if i + 1 < len(alldates) else None
            if nxt is not None and nxt in feat:
                f = feat[nxt]
                feat[d]["next_oc"] = (f["c"] / f["o"] - 1.0) * 100.0
                feat[d]["next_date"] = nxt
            else:
                feat[d]["next_oc"] = None
                feat[d]["next_date"] = None
            rows.append(feat[d])
    return rows, skipped


def build_1hour_panel():
    """Breadth replication: 28 equities/ETFs, 7 hourly bars 09:30..15:30.
    NOTE the bucket definitions differ -- the final hourly bar is only the last
    30 minutes, and the causal cut is 13:30 not 14:00."""
    rows = []
    for sym, path in symbols_for("1hour"):
        days = load_days(path)
        dates = sorted(days)
        feat = {}
        for d in dates:
            bars = days[d]
            if len(bars) != 7:
                continue
            mods = [mod(b["date"]) for b in bars]
            if mods != [570, 630, 690, 750, 810, 870, 930]:
                continue
            vol = [float(b["v"]) for b in bars]
            vtot = sum(vol)
            if vtot <= 0:
                continue
            o = float(bars[0]["o"])
            c = float(bars[-1]["c"])
            if o <= 0 or c <= 0:
                continue
            v14 = vol[:4]                      # 09:30..13:30 open
            v14tot = sum(v14)
            px1330 = float(bars[4]["o"])
            feat[d] = dict(
                sym=sym, date=d, o=o, c=c,
                f_first=vol[0] / vtot,                 # 09:30-10:30
                f_last=vol[6] / vtot,                  # 15:30-16:00 (30 min)
                f_mid=sum(vol[1:6]) / vtot,
                tilt=(vol[6] - vol[0]) / vtot,
                ret_day=(c / o - 1.0) * 100.0,
                f14_first=(v14[0] / v14tot) if v14tot > 0 else None,
                f14_late=(v14[3] / v14tot) if v14tot > 0 else None,
                tilt14=((v14[3] - v14[0]) / v14tot) if v14tot > 0 else None,
                ret_1400_close=((c / px1330 - 1.0) * 100.0) if px1330 > 0 else None,
                ret_open_1400=((px1330 / o - 1.0) * 100.0) if px1330 > 0 else None,
            )
        alldates = dates
        idx = dict((d, i) for i, d in enumerate(alldates))
        for d in sorted(feat):
            i = idx[d]
            nxt = alldates[i + 1] if i + 1 < len(alldates) else None
            if nxt is not None and nxt in feat:
                f = feat[nxt]
                feat[d]["next_oc"] = (f["c"] / f["o"] - 1.0) * 100.0
            else:
                feat[d]["next_oc"] = None
            rows.append(feat[d])
    return rows


# --------------------------------------------------------------------------
# causal trailing z-score
# --------------------------------------------------------------------------
def add_trailing_z(rows, fields):
    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r["sym"]].append(r)
    for sym, rs in by_sym.items():
        rs.sort(key=lambda r: r["date"])
        for f in fields:
            hist = []
            for r in rs:
                v = r.get(f)
                z = None
                if len(hist) >= TRAIL_MIN:
                    w = hist[-TRAIL_WIN:]
                    mu, sd = mean(w), stdev(w)
                    if v is not None and sd and sd > 0:
                        z = (v - mu) / sd
                r["z_" + f] = z
                if v is not None:
                    hist.append(v)


def bucket_of(z):
    if z is None:
        return None
    for i, e in enumerate(Z_EDGES):
        if z <= e:
            return i
    return len(Z_EDGES)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def describe(obs):
    """obs = list of (date, value). Returns a dict of the required diagnostics."""
    vals = [v for _, v in obs]
    dates = set(d for d, _ in obs)
    n = len(vals)
    mu = mean(vals) if n else float("nan")
    se = sem(vals) if n > 1 else float("nan")
    # drop the single most influential date
    bydate = defaultdict(list)
    for d, v in obs:
        bydate[d].append(v)
    drop_mu = float("nan")
    worst = None
    if len(bydate) > 1:
        best_shift = -1.0
        for d, vs in bydate.items():
            rest = [v for dd, v in obs if dd != d]
            if not rest:
                continue
            m2 = mean(rest)
            if abs(m2 - mu) > best_shift:
                best_shift, drop_mu, worst = abs(m2 - mu), m2, d
    return dict(n=n, mean=mu, med=median(vals), sd=stdev(vals) if n > 1 else float("nan"),
                se=se, ndates=len(dates), trim=trimmed_mean(vals),
                drop1=drop_mu, worst=worst,
                t=(mu / se if se and se == se and se > 0 else float("nan")))


HDR = ("%-13s %6s %8s %8s %8s %8s %6s %8s %8s %7s" %
       ("bucket", "n", "mean%", "median%", "sd", "se", "dates", "trim5%", "drop1d", "t"))


def fmt_row(label, s):
    return ("%-13s %6d %8.4f %8.4f %8.3f %8.4f %6d %8.4f %8.4f %7.2f" %
            (label, s["n"], s["mean"], s["med"], s["sd"], s["se"], s["ndates"],
             s["trim"], s["drop1"], s["t"]))


def bucket_table(rows, zfield, outfield, title, sign_field=None, out=None):
    out = out if out is not None else []
    out.append("")
    out.append(title)
    out.append(HDR)
    buckets = [[] for _ in range(len(Z_EDGES) + 1)]
    for r in rows:
        z = r.get(zfield)
        y = r.get(outfield)
        if z is None or y is None:
            continue
        b = bucket_of(z)
        buckets[b].append((r["date"], y))
    stats = []
    for i, obs in enumerate(buckets):
        if len(obs) < 5:
            out.append("%-13s %6d  (too few)" % (BUCKET_LABELS[i], len(obs)))
            stats.append(None)
            continue
        s = describe(obs)
        stats.append(s)
        out.append(fmt_row(BUCKET_LABELS[i], s))

    # B5 - B1 spread, pooled and as a date-level series
    if stats[0] and stats[-1]:
        d = stats[-1]["mean"] - stats[0]["mean"]
        sd_ = math.sqrt(stats[-1]["se"] ** 2 + stats[0]["se"] ** 2)
        out.append("%-13s %6s %8.4f %8s %8s %8.4f %6s %8s %8s %7.2f" %
                   ("B5-B1", "", d, "", "", sd_, "", "", "", d / sd_ if sd_ > 0 else float("nan")))
        # date-level long/short spread
        b1 = defaultdict(list)
        b5 = defaultdict(list)
        for r in rows:
            z, y = r.get(zfield), r.get(outfield)
            if z is None or y is None:
                continue
            b = bucket_of(z)
            if b == 0:
                b1[r["date"]].append(y)
            elif b == len(Z_EDGES):
                b5[r["date"]].append(y)
        common = sorted(set(b1) & set(b5))
        if len(common) >= 5:
            ser = [mean(b5[d]) - mean(b1[d]) for d in common]
            out.append("  date-level B5-B1 spread: n_dates=%d mean=%+.4f%% median=%+.4f%% "
                       "sd=%.3f se=%.4f t=%.2f  (%.0f%% of dates positive)" %
                       (len(ser), mean(ser), median(ser), stdev(ser), sem(ser),
                        mean(ser) / sem(ser) if sem(ser) > 0 else float("nan"),
                        100.0 * sum(1 for v in ser if v > 0) / len(ser)))
        else:
            out.append("  date-level B5-B1 spread: only %d overlapping dates -- not testable"
                       % len(common))
        # monotonicity
        ms = [s["mean"] for s in stats if s]
        inc = all(ms[i] < ms[i + 1] for i in range(len(ms) - 1))
        dec = all(ms[i] > ms[i + 1] for i in range(len(ms) - 1))
        out.append("  monotone across buckets: %s" %
                   ("YES (increasing)" if inc else "YES (decreasing)" if dec else "NO"))

    if sign_field:
        for lab, keep in (("cond-window UP  ", lambda r: r[sign_field] > 0),
                          ("cond-window DOWN", lambda r: r[sign_field] <= 0)):
            sub = [r for r in rows if r.get(sign_field) is not None and keep(r)]
            sb = [[] for _ in range(len(Z_EDGES) + 1)]
            for r in sub:
                z, y = r.get(zfield), r.get(outfield)
                if z is None or y is None:
                    continue
                sb[bucket_of(z)].append((r["date"], y))
            parts = []
            for i, obs in enumerate(sb):
                if len(obs) < 5:
                    parts.append("B%d n/a" % (i + 1))
                else:
                    s = describe(obs)
                    parts.append("B%d %+.3f(n=%d)" % (i + 1, s["mean"], s["n"]))
            out.append("  [%s] %s" % (lab, "  ".join(parts)))
    return out, stats


REG = {}   # (panel, split, zfield, outfield) -> (beta, se, n, ndates)


def regression_block(rows, zfield, outfield, ctrl_field, out, key=None):
    """outcome ~ z_shape + ctrl_ret + |ctrl_ret| + symbol FE, date-clustered SE."""
    use = [r for r in rows
           if r.get(zfield) is not None and r.get(outfield) is not None
           and r.get(ctrl_field) is not None]
    if len(use) < 100:
        out.append("  OLS: too few rows (%d)" % len(use))
        return None
    syms = sorted(set(r["sym"] for r in use))
    X, y, g = [], [], []
    for r in use:
        row = [1.0, r[zfield], r[ctrl_field], abs(r[ctrl_field])]
        row += [1.0 if r["sym"] == s else 0.0 for s in syms[1:]]
        X.append(row)
        y.append(r[outfield])
        g.append(r["date"])
    beta, se = ols_cluster(X, y, g)
    if beta is None:
        out.append("  OLS: singular")
        return None
    b, s = beta[1], (se[1] if se else float("nan"))
    out.append("  OLS %s ~ %s + %s + |%s| + symbol FE   (n=%d, %d dates, date-clustered SE)"
               % (outfield, zfield, ctrl_field, ctrl_field, len(use), len(set(g))))
    out.append("     beta(%s) = %+.4f%% per 1 sd of shape   se=%.4f   t=%.2f"
               % (zfield, b, s, (b / s) if s and s == s and s > 0 else float("nan")))
    out.append("     beta(%s)=%+.4f  beta(|%s|)=%+.4f"
               % (ctrl_field, beta[2], ctrl_field, beta[3]))
    if key:
        REG[key] = (b, s, len(use), len(set(g)))
    return b, s


# --------------------------------------------------------------------------
def run_panel(rows, name, out, sign_a="ret_day", sign_b="ret_open_1400"):
    tr = [r for r in rows if r["date"] < SPLIT_DATE]
    te = [r for r in rows if r["date"] >= SPLIT_DATE]
    out.append("=" * 108)
    out.append("PANEL %s   symbols=%d  symbol-days=%d  dates=%d  (%s .. %s)"
               % (name, len(set(r["sym"] for r in rows)), len(rows),
                  len(set(r["date"] for r in rows)),
                  min(r["date"] for r in rows), max(r["date"] for r in rows)))
    out.append("  TRAIN rows=%d dates=%d   TEST rows=%d dates=%d"
               % (len(tr), len(set(r["date"] for r in tr)),
                  len(te), len(set(r["date"] for r in te))))
    out.append("=" * 108)

    # sanity: how much is shape just a restatement of the day's move?
    for f in ("f_first", "f_last", "tilt"):
        a = [(r[f], r["ret_day"]) for r in rows if r.get(f) is not None]
        out.append("  corr(%s, day ret) = %+.3f   corr(%s, |day ret|) = %+.3f   "
                   "mean=%.4f sd=%.4f"
                   % (f, pearson([x for x, _ in a], [z for _, z in a]),
                      f, pearson([x for x, _ in a], [abs(z) for _, z in a]),
                      mean([x for x, _ in a]), stdev([x for x, _ in a])))

    results = {}
    for split_name, data in (("TRAIN", tr), ("TEST", te)):
        out.append("")
        out.append("#" * 108)
        out.append("### %s  (%s)   signal dates %s 2025-01-01" %
                   (split_name, name, "<" if split_name == "TRAIN" else ">="))
        out.append("#" * 108)

        out.append("")
        out.append("--- (a) NEXT-DAY open-to-close return %, by TODAY's full-day volume shape ---")
        for zf in ("z_f_first", "z_f_mid", "z_f_last", "z_tilt"):
            _, st = bucket_table(data, zf, "next_oc",
                                 "[%s] next-day O->C ~ %s" % (split_name, zf),
                                 sign_field=sign_a, out=out)
            results[(split_name, zf, "next_oc")] = st
            regression_block(data, zf, "next_oc", "ret_day", out,
                             key=(name, split_name, zf, "next_oc"))

        out.append("")
        out.append("--- (a2) NEXT-DAY |open-to-close| return % (magnitude, not direction) ---")
        for r in data:
            r["next_abs"] = abs(r["next_oc"]) if r.get("next_oc") is not None else None
        for zf in ("z_f_first", "z_f_last"):
            bucket_table(data, zf, "next_abs",
                         "[%s] next-day |O->C| ~ %s" % (split_name, zf), out=out)

        out.append("")
        out.append("--- (b) SAME-DAY 14:00-to-close return %, by shape measured only up to 14:00 ---")
        for zf in ("z_f14_first", "z_f14_late", "z_tilt14"):
            _, st = bucket_table(data, zf, "ret_1400_close",
                                 "[%s] 14:00->close ~ %s" % (split_name, zf),
                                 sign_field=sign_b, out=out)
            results[(split_name, zf, "ret_1400_close")] = st
            regression_block(data, zf, "ret_1400_close", "ret_open_1400", out,
                             key=(name, split_name, zf, "ret_1400_close"))
    return results


def power_note(rows, out, label):
    """Can this panel even see a 0.25%/trade effect?"""
    y = [r["next_oc"] for r in rows if r.get("next_oc") is not None]
    if len(y) < 50:
        return
    sd = stdev(y)
    nb = len(y) / 5.0                       # rough per-bucket n
    se_b = sd / math.sqrt(nb)
    bydate = defaultdict(list)
    for r in rows:
        if r.get("next_oc") is not None:
            bydate[r["date"]].append(r["next_oc"])
    dser = [mean(v) for v in bydate.values()]
    se_d = stdev(dser) / math.sqrt(len(dser))
    out.append("  POWER (%s): next-day O->C sd=%.2f%%; a 5-bucket split gives n~%.0f/bucket,"
               % (label, sd, nb))
    out.append("    pooled se~%.3f%% so a true %.2f%% bucket effect would land at t~%.1f;"
               % (se_b, COST_BPS, COST_BPS / se_b))
    out.append("    at the DATE level (%d dates, sd of daily mean=%.2f%%) se~%.3f%%, t~%.1f."
               % (len(dser), stdev(dser), se_d, COST_BPS / se_d))


def candidate_block(rows5, rows1, out):
    """The auto-selected strongest TRAIN cell replicated on TEST, so test it as its
    own hypothesis instead of letting the pooled summary bury it.

    RULE: z_tilt14 <= -1.0  (morning volume unusually tilted toward the 09:30-10:30
    hour vs the 13:00-14:00 hour, relative to that symbol's own trailing 60 sessions)
    -> SHORT at 14:00, cover at the 15:55 close.

    The obvious confound is that the whole 14:00->close window drifts, so the
    decisive test is the EXCESS of the signalled names over the un-signalled names
    ON THE SAME DATE, tested as a date-level series.
    """
    zf, of, CUT = "z_tilt14", "ret_1400_close", -1.0
    out.append("")
    out.append("#" * 108)
    out.append("FOLLOW-UP -- the one cell that replicated, tested as its own hypothesis")
    out.append("RULE: %s <= %.1f  ->  short at 14:00, cover at 15:55 close" % (zf, CUT))
    out.append("#" * 108)

    for rows, pname in ((rows5, "5-MINUTE"), (rows1, "1-HOUR (13:30 cut, different bars)")):
        out.append("")
        out.append("--- %s ---" % pname)
        for split in ("TRAIN", "TEST"):
            data = [r for r in rows
                    if (r["date"] < SPLIT_DATE) == (split == "TRAIN")
                    and r.get(zf) is not None and r.get(of) is not None]
            sig = [(r["date"], r[of]) for r in data if r[zf] <= CUT]
            rest = [(r["date"], r[of]) for r in data if r[zf] > CUT]
            if len(sig) < 30 or len(rest) < 30:
                out.append("  %s: too few" % split)
                continue
            s, b, a = describe(sig), describe(rest), describe([(r["date"], r[of]) for r in data])
            out.append("  %s signalled : n=%4d dates=%3d mean=%+.4f%% med=%+.4f%% sd=%.3f "
                       "se=%.4f t=%+.2f trim5%%=%+.4f drop1d=%+.4f"
                       % (split, s["n"], s["ndates"], s["mean"], s["med"], s["sd"],
                          s["se"], s["t"], s["trim"], s["drop1"]))
            out.append("  %s rest      : n=%4d dates=%3d mean=%+.4f%%   |  ALL: mean=%+.4f%% "
                       "(the drift the signal must beat)"
                       % (split, b["n"], b["ndates"], b["mean"], a["mean"]))
            out.append("  %s RAW EDGE vs rest = %+.4f%%" % (split, s["mean"] - b["mean"]))

            # date-level excess: signalled minus un-signalled, same date
            bd_s, bd_r = defaultdict(list), defaultdict(list)
            for d, v in sig:
                bd_s[d].append(v)
            for d, v in rest:
                bd_r[d].append(v)
            common = sorted(set(bd_s) & set(bd_r))
            if len(common) >= 10:
                ex = [mean(bd_s[d]) - mean(bd_r[d]) for d in common]
                out.append("  %s DATE-LEVEL excess: n_dates=%d mean=%+.4f%% med=%+.4f%% "
                           "sd=%.3f se=%.4f t=%+.2f (%.0f%% of dates negative)"
                           % (split, len(ex), mean(ex), median(ex), stdev(ex), sem(ex),
                              mean(ex) / sem(ex) if sem(ex) > 0 else float("nan"),
                              100.0 * sum(1 for v in ex if v < 0) / len(ex)))
            # net of cost, trading it short
            gross = -s["mean"]
            out.append("  %s NET AFTER %.2f%% COST (short): %+.4f%% per trade  -> %s"
                       % (split, COST_BPS, gross - COST_BPS,
                          "TRADEABLE" if gross - COST_BPS > 0 else "LOSES MONEY"))
            if pname == "5-MINUTE":
                # per-symbol and per-half-year: is one name / one stretch carrying it?
                bysym = defaultdict(list)
                for r in data:
                    if r[zf] <= CUT:
                        bysym[r["sym"]].append(r[of])
                out.append("  %s by symbol: %s" % (split, "  ".join(
                    "%s %+.3f(n=%d)" % (k, mean(v), len(v)) for k, v in sorted(bysym.items()))))
                byhy = defaultdict(list)
                for r in data:
                    if r[zf] <= CUT:
                        byhy[r["date"][:4] + ("H1" if r["date"][5:7] <= "06" else "H2")].append(r[of])
                out.append("  %s by half-year: %s" % (split, "  ".join(
                    "%s %+.3f(n=%d)" % (k, mean(v), len(v)) for k, v in sorted(byhy.items()))))
                loo = []
                for drop in sorted(bysym):
                    keep = [v for k, vs in bysym.items() if k != drop for v in vs]
                    loo.append("ex-%s %+.3f" % (drop, mean(keep)))
                out.append("  %s leave-one-symbol-out mean: %s" % (split, "  ".join(loo)))


def main():
    out = []
    out.append("VOLUME SHAPE PROBE -- does participation TIMING predict returns?")
    out.append("cost hurdle for tradeability: %.2f%% per trade" % COST_BPS)
    out.append("buckets: causal trailing z (prior %d sessions, min %d) vs fixed edges %s"
               % (TRAIL_WIN, TRAIL_MIN, Z_EDGES))
    out.append("NOTE multiple testing: 4 shape vars x 2 outcomes = 8 primary tests per split;")
    out.append("     a 2-SE bar is therefore generous. f_mid = 1 - f_first - f_last (not independent).")

    rows5, skipped = build_5min_panel()
    add_trailing_z(rows5, ["f_first", "f_mid", "f_last", "tilt",
                           "f14_first", "f14_late", "tilt14"])
    out.append("")
    out.append("5-min panel build: %d symbol-days kept; skipped %s"
               % (len(rows5), dict(skipped)))
    power_note(rows5, out, "5-min panel")
    res5 = run_panel(rows5, "5-MINUTE (exact 09:30-10:30 / 15:00-16:00 / 14:00 cut)", out)

    rows1 = build_1hour_panel()
    add_trailing_z(rows1, ["f_first", "f_mid", "f_last", "tilt",
                           "f14_first", "f14_late", "tilt14"])
    out.append("")
    out.append("")
    out.append("BREADTH REPLICATION on hourly bars. Definitions differ: 'last' bucket is the")
    out.append("final 30 minutes (15:30-16:00) and the causal cut is 13:30, not 14:00.")
    out.append("6 of these symbols overlap the 5-minute panel, so this is not independent.")
    power_note(rows1, out, "1-hour panel")
    res1 = run_panel(rows1, "1-HOUR (28 equities/ETFs)", out)

    # --- appendix: the 191-symbol wide cross-section (only 3 dates) ---
    out.append("")
    out.append("=" * 108)
    out.append("APPENDIX -- wide 191-symbol 5min_ext cross-section")
    ext = symbols_for("5min_ext")
    dts = set()
    for _, p in ext:
        for d in load_days(p):
            dts.add(d)
    out.append("  %d symbols but only %d distinct session dates (%s). A cross-section this"
               % (len(ext), len(dts), ", ".join(sorted(dts))))
    out.append("  narrow in TIME cannot test a daily effect at all -- any 'n' it produces is")
    out.append("  ~%d observations riding on %d days. Not measured." % (len(ext) * len(dts), len(dts)))
    out.append("=" * 108)

    # ---------------- headline summary matrix ----------------
    out.append("")
    out.append("#" * 108)
    out.append("SUMMARY -- every test in one place.")
    out.append("beta = % return per +1 sd of the shape variable, from OLS with the conditioning")
    out.append("window's return, its absolute value, and symbol fixed effects as controls;")
    out.append("t uses DATE-CLUSTERED standard errors (the honest ones -- pooled t is inflated")
    out.append("because symbols move together within a day).")
    out.append("#" * 108)
    hdr = "%-34s %-14s %10s %7s %10s %7s %8s" % (
        "panel / shape var -> outcome", "", "TRAIN beta", "t", "TEST beta", "t", "same sign")
    out.append(hdr)
    out.append("-" * 108)
    same = tot = 0
    biggest = 0.0
    panels, seen = [], set()
    for k in REG:
        if k[0] not in seen:
            seen.add(k[0])
            panels.append(k[0])
    order = [("z_f_first", "next_oc"), ("z_f_mid", "next_oc"), ("z_f_last", "next_oc"),
             ("z_tilt", "next_oc"), ("z_f14_first", "ret_1400_close"),
             ("z_f14_late", "ret_1400_close"), ("z_tilt14", "ret_1400_close")]
    for panel in panels:
        short = panel.split(" ")[0]
        for zf, of in order:
            a = REG.get((panel, "TRAIN", zf, of))
            b = REG.get((panel, "TEST", zf, of))
            if not a or not b:
                continue
            ta = a[0] / a[1] if a[1] else float("nan")
            tb = b[0] / b[1] if b[1] else float("nan")
            ss = "yes" if a[0] * b[0] > 0 else "no"
            same += 1 if ss == "yes" else 0
            tot += 1
            biggest = max(biggest, abs(a[0]), abs(b[0]))
            out.append("%-34s %-14s %+10.4f %7.2f %+10.4f %7.2f %8s"
                       % (short + "  " + zf, "-> " + of, a[0], ta, b[0], tb, ss))
    out.append("-" * 108)
    out.append("TRAIN/TEST sign agreement: %d of %d (%.0f%%) -- a coin flip is %d."
               % (same, tot, 100.0 * same / tot if tot else 0, tot // 2))
    out.append("Largest |beta| anywhere: %.4f%% per sd, vs a %.2f%% per-trade cost hurdle."
               % (biggest, COST_BPS))
    out.append("A +-2 sd shape move would therefore be worth at most %.3f%% -- under cost."
               % (4 * biggest))

    # ---- the single best-looking cell on TRAIN, followed to TEST ----
    out.append("")
    out.append("STRONGEST SINGLE CELL ON TRAIN, FOLLOWED TO TEST")
    best = None
    for res, pname in ((res5, "5-MINUTE"), (res1, "1-HOUR")):
        for (split, zf, of), st in res.items():
            if split != "TRAIN" or not st:
                continue
            for i, s in enumerate(st):
                if not s or s["n"] < 100:
                    continue
                if best is None or abs(s["t"]) > abs(best[0]["t"]):
                    best = (s, pname, zf, of, i)
    if best:
        s, pname, zf, of, i = best
        out.append("  %s  %s  bucket %s  ->  %s" % (pname, zf, BUCKET_LABELS[i], of))
        out.append("    TRAIN: mean=%+.4f%% median=%+.4f%% sd=%.3f se=%.4f n=%d dates=%d "
                   "t=%.2f trim5%%=%+.4f drop-worst-date=%+.4f"
                   % (s["mean"], s["med"], s["sd"], s["se"], s["n"], s["ndates"],
                      s["t"], s["trim"], s["drop1"]))
        res = res5 if pname == "5-MINUTE" else res1
        st2 = res.get(("TEST", zf, of))
        if st2 and st2[i]:
            s2 = st2[i]
            out.append("    TEST : mean=%+.4f%% median=%+.4f%% sd=%.3f se=%.4f n=%d dates=%d "
                       "t=%.2f trim5%%=%+.4f drop-worst-date=%+.4f"
                       % (s2["mean"], s2["med"], s2["sd"], s2["se"], s2["n"], s2["ndates"],
                          s2["t"], s2["trim"], s2["drop1"]))
            out.append("    TEST retains %.0f%% of the TRAIN effect."
                       % (100.0 * s2["mean"] / s["mean"] if s["mean"] else float("nan")))
    # ---- follow-up: the one cell that replicated, tested on its own terms ----
    candidate_block(rows5, rows1, out)

    out.append("")
    out.append("=" * 108)
    out.append("VERDICT -- NEGATIVE. Volume shape does not predict next-day open-to-close")
    out.append("return at all: 14 controlled slopes across two panels, largest |beta| 0.12% per")
    out.append("sd, TRAIN/TEST sign agreement 8/14 (a coin flip), nothing monotone, and the")
    out.append("1-hour panel is powered to see a 0.25% bucket effect at t~4.4 and sees nothing.")
    out.append("")
    out.append("One cell DID replicate and deserves the explicit refutation above: an unusually")
    out.append("front-loaded morning (z_tilt14 <= -1) preceded a 14:00->close return of -0.242%")
    out.append("on TRAIN and -0.230% on TEST. It fails anyway, four separate ways:")
    out.append("  1. COST. Shorting it grosses 0.24%%/0.23%%, under the %.2f%% hurdle -- net"
               % COST_BPS)
    out.append("     -0.008% (TRAIN) and -0.020% (TEST) per trade. It loses money as a rule.")
    out.append("  2. DRIFT. Most of TRAIN is just the afternoon drifting down for everything")
    out.append("     that year (all names averaged -0.104%). Netting out the un-signalled names")
    out.append("     on the same date leaves t=-1.57 (TRAIN) and t=-0.63 (TEST) -- under 2 SE.")
    out.append("  3. ONE SYMBOL. MARA alone averages -0.88% in both splits; excluding it halves")
    out.append("     the effect (-0.242->-0.126 TRAIN, -0.230->-0.118 TEST). Excluding any other")
    out.append("     single name changes nothing. This is one high-beta miner, not a shape law.")
    out.append("  4. TAIL, NOT CENTRE. The median signalled day is -0.02% (TRAIN) and -0.00%")
    out.append("     (TEST); trimming 5% each tail leaves -0.167%/-0.206%. A mean built from a")
    out.append("     left tail is not something a 14:00 entry can harvest.")
    out.append("")
    out.append("Do not trade volume shape. The honest use of participation timing here is as a")
    out.append("descriptive context feature, not a signal.")
    out.append("=" * 108)

    txt = "\n".join(out)
    print(txt)
    dst = "/Users/blackbox/TradeFinder/backend/scripts/research/volume_shape_output.txt"
    try:
        open(dst, "w").write(txt + "\n")
    except Exception:
        pass
    return res5, res1


if __name__ == "__main__":
    main()
