#!/usr/bin/env python
"""
probe_overnight_vs_intraday.py
==============================
Decompose daily returns into the OVERNIGHT GAP and the REGULAR SESSION, and ask
whether the gap predicts the direction of the session that follows it.

Decomposition (validated against 5min bars -- see validate_daily_vs_5min()):
    gap_t      = open_t  / close_{t-1} - 1        (overnight, not tradeable intraday)
    intra_t    = close_t / open_t      - 1        (regular session, TRADEABLE from 09:30)
    (1+gap)(1+intra) = close-to-close

Conditioning, per the brief:
    (a) gap size in ATR units          gap_atr = (open_t - close_{t-1}) / ATR14(t-1)
    (b) gap WITH or AGAINST the prior day's close direction
    (c) whether the first 30 minutes confirm or fade the gap   (needs intraday bars)

Strategy convention used throughout:
    FADE  return = -sign(gap) * intra      (short the gap / bet on gap fill)
    CONT  return = +sign(gap) * intra      (bet on gap continuation)
    FADE = -CONT, so one table reports both.

Splits are BY DATE, never randomly.  TRAIN = signal date < 2025-01-01,
TEST = signal date >= 2025-01-01.  Nothing is tuned on TEST.

Panels (dictated by what is actually on disk):
    A  28 liquid equities/ETFs, daily, 2022-01..2025-08  -> real TRAIN/TEST split
    B  187 low-float "mover" names, daily, 2026-05..2026-09 -> 3rd holdout, other regime
    C  6 equities with 5min regular-session bars, 2024-01..2025-08 -> first-30-min test
    D  191 movers with 5min_ext premarket bars -> only 3 distinct dates, disqualified

Run:  /Users/blackbox/TradeFinder/.venv/bin/python \
        scripts/research/probe_overnight_vs_intraday.py     (from backend/)
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics as st
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT = "2025-01-01"          # TRAIN < SPLIT <= TEST
COST = 0.25                   # round-trip cost floor, percent
CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD"}   # 24/7: no overnight gap exists
MIN_PRICE = 1.00
MAX_CAL_GAP_DAYS = 5          # skip holes (halts, delistings, long holidays)

# ----------------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------------


def load_bars(path: str) -> List[dict]:
    with open(path) as fh:
        raw = json.load(fh)["bars"]
    return sorted(raw.values(), key=lambda b: b["time"])


def sym_of(path: str) -> str:
    return os.path.basename(path).split("_")[0]


def et_minute(bar: dict) -> int:
    """Minute-of-day in ET.  The 'date' string is already ET wall clock."""
    d = bar["date"]
    return int(d[11:13]) * 60 + int(d[14:16])


def daysdiff(d1: str, d2: str) -> int:
    import datetime as _dt
    a = _dt.date(int(d1[:4]), int(d1[5:7]), int(d1[8:10]))
    b = _dt.date(int(d2[:4]), int(d2[5:7]), int(d2[8:10]))
    return (b - a).days


# ----------------------------------------------------------------------------
# indicators (local, no lookahead)
# ----------------------------------------------------------------------------


def atr_at(bars: Sequence[dict], i: int, n: int = 14) -> Optional[float]:
    """ATR over the n bars ENDING at index i (so at index i+1 it is known)."""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        h, l, pc = bars[k]["h"], bars[k]["l"], bars[k - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


# ----------------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------------


def trimmed_mean(vals: Sequence[float], frac: float = 0.05) -> Optional[float]:
    v = sorted(vals)
    k = int(len(v) * frac)
    if len(v) - 2 * k < 3:
        return None
    core = v[k: len(v) - k] if k else v
    return sum(core) / len(core)


class Bucket:
    """Observations tagged with the session date they belong to."""

    def __init__(self, name: str):
        self.name = name
        self.vals: List[float] = []
        self.dates: List[str] = []
        self.syms: List[str] = []

    def add(self, val: float, date: str, sym: str) -> None:
        self.vals.append(val)
        self.dates.append(date)
        self.syms.append(sym)

    def __len__(self) -> int:
        return len(self.vals)

    # -- date clustering ------------------------------------------------
    def by_date(self) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for v, d in zip(self.vals, self.dates):
            out.setdefault(d, []).append(v)
        return out

    def stats(self) -> dict:
        n = len(self.vals)
        if n == 0:
            return {"n": 0}
        mean = sum(self.vals) / n
        med = st.median(self.vals)
        sd = st.pstdev(self.vals) if n < 2 else st.stdev(self.vals)
        se = sd / math.sqrt(n) if n else float("nan")

        bd = self.by_date()
        dmeans = {d: sum(v) / len(v) for d, v in bd.items()}
        nd = len(dmeans)
        # date-clustered SE: treat each session date as one independent draw
        if nd >= 2:
            dm = list(dmeans.values())
            cl_mean = sum(dm) / nd
            cl_se = st.stdev(dm) / math.sqrt(nd)
        else:
            cl_mean, cl_se = mean, float("nan")

        tm = trimmed_mean(self.vals, 0.05)

        # leave-one-date-out influence: how much does the single worst/best
        # date move the pooled mean?
        infl = 0.0
        worst_date = ""
        drop5 = mean
        if nd >= 6:
            deltas = []
            tot = sum(self.vals)
            for d, vs in bd.items():
                rest_n = n - len(vs)
                if rest_n < 3:
                    continue
                rest_mean = (tot - sum(vs)) / rest_n
                deltas.append((abs(mean - rest_mean), mean - rest_mean, d))
            if deltas:
                deltas.sort(reverse=True)
                infl = deltas[0][1]          # pooled mean minus mean-without-that-date
                worst_date = deltas[0][2]
                drop_set = {d for _, _, d in deltas[:5]}
                keep = [v for v, d in zip(self.vals, self.dates) if d not in drop_set]
                drop5 = (sum(keep) / len(keep)) if len(keep) >= 3 else float("nan")

        return {
            "n": n,
            "mean": mean,
            "median": med,
            "sd": sd,
            "se": se,
            "t": mean / se if se else float("nan"),
            "ndates": nd,
            "nsyms": len(set(self.syms)),
            "cl_mean": cl_mean,
            "cl_se": cl_se,
            "cl_t": (cl_mean / cl_se) if cl_se and not math.isnan(cl_se) else float("nan"),
            "trim": tm,
            "max_date_infl": infl,
            "worst_date": worst_date,
            "drop5": drop5,
            "hit": 100.0 * sum(1 for v in self.vals if v > 0) / n,
        }


HDR = ("{:<26} {:>6} {:>8} {:>8} {:>7} {:>7} {:>6} {:>6} {:>7} {:>7} {:>6} {:>8} {:>7}"
       .format("bucket", "n", "mean%", "med%", "sd%", "se%", "t", "clus_t", "trim5%",
               "drop5d%", "dates", "maxinfl", "hit%"))


def fmt_row(name: str, s: dict) -> str:
    if s.get("n", 0) == 0:
        return "{:<26} {:>6}".format(name, 0)
    def f(x, w=8, p=3):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return " " * (w - 3) + "n/a"
        return "{:>{w}.{p}f}".format(x, w=w, p=p)
    return ("{:<26} {:>6d} {} {} {} {} {} {} {} {} {:>6d} {} {}".format(
        name, s["n"], f(s["mean"]), f(s["median"]), f(s["sd"], 7, 2), f(s["se"], 7, 3),
        f(s["t"], 6, 2), f(s["cl_t"], 6, 2), f(s["trim"], 7, 3), f(s["drop5"], 7, 3),
        s["ndates"], f(s["max_date_infl"], 8, 3), f(s["hit"], 7, 1)))


def table(title: str, buckets: Sequence[Bucket], note: str = "") -> None:
    print("\n" + title)
    if note:
        print("  " + note)
    print("  " + HDR)
    for b in buckets:
        print("  " + fmt_row(b.name, b.stats()))


def verdict_line(b: Bucket) -> str:
    s = b.stats()
    if s.get("n", 0) < 30:
        return "{}: n={} too small to judge".format(b.name, s.get("n", 0))
    naive = abs(s["t"]) > 2
    clus = (not math.isnan(s["cl_t"])) and abs(s["cl_t"]) > 2
    trim_ok = s["trim"] is not None and (s["trim"] * s["mean"] > 0) and abs(s["trim"]) >= 0.5 * abs(s["mean"])
    cost_ok = abs(s["mean"]) > COST
    return ("{}: mean={:+.3f}% | >2 naive SE from zero: {} | >2 date-clustered SE: {} "
            "| survives 5% trim: {} | clears {:.2f}% cost: {} | dates={}"
            .format(b.name, s["mean"], "YES" if naive else "no", "YES" if clus else "no",
                    "YES" if trim_ok else "no", COST, "YES" if cost_ok else "no", s["ndates"]))


# ----------------------------------------------------------------------------
# feature construction
# ----------------------------------------------------------------------------


def build_daily_rows(paths: Sequence[str]) -> List[dict]:
    """One row per (symbol, session) with the gap/intraday decomposition."""
    rows: List[dict] = []
    for p in paths:
        sym = sym_of(p)
        if sym in CRYPTO:
            continue
        bars = load_bars(p)
        if len(bars) < 30:
            continue
        for i in range(1, len(bars)):
            cur, prev = bars[i], bars[i - 1]
            d, pd_ = cur["date"][:10], prev["date"][:10]
            if daysdiff(pd_, d) > MAX_CAL_GAP_DAYS:
                continue
            if min(prev["c"], prev["o"], cur["o"], cur["c"]) < MIN_PRICE:
                continue
            a = atr_at(bars, i - 1, 14)
            if not a or a <= 0:
                continue
            gap = cur["o"] / prev["c"] - 1.0
            intra = cur["c"] / cur["o"] - 1.0
            gap_atr = (cur["o"] - prev["c"]) / a
            prev_intra = prev["c"] / prev["o"] - 1.0
            prev_c2c = (prev["c"] / bars[i - 2]["c"] - 1.0) if i >= 2 else 0.0
            if gap == 0:
                continue
            sgn = 1.0 if gap > 0 else -1.0
            rows.append({
                "sym": sym, "date": d,
                "gap": 100 * gap, "intra": 100 * intra,
                "gap_atr": gap_atr, "abs_gap_atr": abs(gap_atr),
                "sgn": sgn,
                "fade": -sgn * 100 * intra,
                "cont": sgn * 100 * intra,
                "prev_intra": 100 * prev_intra,
                "prev_c2c": 100 * prev_c2c,
                "with_prev": sgn * (1.0 if prev_intra > 0 else -1.0) > 0,
                "with_prev_c2c": sgn * (1.0 if prev_c2c > 0 else -1.0) > 0,
            })
    return rows


ATR_BINS = [(0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1e9)]
ATR_LBL = ["|gap|ATR 0.00-0.25", "|gap|ATR 0.25-0.50", "|gap|ATR 0.50-1.00",
           "|gap|ATR 1.00-2.00", "|gap|ATR 2.00+"]


def atr_bin(x: float) -> int:
    for j, (lo, hi) in enumerate(ATR_BINS):
        if lo <= x < hi:
            return j
    return len(ATR_BINS) - 1


def split_rows(rows: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    return ([r for r in rows if r["date"] < SPLIT],
            [r for r in rows if r["date"] >= SPLIT])


def run_a(rows: Sequence[dict], tag: str, field: str = "fade") -> List[Bucket]:
    """(a) gap size in ATR units."""
    bs = [Bucket(ATR_LBL[j]) for j in range(len(ATR_BINS))]
    for r in rows:
        bs[atr_bin(r["abs_gap_atr"])].add(r[field], r["date"], r["sym"])
    table("[{}] (a) FADE-the-gap return by gap size (open->close, signed -sign(gap))".format(tag), bs)
    return bs


def run_a_direction(rows: Sequence[dict], tag: str) -> None:
    """Raw session return split by gap direction, to expose asymmetry."""
    bs = []
    for j in range(len(ATR_BINS)):
        for updn, lab in ((True, "UP  "), (False, "DOWN")):
            b = Bucket("{} {}".format(lab, ATR_LBL[j][9:]))
            for r in rows:
                if atr_bin(r["abs_gap_atr"]) == j and ((r["sgn"] > 0) == updn):
                    b.add(r["intra"], r["date"], r["sym"])
            bs.append(b)
    table("[{}] (a2) RAW session return (open->close) by gap DIRECTION x size".format(tag), bs,
          note="positive = session rose; up-gap+positive = continuation")


def run_b(rows: Sequence[dict], tag: str, key: str = "with_prev") -> None:
    """(b) gap with vs against the prior day's direction."""
    bs = []
    for w, lab in ((True, "WITH prior day"), (False, "AGAINST prior day")):
        b = Bucket(lab)
        for r in rows:
            if r[key] == w:
                b.add(r["fade"], r["date"], r["sym"])
        bs.append(b)
    for j in range(len(ATR_BINS)):
        for w, lab in ((True, "WITH"), (False, "AGST")):
            b = Bucket("{} {}".format(lab, ATR_LBL[j][9:]))
            for r in rows:
                if r[key] == w and atr_bin(r["abs_gap_atr"]) == j:
                    b.add(r["fade"], r["date"], r["sym"])
            bs.append(b)
    table("[{}] (b) FADE return by gap vs prior-day direction ({})".format(tag, key), bs)


