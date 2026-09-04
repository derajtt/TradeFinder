"""probe_vol_of_vol.py -- does COMPRESSION IN THE DISPERSION of 5-minute returns
precede directional moves?

Effect under test
-----------------
For each symbol-day compute sigma_d = stdev of that day's 5-minute returns.
Then vov_d = stdev of {sigma_{d-9} .. sigma_d}  (10-day vol-OF-vol).
Bucket days by vov percentile; measure NEXT day's absolute move and signed move.

Hypothesis: LOW vol-of-vol precedes LARGER absolute moves regardless of sign
(a straddle-like/magnitude signal, not a directional one).

Measurement rules honoured here
-------------------------------
* Split by DATE. TRAIN = signal date < 2025-01-01. TEST = >= 2025-01-01.
  Every knob is chosen on TRAIN; TEST is computed once and only reported.
* Percentile ranks are CAUSAL: vov_d is ranked against the trailing 120 vov
  values of the SAME symbol (>=60 required). No full-sample ranking.
* Standard errors are DATE-CLUSTERED. The usable 5-minute universe is 6
  co-moving US equity/ETF names, so n observations are NOT independent; the
  naive sd/sqrt(n) would be fake precision. We report both and lead with the
  clustered one.
* Every bucket reports n, mean, median, sd, se, and DISTINCT SESSION DATES.
* Outlier dependence: 5%/95% trimmed mean, plus a drop-the-worst-date check.
* Confound control: raw vov is mechanically correlated with the vol LEVEL, so
  we also bucket on cv = vov / mean(sigma) (level-free) and double-sort vov
  inside sigma-level terciles. Otherwise "low vol-of-vol" is just "low vol".
* Breadth: only 6 five-minute series exist with real history, so a daily-bar
  Parkinson proxy for sigma is validated against the true 5-minute sigma and
  then run across 28 equity names x ~900 dates as an independent breadth test.

Run:
    cd /Users/blackbox/TradeFinder/backend
    ../.venv/bin/python scripts/research/probe_vol_of_vol.py
"""
import datetime
import glob
import json
import math
import os
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

CACHE = "/Users/blackbox/TradeFinder/data/rev_cache"
SPLIT = "2025-01-01"          # signal dates < SPLIT are TRAIN
VOV_WINDOW = 10               # days of sigma in the vol-of-vol stdev
RANK_LOOKBACK = 120           # trailing window for the causal percentile rank
RANK_MIN = 60                 # minimum trailing history before a rank is valid
COST_PCT = 0.25               # round-trip slippage + spread proxy, in percent
RTH_OPEN, RTH_CLOSE = 570, 960    # ET minutes: 09:30 .. 16:00

# The 5-minute files with real history. The other 191 *_5min_ext.json files
# hold 3 days each (2026-09-01..03) -- unusable for a 10-day rolling window.
EQ_5MIN = ["AAPL", "MARA", "NVDA", "QQQ", "SPY", "TSLA"]
CRYPTO_5MIN = ["BTCUSD", "ETHUSD"]


# ----------------------------------------------------------------- utilities
def load_bars(path: str) -> List[dict]:
    with open(path) as fh:
        return sorted(json.load(fh)["bars"].values(), key=lambda b: b["time"])


def minute_of_day(date_str: str) -> int:
    """Bar 'date' strings are already ET wall clock ('2024-01-02 09:30:00')."""
    return int(date_str[11:13]) * 60 + int(date_str[14:16])


