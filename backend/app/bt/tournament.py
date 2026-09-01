"""Exit tournament + strategy metrics over frozen (signal, entry, path) trades.
Same entry, same size, same costs for every policy — only the exit differs."""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional

from ..strategy.exits import EXEC_MODELS, POLICIES


def wilson_lb(wins: int, n: int, z: float = 1.96) -> Optional[float]:
    if n == 0:
        return None
    p = wins / n
    den = 1 + z * z / n
    center = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((center - rad) / den, 4)


def run_policy(trades: List[dict], policy_name: str, model_name: str) -> List[dict]:
    pol = POLICIES[policy_name]
    model = EXEC_MODELS[model_name]
    out = []
    for t in trades:
        entry = t["entries"][model_name]
        res = pol["fn"](entry, t.get("stop"), t.get("target1"), t.get("target2"),
                        t["path"], model, t)
        out.append({**res, "symbol": t["symbol"], "date": t["date"],
                    "signal_time": t["signal_time"], "tier": t.get("tier"),
                    "grade": t.get("catalyst_grade"),
                    "category": t.get("catalyst_category"),
                    "setup": t.get("setup"), "rotation": t.get("rotation"),
                    "gap_pct": t.get("gap_pct"),
                    "spread_est_pct": t.get("spread_est_pct"),
                    "entry": entry})
    return out


def metrics(results: List[dict]) -> Dict[str, Any]:
    decided = [r for r in results if r["outcome"] in ("win", "loss", "neutral")]
    wins = [r for r in decided if r["outcome"] == "win"]
    losses = [r for r in decided if r["outcome"] == "loss"]
    amb = [r for r in results if r["outcome"] == "ambiguous"]
    nofill = [r for r in results if r["outcome"] == "no_fill"]
    rets = [r["pct"] for r in decided if r["pct"] is not None]
    rs = [r["r"] for r in decided if r["r"] is not None]
    n = len(decided)
    gross_w = sum(r["pct"] for r in wins if r["pct"]) if wins else 0.0
    gross_l = -sum(r["pct"] for r in losses if r["pct"]) if losses else 0.0
    eq, peak, mdd = 0.0, 0.0, 0.0
    for r in sorted(decided, key=lambda x: (x["date"], x["signal_time"])):
        eq += (r["pct"] or 0)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    exp_min = [r["t_min"] for r in decided if r.get("t_min") is not None]
    top5 = sorted((r["pct"] or 0 for r in decided), reverse=True)[:5]
    total_pos = sum(p for p in (r["pct"] or 0 for r in decided) if p > 0)
    wr = len(wins) / n if n else None
    return {
        "n": n, "n_signals": len(results), "wins": len(wins), "losses": len(losses),
        "neutral": n - len(wins) - len(losses),
        "ambiguous": len(amb), "no_fill": len(nofill),
        "ambiguous_rate": round(len(amb) / len(results), 3) if results else None,
        "fill_rate": round((len(results) - len(nofill)) / len(results), 3) if results else None,
        "win_rate": round(wr, 4) if wr is not None else None,
        "win_rate_lb": wilson_lb(len(wins), n),
        "expectancy_pct": round(sum(rets) / n, 3) if n else None,
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "avg_win_pct": round(gross_w / len(wins), 3) if wins else None,
        "avg_loss_pct": round(-gross_l / len(losses), 3) if losses else None,
        "payoff": round((gross_w / len(wins)) / (gross_l / len(losses)), 2)
                  if wins and losses and gross_l > 0 else None,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else
                         (999.0 if gross_w > 0 else None),
        "median_pct": round(sorted(rets)[len(rets) // 2], 3) if rets else None,
        "max_drawdown_pct": round(mdd, 2),
        "avg_exposure_min": round(sum(exp_min) / len(exp_min), 1) if exp_min else None,
        "ret_per_min": round(sum(rets) / max(1e-9, sum(exp_min)), 4)
                       if rets and exp_min else None,
        "top5_share": round(sum(top5) / total_pos, 3) if total_pos > 0 else None,
    }


def bootstrap_ci(results: List[dict], stat: str = "expectancy_pct",
                 iters: int = 500, seed: int = 7) -> Optional[Dict[str, float]]:
    decided = [r for r in results if r["outcome"] in ("win", "loss", "neutral")
               and r["pct"] is not None]
    if len(decided) < 8:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        sample = [rng.choice(decided) for _ in decided]
        if stat == "expectancy_pct":
            vals.append(sum(r["pct"] for r in sample) / len(sample))
        elif stat == "win_rate":
            vals.append(sum(1 for r in sample if r["outcome"] == "win") / len(sample))
    vals.sort()
    return {"lo": round(vals[int(0.025 * iters)], 3),
            "hi": round(vals[int(0.975 * iters)], 3)}


def mfe_decay(trades: List[dict], model_name: str = "baseline",
              grid: int = 5, upto: int = 120) -> List[dict]:
    """Average unrealized % at each minute after entry — reveals whether profits
    peak in the first minutes or build into the open."""
    out = []
    for m in range(grid, upto + 1, grid):
        vals, mfes = [], []
        for t in trades:
            entry = t["entries"][model_name]
            seen = [b for b in t["path"] if b["t_min"] <= m]
            if not seen:
                continue
            vals.append((seen[-1]["c"] - entry) / entry * 100.0)
            mfes.append((max(b["h"] for b in seen) - entry) / entry * 100.0)
        if vals:
            out.append({"minute": m, "avg_unrealized_pct": round(sum(vals) / len(vals), 3),
                        "avg_mfe_pct": round(sum(mfes) / len(mfes), 3), "n": len(vals)})
    return out


def segment(results: List[dict], key) -> Dict[str, Any]:
    groups: Dict[str, List[dict]] = {}
    for r in results:
        groups.setdefault(str(key(r)), []).append(r)
    return {k: metrics(v) for k, v in sorted(groups.items())}


def tournament(trades: List[dict], models=("optimistic", "baseline", "pessimistic"),
               hist_only: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, spec in POLICIES.items():
        if hist_only and not spec.get("hist_ok"):
            out[name] = {"family": spec["family"],
                         "unavailable": "finer than 5-min bars — forward/live only"}
            continue
        row: Dict[str, Any] = {"family": spec["family"]}
        for m in models:
            res = run_policy(trades, name, m)
            row[m] = metrics(res)
        base_res = run_policy(trades, name, "baseline")
        row["ci_expectancy"] = bootstrap_ci(base_res)
        row["by_tier"] = segment(base_res, lambda r: r.get("tier"))
        row["by_grade"] = segment(base_res, lambda r: r.get("grade"))
        out[name] = row
    return out