# ----------------------------------------------------------------------------
# (c) first 30 minutes -- needs intraday bars
# ----------------------------------------------------------------------------


def build_intraday_rows(syms: Sequence[str], suffix: str) -> List[dict]:
    """Row per (symbol, session): gap, first-30min return, and 10:00->close return."""
    rows: List[dict] = []
    for sym in syms:
        if sym in CRYPTO:
            continue
        dp = os.path.join(CACHE, "{}_1day.json".format(sym))
        ip = os.path.join(CACHE, "{}_{}.json".format(sym, suffix))
        if not (os.path.exists(dp) and os.path.exists(ip)):
            continue
        dbars = load_bars(dp)
        didx = {b["date"][:10]: k for k, b in enumerate(dbars)}
        byday: Dict[str, List[dict]] = {}
        for b in load_bars(ip):
            byday.setdefault(b["date"][:10], []).append(b)

        for d, lst in sorted(byday.items()):
            k = didx.get(d)
            if k is None or k < 1:
                continue
            prev = dbars[k - 1]
            if daysdiff(prev["date"][:10], d) > MAX_CAL_GAP_DAYS:
                continue
            a = atr_at(dbars, k - 1, 14)
            if not a or a <= 0:
                continue
            reg = sorted([b for b in lst if 570 <= et_minute(b) <= 955],
                         key=lambda b: b["time"])
            if len(reg) < 60:                       # need a full-ish session
                continue
            if et_minute(reg[0]) != 570 or et_minute(reg[-1]) < 950:
                continue
            open_930 = reg[0]["o"]
            # 09:30 -> 10:00 == open of the 09:30 bar to close of the 09:55 bar
            first30 = [b for b in reg if 570 <= et_minute(b) < 600]
            if not first30 or et_minute(first30[-1]) != 595:
                continue
            px_1000 = first30[-1]["c"]
            close_reg = reg[-1]["c"]
            if min(open_930, px_1000, close_reg, prev["c"]) < MIN_PRICE:
                continue
            gap = open_930 / prev["c"] - 1.0
            if gap == 0:
                continue
            sgn = 1.0 if gap > 0 else -1.0
            r30 = px_1000 / open_930 - 1.0
            rest = close_reg / px_1000 - 1.0        # tradeable: entry 10:00, exit close
            rows.append({
                "sym": sym, "date": d,
                "gap": 100 * gap, "gap_atr": (open_930 - prev["c"]) / a,
                "abs_gap_atr": abs((open_930 - prev["c"]) / a),
                "sgn": sgn,
                "r30": 100 * r30,
                "rest": 100 * rest,
                "fade_rest": -sgn * 100 * rest,
                "cont_rest": sgn * 100 * rest,
                "confirm": (r30 * gap) > 0,          # first 30min moved WITH the gap
                "r30_sgn": 1.0 if r30 > 0 else -1.0,
                "follow30_rest": (1.0 if r30 > 0 else -1.0) * 100 * rest,
            })
    return rows