def sd(xs: Sequence[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def trimmed_mean(xs: Sequence[float], frac: float = 0.05) -> float:
    """Mean after dropping the top and bottom `frac` of observations."""
    if len(xs) < 20:
        return float("nan")
    s = sorted(xs)
    k = int(len(s) * frac)
    core = s[k:len(s) - k] if k else s
    return mean(core)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation (statistics.correlation is 3.10+; this venv is 3.9)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def pct_rank(value: float, history: Sequence[float]) -> float:
    """Fraction of the trailing history strictly below `value`, in [0, 1]."""
    return sum(1 for h in history if h < value) / len(history)


def clustered_se(vals: Sequence[float], dates: Sequence[str]) -> Tuple[float, int]:
    """SE of the mean with observations clustered by session date.

    Equal-weights dates: same-day observations across 6 co-moving symbols carry
    roughly one independent shock, not six.
    """
    by_date: Dict[str, List[float]] = {}
    for v, d in zip(vals, dates):
        by_date.setdefault(d, []).append(v)

    date_means = [mean(v) for v in by_date.values()]
    if len(date_means) < 2:
        return float("nan"), len(date_means)
    return sd(date_means) / math.sqrt(len(date_means)), len(date_means)


def drop_worst_date(vals: Sequence[float], dates: Sequence[str]) -> Tuple[float, str]:
    """Recompute the mean after removing the single most influential date."""
    by_date: Dict[str, List[float]] = {}
    for v, d in zip(vals, dates):
        by_date.setdefault(d, []).append(v)
    grand = mean(vals)
    worst_date, worst_shift, worst_mean = "", -1.0, grand
    for d in by_date:
        rest = [v for v, dd in zip(vals, dates) if dd != d]
        if len(rest) < 2:
            continue
        m = mean(rest)
        if abs(m - grand) > worst_shift:
            worst_shift, worst_date, worst_mean = abs(m - grand), d, m
    return worst_mean, worst_date


class Stat(object):
    """Summary of one bucket for one outcome column."""

    def __init__(self, vals: Sequence[float], dates: Sequence[str]) -> None:
        self.n = len(vals)
        self.mean = mean(vals) if vals else float("nan")
        self.median = statistics.median(vals) if vals else float("nan")
        self.sd = sd(vals)
        self.se_naive = self.sd / math.sqrt(self.n) if self.n > 1 else float("nan")
        self.se, self.n_dates = clustered_se(vals, dates)
        self.trimmed = trimmed_mean(vals)
        self.t = self.mean / self.se if self.se and self.se == self.se else float("nan")

    def row(self, label: str) -> str:
        return ("  %-14s n=%5d dates=%4d  mean=%+7.3f  med=%+7.3f  sd=%6.3f  "
                "se=%6.3f  t=%+6.2f  trim5%%=%+7.3f"
                % (label, self.n, self.n_dates, self.mean, self.median,
                   self.sd, self.se, self.t, self.trimmed))


# ------------------------------------------------------- feature construction
def daily_sigma_from_5min(symbol: str, rth_only: bool) -> Dict[str, dict]:
    """Per-session dispersion of 5-minute returns + that session's OHLC.

    Returns {date: {sigma, o, h, l, c, nbars}} built only from 5-minute bars, so
    the session OHLC and the dispersion always describe the same window.
    """
    path = os.path.join(CACHE, "%s_5min.json" % symbol)
    if not os.path.exists(path):
        return {}
    by_day: Dict[str, List[dict]] = {}
    for b in load_bars(path):
        if rth_only:
            m = minute_of_day(b["date"])
            if m < RTH_OPEN or m >= RTH_CLOSE:
                continue
        by_day.setdefault(b["date"][:10], []).append(b)

    need = 60 if rth_only else 240      # 78 RTH bars/day equities, 288 crypto
    out: Dict[str, dict] = {}
    for day, bars in by_day.items():
        if len(bars) < need:
            continue
        bars.sort(key=lambda b: b["time"])
        rets = []
        for prev, cur in zip(bars, bars[1:]):
            if prev["c"] > 0:
                rets.append(cur["c"] / prev["c"] - 1.0)
        if len(rets) < need - 1:
            continue
        out[day] = {
            "sigma": sd(rets) * 100.0,             # percent per 5 minutes
            "o": bars[0]["o"],
            "h": max(b["h"] for b in bars),
            "l": min(b["l"] for b in bars),
            "c": bars[-1]["c"],
            "nbars": len(bars),
        }
    return out


def daily_sigma_parkinson(symbol: str) -> Dict[str, dict]:
    """Daily-bar stand-in for intraday dispersion: Parkinson range volatility.

    sigma_P = ln(h/l) / (2*sqrt(ln 2)) -- the classic high/low estimator of the
    same diffusion parameter the 5-minute stdev estimates. Validated below.
    """
    path = os.path.join(CACHE, "%s_1day.json" % symbol)
    if not os.path.exists(path):
        return {}
    out: Dict[str, dict] = {}
    for b in load_bars(path):
        if b["l"] <= 0 or b["h"] <= 0 or b["o"] <= 0 or b["c"] <= 0:
            continue
        out[b["date"][:10]] = {
            "sigma": math.log(b["h"] / b["l"]) / (2.0 * math.sqrt(math.log(2.0))) * 100.0,
            "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "nbars": 1,
        }
    return out


def build_observations(series: Dict[str, Dict[str, dict]],
                       max_gap_days: int = 5) -> List[dict]:
    """One row per (symbol, signal day d) with next-session outcomes.

    Every field is causal as of the close of day d.
    """
    rows: List[dict] = []
    for sym, per_day in series.items():
        days = sorted(per_day)
        sig = [per_day[d]["sigma"] for d in days]
        vov_hist: List[float] = []          # trailing vov values for ranking
        for i, day in enumerate(days):
            if i + 1 >= len(days):
                continue
            if i + 1 < VOV_WINDOW:
                continue
            win_days = days[i - VOV_WINDOW + 1:i + 1]
            # the 10 sigma values must be 10 consecutive sessions in this file
            d0 = datetime.date.fromisoformat(win_days[0])
            d1 = datetime.date.fromisoformat(win_days[-1])
            if (d1 - d0).days > VOV_WINDOW * 2 + 6:
                continue
            win = sig[i - VOV_WINDOW + 1:i + 1]
            vov = sd(win)
            lvl = mean(win)
            if lvl <= 0:
                continue

            nxt = days[i + 1]
            gap = (datetime.date.fromisoformat(nxt) - datetime.date.fromisoformat(day)).days
            if gap > max_gap_days:
                vov_hist.append(vov)
                continue

            cur, nx = per_day[day], per_day[nxt]
            rank = (pct_rank(vov, vov_hist[-RANK_LOOKBACK:])
                    if len(vov_hist) >= RANK_MIN else None)
            lvl_hist_ok = len(vov_hist) >= RANK_MIN
            vov_hist.append(vov)
            if rank is None:
                continue

            sgn_cc = (nx["c"] / cur["c"] - 1.0) * 100.0
            sgn_oc = (nx["c"] / nx["o"] - 1.0) * 100.0
            rows.append({
                "sym": sym, "date": day, "next": nxt,
                "vov": vov, "sigma": cur["sigma"], "level": lvl,
                "cv": vov / lvl,
                "rank": rank,
                "sgn_cc": sgn_cc, "abs_cc": abs(sgn_cc),
                "sgn_oc": sgn_oc, "abs_oc": abs(sgn_oc),
                "rng": (nx["h"] - nx["l"]) / nx["o"] * 100.0,
                "train": day < SPLIT,
                "_lvl_ok": lvl_hist_ok,
            })
    rows.sort(key=lambda r: (r["date"], r["sym"]))
    return rows


# ------------------------------------------------------------------ reporting
def bucketize(rows: List[dict], key: str, nb: int = 5) -> List[List[dict]]:
    """Split into nb buckets by a [0,1] causal percentile field."""
    out: List[List[dict]] = [[] for _ in range(nb)]
    for r in rows:
        b = min(int(r[key] * nb), nb - 1)
        out[b].append(r)
    return out


def bucket_table(rows: List[dict], key: str, outcome: str, title: str,
                 nb: int = 5) -> List[Stat]:
    print("\n%s   [outcome = %s, %%]" % (title, outcome))
    buckets = bucketize(rows, key, nb)
    stats = []
    for i, b in enumerate(buckets):
        if not b:
            stats.append(None)
            print("  Q%d  (empty)" % (i + 1))
            continue
        st = Stat([r[outcome] for r in b], [r["date"] for r in b])
        stats.append(st)
        print(st.row("Q%d %s" % (i + 1, "(low vov)" if i == 0
                                 else "(high vov)" if i == nb - 1 else "")))
    good = [s for s in stats if s]
    if len(good) == nb:
        ms = [s.mean for s in good]
        up = all(ms[i] <= ms[i + 1] for i in range(nb - 1))
        dn = all(ms[i] >= ms[i + 1] for i in range(nb - 1))
        print("  monotone across buckets: %s%s"
              % (up or dn, " (rising)" if up else " (falling)" if dn else ""))
        print("  spread Q1-Q5 = %+.3f" % (ms[0] - ms[-1]))
    return stats


def q1_forensics(rows: List[dict], key: str, outcome: str, nb: int = 5) -> None:
    """The date-concentration and outlier checks that kill fake effects."""
    b = bucketize(rows, key, nb)[0]
    if not b:
        return
    vals = [r[outcome] for r in b]
    dates = [r["date"] for r in b]
    m = mean(vals)
    dropped, worst = drop_worst_date(vals, dates)
    print("  Q1 forensics [%s]: mean=%+.3f  trim5%%=%+.3f  drop-worst-date(%s)=%+.3f"
          % (outcome, m, trimmed_mean(vals), worst, dropped))
    by_date: Dict[str, List[float]] = {}
    for v, d in zip(vals, dates):
        by_date.setdefault(d, []).append(v)
    dm = sorted(((mean(v), d) for d, v in by_date.items()), reverse=True)
    print("      top-3 dates by mean: %s" % ", ".join("%s %+.2f" % (d, x) for x, d in dm[:3]))
    frac_pos = sum(1 for v in vals if v > 0) / len(vals)
    print("      fraction positive = %.3f (n=%d)" % (frac_pos, len(vals)))


def section(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


# ----------------------------------------------------------------------- main
def main() -> None:
    print("VOL-OF-VOL PROBE -- dispersion compression as a next-day signal")
    print("TRAIN: signal date < %s     TEST: signal date >= %s" % (SPLIT, SPLIT))
    print("cost hurdle: %.2f%% per trade" % COST_PCT)

    # ---------- data inventory (state the limits up front) ----------
    section("0. DATA INVENTORY")
    n5 = len(glob.glob(os.path.join(CACHE, "*_5min*.json")))
    usable = []
    for f in glob.glob(os.path.join(CACHE, "*_5min*.json")):
        with open(f) as fh:
            days = set(v["date"][:10] for v in json.load(fh)["bars"].values())
        if len(days) >= 40:
            usable.append((os.path.basename(f), len(days)))
    print("5-minute files on disk: %d" % n5)
    print("5-minute files with >=40 distinct days: %d  -> %s"
          % (len(usable), ", ".join(sorted(u[0].replace("_5min.json", "") for u in usable))))
    print("The other %d hold 3 sessions each (2026-09-01..03): a 10-day rolling"
          % (n5 - len(usable)))
    print("vol-of-vol cannot be formed from them at all.")

    eq = {s: daily_sigma_from_5min(s, rth_only=True) for s in EQ_5MIN}
    eq = {s: v for s, v in eq.items() if v}
    for s in sorted(eq):
        ds = sorted(eq[s])
        print("  %-6s sessions=%d  %s .. %s" % (s, len(ds), ds[0], ds[-1]))

    # ---------- primary: true 5-minute dispersion ----------
    section("1. PRIMARY -- true 5-minute return dispersion, 6 US equity/ETF names")
    rows = build_observations(eq)
    tr = [r for r in rows if r["train"]]
    te = [r for r in rows if not r["train"]]
    print("observations: total=%d  TRAIN=%d (%d dates)  TEST=%d (%d dates)"
          % (len(rows), len(tr), len(set(r["date"] for r in tr)),
             len(te), len(set(r["date"] for r in te))))
    print("NOTE: only %d distinct TRAIN dates exist and the 6 names co-move, so"
          % len(set(r["date"] for r in tr)))
    print("date-clustered SEs (reported as 'se') are the honest ones; the naive")
    print("sd/sqrt(n) would overstate precision by roughly sqrt(6).")

    print("\n--- TRAIN: bucket by causal trailing percentile of RAW vol-of-vol ---")
    for oc in ["abs_cc", "abs_oc", "rng", "sgn_cc", "sgn_oc"]:
        bucket_table(tr, "rank", oc, "TRAIN vov-quintile")
    q1_forensics(tr, "rank", "abs_cc")
    q1_forensics(tr, "rank", "sgn_cc")
    q1_forensics(tr, "rank", "sgn_oc")

    # confound: is this just the LEVEL of vol?
    section("2. IS IT VOL-OF-VOL, OR JUST THE LEVEL OF VOL?  (TRAIN)")
    print("corr(vov, sigma level) over TRAIN = %.3f"
          % pearson([r["vov"] for r in tr], [r["level"] for r in tr]))
    lvl_rank_rows = []
    by_sym: Dict[str, List[dict]] = {}
    for r in rows:
        by_sym.setdefault(r["sym"], []).append(r)
    for sym, rs in by_sym.items():
        rs.sort(key=lambda r: r["date"])
        hist: List[float] = []
        for r in rs:
            if len(hist) >= RANK_MIN:
                r["lvl_rank"] = pct_rank(r["level"], hist[-RANK_LOOKBACK:])
                lvl_rank_rows.append(r)
            hist.append(r["level"])
    tr_l = [r for r in lvl_rank_rows if r["train"]]
    bucket_table(tr_l, "lvl_rank", "abs_cc", "TRAIN sigma-LEVEL quintile (confound benchmark)")

    # level-free variant: coefficient of variation of sigma
    for sym, rs in by_sym.items():
        rs.sort(key=lambda r: r["date"])
        hist = []
        for r in rs:
            if len(hist) >= RANK_MIN:
                r["cv_rank"] = pct_rank(r["cv"], hist[-RANK_LOOKBACK:])
            hist.append(r["cv"])
    tr_cv = [r for r in tr if "cv_rank" in r]
    bucket_table(tr_cv, "cv_rank", "abs_cc", "TRAIN cv=vov/level quintile (level-free)")
    bucket_table(tr_cv, "cv_rank", "sgn_cc", "TRAIN cv=vov/level quintile (level-free)")

    # double sort: vov inside a sigma-level tercile
    print("\n--- TRAIN double sort: vov tercile INSIDE each sigma-level tercile ---")
    print("    (does vov add anything once the vol level is held fixed?)")
    have = [r for r in tr if "lvl_rank" in r]
    for li in range(3):
        sub = [r for r in have if min(int(r["lvl_rank"] * 3), 2) == li]
        if len(sub) < 60:
            continue
        vs = sorted(r["rank"] for r in sub)
        c1, c2 = vs[len(vs) // 3], vs[2 * len(vs) // 3]
        print("  sigma-level tercile %d (n=%d):" % (li + 1, len(sub)))
        for name, sel in (("vov low ", [r for r in sub if r["rank"] <= c1]),
                          ("vov mid ", [r for r in sub if c1 < r["rank"] <= c2]),
                          ("vov high", [r for r in sub if r["rank"] > c2])):
            if not sel:
                continue
            st = Stat([r["abs_cc"] for r in sel], [r["date"] for r in sel])
            print("   " + st.row(name))

    # ---------- TEST ----------
    section("3. TEST (held out, 2025-01-01 onward) -- computed once, never tuned on")
    for oc in ["abs_cc", "abs_oc", "sgn_cc", "sgn_oc"]:
        bucket_table(te, "rank", oc, "TEST vov-quintile")
    q1_forensics(te, "rank", "abs_cc")
    q1_forensics(te, "rank", "sgn_oc")

    # ---------- tradeability ----------
    section("4. TRADEABILITY OF THE LONG-ONLY VERSION")
    for tag, dat in (("TRAIN", tr), ("TEST", te)):
        q1 = bucketize(dat, "rank", 5)[0]
        if not q1:
            continue
        for oc, desc in (("sgn_oc", "buy next open / sell next close"),
                         ("sgn_cc", "buy this close / sell next close")):
            st = Stat([r[oc] for r in q1], [r["date"] for r in q1])
            print("%s  Q1(low vov) %-32s mean=%+.3f%%  se=%.3f  t=%+.2f  "
                  "net of %.2f%% = %+.3f%%  n=%d dates=%d"
                  % (tag, desc, st.mean, st.se, st.t, COST_PCT,
                     st.mean - COST_PCT, st.n, st.n_dates))

    # ---------- crypto sanity ----------
    section("5. CRYPTO 5-MINUTE (separate universe, 24h sessions)")
    cr = {s: daily_sigma_from_5min(s, rth_only=False) for s in CRYPTO_5MIN}
    cr = {s: v for s, v in cr.items() if v}
    crows = build_observations(cr, max_gap_days=2)
    ctr = [r for r in crows if r["train"]]
    cte = [r for r in crows if not r["train"]]
    print("crypto obs: TRAIN=%d (%d dates)  TEST=%d (%d dates)"
          % (len(ctr), len(set(r["date"] for r in ctr)),
             len(cte), len(set(r["date"] for r in cte))))
    if ctr:
        bucket_table(ctr, "rank", "abs_cc", "CRYPTO TRAIN vov-quintile")
        bucket_table(ctr, "rank", "sgn_cc", "CRYPTO TRAIN vov-quintile")
    if cte:
        bucket_table(cte, "rank", "abs_cc", "CRYPTO TEST vov-quintile")

    # ---------- breadth via a validated daily proxy ----------
    section("6. BREADTH TEST -- daily Parkinson proxy for intraday dispersion")
    print("Six symbols is too thin to trust. Validate a daily-bar proxy against")
    print("the true 5-minute sigma on the overlap, then run it wide.")
    for s in sorted(eq):
        pk = daily_sigma_parkinson(s)
        common = sorted(set(eq[s]) & set(pk))
        if len(common) < 50:
            print("  %-6s overlap too small (%d)" % (s, len(common)))
            continue
        a = [eq[s][d]["sigma"] for d in common]
        b = [pk[d]["sigma"] for d in common]
        print("  %-6s corr(5min sigma, Parkinson sigma) = %.3f over %d sessions"
              % (s, pearson(a, b), len(common)))

    wide: Dict[str, Dict[str, dict]] = {}
    for f in sorted(glob.glob(os.path.join(CACHE, "*_1day.json"))):
        sym = os.path.basename(f).replace("_1day.json", "")
        if sym.endswith("USD"):
            continue                      # keep the breadth test all-equity
        ser = daily_sigma_parkinson(sym)
        if len(ser) >= 400:
            wide[sym] = ser
    print("\nbreadth universe: %d equity names with >=400 daily bars" % len(wide))
    wrows = build_observations(wide)
    wtr = [r for r in wrows if r["train"]]
    wte = [r for r in wrows if not r["train"]]
    print("obs: TRAIN=%d (%d dates, %d syms)  TEST=%d (%d dates)"
          % (len(wtr), len(set(r["date"] for r in wtr)),
             len(set(r["sym"] for r in wtr)),
             len(wte), len(set(r["date"] for r in wte))))
    for oc in ["abs_cc", "sgn_cc"]:
        bucket_table(wtr, "rank", oc, "BREADTH TRAIN vov-quintile")
    q1_forensics(wtr, "rank", "abs_cc")
    q1_forensics(wtr, "rank", "sgn_cc")
    for oc in ["abs_cc", "sgn_cc"]:
        bucket_table(wte, "rank", oc, "BREADTH TEST vov-quintile")
    q1_forensics(wte, "rank", "sgn_cc")

    section("7. DIRECTION VS MAGNITUDE -- the decisive question")
    for tag, dat in (("PRIMARY TRAIN", tr), ("PRIMARY TEST", te),
                     ("BREADTH TRAIN", wtr), ("BREADTH TEST", wte)):
        if not dat:
            continue
        q1 = bucketize(dat, "rank", 5)[0]
        q5 = bucketize(dat, "rank", 5)[4]
        a1 = Stat([r["abs_cc"] for r in q1], [r["date"] for r in q1])
        a5 = Stat([r["abs_cc"] for r in q5], [r["date"] for r in q5])
        s1 = Stat([r["sgn_cc"] for r in q1], [r["date"] for r in q1])
        hit = sum(1 for r in q1 if r["sgn_cc"] > 0) / len(q1)
        print("%-14s  |move| Q1=%.3f Q5=%.3f (Q1-Q5=%+.3f)   signed Q1=%+.3f "
              "(t=%+.2f, hit=%.3f)"
              % (tag, a1.mean, a5.mean, a1.mean - a5.mean, s1.mean, s1.t, hit))
    print("\nA magnitude-only effect is NOT tradeable long-only on this platform:")
    print("there is no options leg here, so |move| without a sign earns nothing.")


if __name__ == "__main__":
    main()
