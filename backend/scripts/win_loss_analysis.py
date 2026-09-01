"""Win/loss differentiator study over all scalper replay trades (dev+val+holdout).
Outcome basis: exit_935 baseline (deterministic time exit — no target-choice bias).
Findings become live shadow challengers, never silent primary changes."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.bt.tournament import run_policy

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")
pool = []
for tag in ("dev", "val", "holdout"):
    try:
        pool += json.load(open(f"{OUT}/dataset_{tag}.json"))["trades"]
    except FileNotFoundError:
        pass
res = run_policy(pool, "exit_935", "baseline")
rows = [dict(t, outcome=r["outcome"], pct=r["pct"]) for t, r in zip(pool, res)
        if r["outcome"] in ("win", "loss", "neutral") and r["pct"] is not None]
print(f"labeled trades: {len(rows)} (of {len(pool)} pool)")

def bucket(rows, keyfn, name, min_n=6):
    groups = {}
    for r in rows:
        groups.setdefault(str(keyfn(r)), []).append(r)
    out = []
    for k, g in sorted(groups.items()):
        if len(g) < min_n:
            out.append({"bucket": k, "n": len(g), "verdict": "insufficient_n"})
            continue
        wins = sum(1 for x in g if x["outcome"] == "win")
        exp = sum(x["pct"] for x in g) / len(g)
        out.append({"bucket": k, "n": len(g), "wr": round(wins / len(g), 2),
                    "exp_pct": round(exp, 2)})
    return {"dimension": name, "buckets": out}

def gapb(r):
    g = r.get("gap_pct") or 0
    return "<10" if g < 10 else "10-25" if g < 25 else "25-50" if g < 50 else "50+"
def dvb(r):
    v = r.get("pm_dollar_volume") or 0
    return "<100k" if v < 1e5 else "100-500k" if v < 5e5 else "500k+"
def rotb(r):
    x = r.get("rotation")
    if x is None: return "unknown"
    return "<5%" if x < 0.05 else "5-20%" if x < 0.2 else "20-100%" if x < 1 else "100%+"
def hourb(r):
    return r["signal_time"][11:13] if r.get("signal_time") else "?"

dims = [bucket(rows, lambda r: r.get("catalyst_category"), "catalyst_category"),
        bucket(rows, lambda r: r.get("catalyst_grade"), "catalyst_grade"),
        bucket(rows, gapb, "gap_bucket"),
        bucket(rows, dvb, "pm_dollar_volume"),
        bucket(rows, rotb, "float_rotation(ESTIMATED)"),
        bucket(rows, lambda r: r.get("tier"), "price_tier"),
        bucket(rows, hourb, "signal_hour_et"),
        bucket(rows, lambda r: r.get("setup"), "entry_setup")]
overall_exp = sum(r["pct"] for r in rows) / len(rows) if rows else 0
findings = []
for d in dims:
    for b in d["buckets"]:
        if "exp_pct" in b and b["n"] >= 8 and abs(b["exp_pct"] - overall_exp) >= 1.5:
            findings.append({"dimension": d["dimension"], **b,
                             "delta_vs_overall": round(b["exp_pct"] - overall_exp, 2)})
findings.sort(key=lambda f: -abs(f["delta_vs_overall"]))
report = {"labeled_n": len(rows), "overall_exp_pct": round(overall_exp, 2),
          "outcome_basis": "exit_935 baseline execution",
          "dimensions": dims, "findings": findings,
          "caveat": ("n=%d total — every finding is a HYPOTHESIS for forward "
                     "shadow testing, not a proven edge. Splits below 6 trades "
                     "are marked insufficient." % len(rows))}
json.dump(report, open(f"{OUT}/win_loss_analysis.json", "w"), indent=1)
print(f"overall exp: {overall_exp:.2f}%")
for f in findings[:8]:
    print(f"  {f['dimension']:28} {f['bucket']:16} n={f['n']:>3} wr={f.get('wr')} "
          f"exp={f['exp_pct']}% (Δ{f['delta_vs_overall']:+.1f})")