def run_c(rows: Sequence[dict], tag: str) -> None:
    bs = []
    for conf, lab in ((True, "CONFIRM (30m with gap)"), (False, "FADE30 (30m vs gap)")):
        b = Bucket(lab)
        for r in rows:
            if r["confirm"] == conf:
                b.add(r["cont_rest"], r["date"], r["sym"])
        bs.append(b)
    table("[{}] (c) CONTINUATION return 10:00->close, signed +sign(gap)".format(tag), bs,
          note="positive = gap direction kept working after 10:00")

    bs2 = []
    for conf, lab in ((True, "CONFIRM"), (False, "FADE30 ")):
        for j in range(len(ATR_BINS)):
            b = Bucket("{} {}".format(lab, ATR_LBL[j][9:]))
            for r in rows:
                if r["confirm"] == conf and atr_bin(r["abs_gap_atr"]) == j:
                    b.add(r["cont_rest"], r["date"], r["sym"])
            bs2.append(b)
    table("[{}] (c2) same, x gap size".format(tag), bs2)

    bs3 = [Bucket("follow the 30m move"), Bucket("gap up only"), Bucket("gap down only")]
    for r in rows:
        bs3[0].add(r["follow30_rest"], r["date"], r["sym"])
        bs3[1 if r["sgn"] > 0 else 2].add(r["follow30_rest"], r["date"], r["sym"])
    table("[{}] (c3) momentum of the first 30m itself: +sign(r30) * (10:00->close)".format(tag), bs3)


