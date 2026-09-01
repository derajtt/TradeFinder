"""Post-run analysis when the optimizer honestly refuses to pick a winner:
tournament + diagnostics on the FULL unselected baseline pool (v2 structural
gates only — no entry-grid selection, so no grid-selection bias)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.bt.tournament import bootstrap_ci, metrics, mfe_decay, run_policy, segment, tournament
from app.bt.optimize import capacity_table, feature_importance
from app.strategy.versions import VERSIONS

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")
dev = json.load(open(f"{OUT}/dataset_dev.json"))["trades"]
val = json.load(open(f"{OUT}/dataset_val.json"))["trades"]
pool = dev + val
print(f"pool: {len(dev)} dev + {len(val)} val = {len(pool)} baseline trades")

t = tournament(pool)
rows = [(k, v["baseline"]) for k, v in t.items() if "baseline" in v and v["baseline"]["n"]]
rows.sort(key=lambda r: (r[1]["win_rate_lb"] or 0), reverse=True)
print(f"\n{'policy':22} {'n':>3} {'WR':>5} {'LB':>5} {'exp%':>7} {'PF':>5} {'DD':>7} {'amb':>3}")
for name, m in rows[:12]:
    print(f"{name:22} {m['n']:>3} {str(m['win_rate']):>5} {str(m['win_rate_lb']):>5} "
          f"{str(m['expectancy_pct']):>7} {str(m['profit_factor']):>5} "
          f"{str(m['max_drawdown_pct']):>7} {m['ambiguous']:>3}")
decay = mfe_decay(pool)
fi = feature_importance(pool)
cap = capacity_table(pool, "exit_935")
by_grade = segment(run_policy(pool, "exit_935", "baseline"), lambda r: r.get("grade"))
by_tier = segment(run_policy(pool, "exit_935", "baseline"), lambda r: r.get("tier"))
by_time = segment(run_policy(pool, "exit_935", "baseline"),
                  lambda r: r["signal_time"][11:13] if r.get("signal_time") else "?")

result = {
    "versions": VERSIONS, "cohort": "dev+val baseline (NO entry-grid selection)",
    "pool_n": len(pool),
    "verdict": ("Optimizer honestly refused: 35 gate-passing trades over 84 "
                "sessions cannot support a 300-config search (accuracy floor "
                "unmet; PBO 0.3). The frozen v2 structural gates remain PRIMARY "
                "in EXPERIMENTAL paper mode; forward paper evidence decides."),
    "tournament": t, "mfe_decay": decay, "feature_importance": fi,
    "capacity": cap, "by_grade": by_grade, "by_tier": by_tier, "by_time": by_time,
    "search": {"configs_tested": 300, "converged_because":
               "no config passed the accuracy floor on stage-1 screen",
               "pbo": 0.3},
    "splits": {"dev": ["2026-03-02", "2026-05-15"], "val": ["2026-05-18", "2026-06-30"],
               "holdout": ["2026-07-01", "2026-08-31"]},
    "coverage_notes": [
        "1-minute history UNAVAILABLE on plan — replay ran on 5-minute bars (premarket verified from 4:00 AM)",
        "Discovery: SEC-filing acceptance timestamps + prev-day movers (global historical news is thin on plan)",
        "Historical float CURRENT-VALUE ONLY — rotation labeled ESTIMATED_CURRENT_FLOAT",
        "Historical bid/ask UNAVAILABLE — tier-based spread estimates; fills at next-bar open + slippage models",
        "~0.4 gate-passing trades/session: the structural gates are strict by design; n=35 is a small sample and every number below carries wide uncertainty",
    ],
    "primary": {"strategy_version": VERSIONS["strategy_version"] + "+baseline",
                "entry": "frozen v2 structural gates (no optimized narrowing)",
                "exit": "current frozen paper policy (partial 1R -> 2R -> time ladder)",
                "mode": "experimental_paper", "holdout_pass": None},
}
json.dump(result, open(f"{OUT}/final_report.json", "w"), default=str, indent=1)
print("\nMFE decay (first bars):", [(d["minute"], d["avg_mfe_pct"]) for d in decay[:6]])
print("saved final_report.json")