# ----------------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------------


def validate_daily_vs_5min() -> None:
    print("\n" + "=" * 100)
    print("VALIDATION: does the daily bar's open/close equal the REGULAR-SESSION open/close?")
    print("=" * 100)
    for sym in ["SPY", "AAPL", "TSLA", "MARA"]:
        dbars = {b["date"][:10]: b for b in load_bars(os.path.join(CACHE, sym + "_1day.json"))}
        byday: Dict[str, List[dict]] = {}
        for b in load_bars(os.path.join(CACHE, sym + "_5min.json")):
            byday.setdefault(b["date"][:10], []).append(b)
        eo = ec = n = 0.0
        for d, lst in byday.items():
            if d not in dbars:
                continue
            lst.sort(key=lambda b: b["time"])
            if et_minute(lst[0]) != 570 or et_minute(lst[-1]) != 955:
                continue
            db = dbars[d]
            n += 1
            eo += abs(lst[0]["o"] - db["o"]) / db["o"]
            ec += abs(lst[-1]["c"] - db["c"]) / db["c"]
        print("  {:<6} n={:>4}  mean |daily.o - 09:30 open| = {:.4f}%   "
              "mean |daily.c - 15:55 close| = {:.4f}%"
              .format(sym, int(n), 100 * eo / n, 100 * ec / n))
    print("  -> daily o/c ARE the regular-session open/close, so gap = o/prev_c - 1 is the true "
          "overnight leg.")


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def main() -> None:
    daily_paths = sorted(glob.glob(os.path.join(CACHE, "*_1day.json")))
    long_paths, short_paths = [], []
    for p in daily_paths:
        if sym_of(p) in CRYPTO:
            continue
        bars = load_bars(p)
        if not bars:
            continue
        (long_paths if bars[0]["date"][:10] < "2023-01-01" else short_paths).append(p)

    print("=" * 100)
    print("OVERNIGHT GAP vs REGULAR SESSION  --  does the gap predict the session that follows?")
    print("=" * 100)
    print("TRAIN = session date < {}   TEST = session date >= {}   cost floor = {:.2f}%/trade"
          .format(SPLIT, SPLIT, COST))
    print("Columns: t = mean/(sd/sqrt(n)) (naive, treats same-day rows as independent)")
    print("         clus_t = mean-of-daily-means / (sd(daily means)/sqrt(n_dates)) -- the honest one")
    print("         trim5% = mean after dropping top and bottom 5% of observations")
    print("         drop5d% = mean after dropping the 5 most influential session dates")
    print("         maxinfl = pooled mean minus mean-without-the-single-most-influential-date")

    validate_daily_vs_5min()

    # ---------------- PANEL A ----------------
    print("\n" + "=" * 100)
    print("PANEL A -- {} liquid equities/ETFs with long history (daily bars)".format(len(long_paths)))
    print("=" * 100)
    rows_a = build_daily_rows(long_paths)
    tr, te = split_rows(rows_a)
    print("symbols: {}".format(", ".join(sorted({sym_of(p) for p in long_paths}))))
    print("rows: total={}  TRAIN={} ({} dates)  TEST={} ({} dates)".format(
        len(rows_a), len(tr), len({r['date'] for r in tr}), len(te), len({r['date'] for r in te})))
    if tr:
        print("TRAIN span {} .. {}   TEST span {} .. {}".format(
            min(r["date"] for r in tr), max(r["date"] for r in tr),
            min(r["date"] for r in te), max(r["date"] for r in te)))

    print("\n----- TRAIN -----")
    a_tr = run_a(tr, "A/TRAIN")
    run_a_direction(tr, "A/TRAIN")
    run_b(tr, "A/TRAIN", "with_prev")
    run_b(tr, "A/TRAIN", "with_prev_c2c")

    print("\n----- TEST (never tuned on) -----")
    a_te = run_a(te, "A/TEST")
    run_a_direction(te, "A/TEST")
    run_b(te, "A/TEST", "with_prev")

    print("\n  monotonicity of FADE mean across the 5 gap-size buckets:")
    for lab, bs in (("TRAIN", a_tr), ("TEST", a_te)):
        ms = [b.stats().get("mean", float("nan")) for b in bs]
        diffs = [ms[i + 1] - ms[i] for i in range(len(ms) - 1)]
        mono = all(d > 0 for d in diffs) or all(d < 0 for d in diffs)
        print("    {}: {}   monotone={}".format(
            lab, "  ".join("{:+.3f}".format(m) for m in ms), mono))

    print("\n  verdicts (TRAIN):")
    for b in a_tr:
        print("    " + verdict_line(b))
    print("  verdicts (TEST):")
    for b in a_te:
        print("    " + verdict_line(b))

    # ---------------- PANEL B ----------------
    print("\n" + "=" * 100)
    print("PANEL B -- {} low-float 'mover' names, daily 2026-05..2026-09 (THIRD holdout, "
          "different universe and regime)".format(len(short_paths)))
    print("=" * 100)
    rows_b = build_daily_rows(short_paths)
    print("rows={}  dates={}  symbols={}".format(
        len(rows_b), len({r["date"] for r in rows_b}), len({r["sym"] for r in rows_b})))
    if rows_b:
        print("span {} .. {}".format(min(r["date"] for r in rows_b), max(r["date"] for r in rows_b)))
        b_all = run_a(rows_b, "B/OOS2")
        run_a_direction(rows_b, "B/OOS2")
        run_b(rows_b, "B/OOS2", "with_prev")
        ms = [b.stats().get("mean", float("nan")) for b in b_all]
        diffs = [ms[i + 1] - ms[i] for i in range(len(ms) - 1)]
        print("\n  monotone across size buckets: {}   means: {}".format(
            all(d > 0 for d in diffs) or all(d < 0 for d in diffs),
            "  ".join("{:+.3f}".format(m) for m in ms)))
        print("  verdicts (B):")
        for b in b_all:
            print("    " + verdict_line(b))

    # ---------------- PANEL C ----------------
    print("\n" + "=" * 100)
    print("PANEL C -- first-30-minute confirm/fade, 5min regular-session bars")
    print("=" * 100)
    c_syms = sorted({sym_of(p) for p in glob.glob(os.path.join(CACHE, "*_5min.json"))} - CRYPTO)
    rows_c = build_intraday_rows(c_syms, "5min")
    ctr, cte = split_rows(rows_c)
    print("symbols: {}".format(", ".join(c_syms)))
    print("rows: total={}  TRAIN={} ({} dates)  TEST={} ({} dates)".format(
        len(rows_c), len(ctr), len({r['date'] for r in ctr}), len(cte), len({r['date'] for r in cte})))
    if ctr:
        print("\n----- TRAIN -----")
        run_c(ctr, "C/TRAIN")
        print("\n----- TEST -----")
        run_c(cte, "C/TEST")

        print("\n  verdicts (C/TRAIN):")
        for conf, lab in ((True, "CONFIRM"), (False, "FADE30")):
            b = Bucket(lab)
            for r in ctr:
                if r["confirm"] == conf:
                    b.add(r["cont_rest"], r["date"], r["sym"])
            print("    " + verdict_line(b))
        print("  verdicts (C/TEST):")
        for conf, lab in ((True, "CONFIRM"), (False, "FADE30")):
            b = Bucket(lab)
            for r in cte:
                if r["confirm"] == conf:
                    b.add(r["cont_rest"], r["date"], r["sym"])
            print("    " + verdict_line(b))

    # ---------------- PANEL D ----------------
    # ---------------- ROBUSTNESS ----------------
    print("\n" + "=" * 100)
    print("ROBUSTNESS")
    print("=" * 100)

    band = lambda r: 0.5 <= r["abs_gap_atr"] < 2.0   # noqa: E731  the TRAIN 'sweet spot'
    print("\nR1. Panel A, FADE return in the 0.50-2.00 ATR band, BY CALENDAR YEAR")
    print("    (band chosen on TRAIN; a real effect should not live in one year)")
    print("  " + HDR)
    for yr in sorted({r["date"][:4] for r in rows_a}):
        b = Bucket("year " + yr)
        for r in rows_a:
            if band(r) and r["date"][:4] == yr:
                b.add(r["fade"], r["date"], r["sym"])
        print("  " + fmt_row(b.name, b.stats()))
    for lab, rr in (("TRAIN 0.5-2.0", tr), ("TEST  0.5-2.0", te)):
        b = Bucket(lab)
        for r in rr:
            if band(r):
                b.add(r["fade"], r["date"], r["sym"])
        print("  " + fmt_row(b.name, b.stats()))
        print("    " + verdict_line(b))

    print("\nR2. Panel A, drop the single most influential SYMBOL from the 0.5-2.0 band")
    for lab, rr in (("TRAIN", tr), ("TEST", te)):
        sel = [r for r in rr if band(r)]
        base = sum(r["fade"] for r in sel) / len(sel)
        worst, wd = None, 0.0
        for s in {r["sym"] for r in sel}:
            keep = [r for r in sel if r["sym"] != s]
            m = sum(r["fade"] for r in keep) / len(keep)
            if abs(base - m) > abs(wd):
                wd, worst = base - m, s
        print("    {}: pooled={:+.3f}%  most influential symbol={} (removing it moves the mean "
              "by {:+.3f}pp -> {:+.3f}%)".format(lab, base, worst, -wd, base - wd))

    if rows_b:
        print("\nR3. Panel B, within-panel DATE split (no train period exists for this universe,")
        print("    so split its 73 dates in half: first half vs second half)")
        bdates = sorted({r["date"] for r in rows_b})
        mid = bdates[len(bdates) // 2]
        print("    first half {} .. {}   second half {} .. {}".format(
            bdates[0], mid, mid, bdates[-1]))
        for half, lab in ((lambda d: d < mid, "B-1st half"), (lambda d: d >= mid, "B-2nd half")):
            bs = []
            for j in range(len(ATR_BINS)):
                b = Bucket("{} {}".format(lab, ATR_LBL[j][9:]))
                for r in rows_b:
                    if half(r["date"]) and atr_bin(r["abs_gap_atr"]) == j:
                        b.add(r["fade"], r["date"], r["sym"])
                bs.append(b)
            table("  {} FADE by gap size".format(lab), bs)
            ms = [x.stats().get("mean", float("nan")) for x in bs]
            d_ = [ms[i + 1] - ms[i] for i in range(len(ms) - 1)]
            print("    monotone={}   means: {}".format(
                all(x > 0 for x in d_) or all(x < 0 for x in d_),
                "  ".join("{:+.3f}".format(m) for m in ms)))

        print("\nR4. Panel B, is the edge in cheap stocks where 0.25% cost is fantasy?")
        print("    FADE return in the 0.50+ ATR band, by prior-close price tercile")
        sel = [r for r in rows_b if r["abs_gap_atr"] >= 0.5]
        # attach price
        px: Dict[Tuple[str, str], float] = {}
        for p in short_paths:
            bars = load_bars(p)
            for i in range(1, len(bars)):
                px[(sym_of(p), bars[i]["date"][:10])] = bars[i - 1]["c"]
        for r in sel:
            r["px"] = px.get((r["sym"], r["date"]), float("nan"))
        sel = [r for r in sel if not math.isnan(r["px"])]
        sel.sort(key=lambda r: r["px"])
        k = len(sel) // 3
        bs = []
        for lo, hi, lab in ((0, k, "cheapest 1/3"), (k, 2 * k, "middle 1/3"), (2 * k, len(sel), "priciest 1/3")):
            chunk = sel[lo:hi]
            b = Bucket("{} (${:.2f}-${:.2f})".format(lab, chunk[0]["px"], chunk[-1]["px"]))
            for r in chunk:
                b.add(r["fade"], r["date"], r["sym"])
            bs.append(b)
        table("  Panel B, |gap|>=0.5 ATR, by price tercile", bs)
        allp = sorted(r["px"] for r in sel)
        print("    median prior close in this band: ${:.2f};  share under $5: {:.0f}%".format(
            allp[len(allp) // 2], 100.0 * sum(1 for p_ in allp if p_ < 5) / len(allp)))

    print("\n" + "=" * 100)
    print("PANEL D -- 5min_ext (premarket) files")
    print("=" * 100)
    ext = sorted(glob.glob(os.path.join(CACHE, "*_5min_ext.json")))
    days = set()
    for p in ext[:400]:
        with open(p) as fh:
            days |= {k[:10] for k in json.load(fh)["bars"].keys()}
    print("  {} symbols, but only {} DISTINCT SESSION DATES: {}".format(
        len(ext), len(days), sorted(days)))
    print("  -> The premarket files cannot support a date split or a date-clustered SE.")
    print("     Any premarket-conditioned number here would rest on {} days. Not measured."
          .format(len(days)))

    print("\n" + "=" * 100)
    print("done")
    print("=" * 100)


if __name__ == "__main__":
    main()
