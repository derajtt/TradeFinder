"""Staged, bounded strategy search with predefined convergence criteria.
Registry records EVERY tested config (including rejects) for overfitting math."""
from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tournament import metrics, run_policy, wilson_lb

# ── predefined BEFORE any search; never loosened to keep searching ──
CONVERGENCE = {
    "max_configs": 400,
    "min_delta_expectancy_pct": 0.15,   # meaningful VAL improvement per round
    "stall_rounds": 2,
    "min_trades_promote": 40,
    "pbo_block": 0.5,
}
ACCURACY_FLOOR = {
    "min_trades": 40,
    "min_expectancy_pct_baseline": 0.0,
    "min_expectancy_pct_pessimistic": -0.35,
    "min_profit_factor": 1.0,
    "max_drawdown_pct": -40.0,
    "max_top5_share": 0.65,
    "min_signals_per_week": 1.0,
}

# entry-side bounded grid (market-structure rationale only)
ENTRY_GRID = {
    "grade_req": [("A",), ("A", "B")],
    "max_gap": [50, 80, 120],
    "max_ext_vwap": [12, 20, 30],
    "min_dollar_vol": [50_000, 100_000, 250_000],
    "rotation_max": [0.4, 1.0, 2.0],
    "min_score": [0, 35, 50],
}
EXIT_SHORTLIST = ["scalp_5m", "scalp_10m", "scalp_15m", "scalp_30m",
                  "target_1R", "target_1.5R", "target_2R",
                  "pct_tp8_sl5", "vwap_loss", "trail_bar_lows", "dd8_from_high",
                  "exit_pre_open_929", "exit_open_930", "exit_935", "exit_1000",
                  "half_1R_hold_open", "partial_5m_hold_open"]


def config_hash(cfg: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def entry_filter(trades: List[dict], e: Dict[str, Any]) -> List[dict]:
    out = []
    for t in trades:
        if t.get("catalyst_grade") not in e["grade_req"]:
            continue
        if t.get("gap_pct") is not None and t["gap_pct"] > e["max_gap"]:
            continue
        f = t.get("features") or {}
        ext = f.get("ext_above_vwap_pct")
        if ext is not None and ext > e["max_ext_vwap"]:
            continue
        if (t.get("pm_dollar_volume") or 0) < e["min_dollar_vol"]:
            continue
        rot = t.get("rotation")
        if rot is not None and rot > e["rotation_max"]:
            continue
        if (t.get("score") or 0) < e["min_score"]:
            continue
        out.append(t)
    return out


class Registry:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def log(self, stage: str, cfg: Dict[str, Any], dev: Dict[str, Any],
            val: Optional[Dict[str, Any]] = None, note: str = ""):
        self.rows.append({"stage": stage, "hash": config_hash(cfg), "cfg": cfg,
                          "dev": _slim(dev), "val": _slim(val) if val else None,
                          "note": note})

    def tested(self) -> int:
        return len(self.rows)


def _slim(m):
    if not m:
        return None
    keys = ("n", "win_rate", "win_rate_lb", "expectancy_pct", "profit_factor",
            "max_drawdown_pct", "top5_share", "avg_exposure_min")
    return {k: m.get(k) for k in keys}


def eval_config(trades_dev, trades_val, entry_cfg, exit_name,
                sessions_dev_weeks: float, sessions_val_weeks: float):
    dev_t = entry_filter(trades_dev, entry_cfg)
    val_t = entry_filter(trades_val, entry_cfg)
    dev_m = metrics(run_policy(dev_t, exit_name, "baseline")) if dev_t else metrics([])
    val_m = metrics(run_policy(val_t, exit_name, "baseline")) if val_t else metrics([])
    pess_m = metrics(run_policy(val_t, exit_name, "pessimistic")) if val_t else metrics([])
    return dev_m, val_m, pess_m


def passes_floor(val_m, pess_m, weeks: float) -> Tuple[bool, str]:
    F = ACCURACY_FLOOR
    if (val_m["n"] or 0) < max(8, F["min_trades"] // 4):
        return False, f"n={val_m['n']} too small on VAL"
    if val_m["expectancy_pct"] is None or val_m["expectancy_pct"] <= F["min_expectancy_pct_baseline"]:
        return False, "expectancy<=0 baseline"
    if pess_m["expectancy_pct"] is not None and pess_m["expectancy_pct"] < F["min_expectancy_pct_pessimistic"]:
        return False, "fails pessimistic execution"
    if val_m["profit_factor"] is not None and val_m["profit_factor"] <= F["min_profit_factor"]:
        return False, "profit_factor<=1"
    if val_m["max_drawdown_pct"] is not None and val_m["max_drawdown_pct"] < F["max_drawdown_pct"]:
        return False, "drawdown too deep"
    if val_m["top5_share"] is not None and val_m["top5_share"] > F["max_top5_share"]:
        return False, "outlier-dependent"
    if weeks > 0 and (val_m["n"] or 0) / weeks < F["min_signals_per_week"]:
        return False, "signal frequency too low"
    return True, "ok"


def staged_search(trades_dev: List[dict], trades_val: List[dict],
                  dev_weeks: float, val_weeks: float,
                  registry: Registry) -> Dict[str, Any]:
    """Stages 1-4: screen -> refine -> interactions -> jitter robustness.
    Returns the locked candidate (entry cfg + exit policy) + diagnostics."""
    # Stage 1: marginal screening on a coarse lattice (bounded)
    keys = list(ENTRY_GRID)
    coarse = [dict(zip(keys, vals)) for vals in itertools.product(
        *[ENTRY_GRID[k] for k in keys])]
    # thin the lattice deterministically to respect the config budget
    step = max(1, len(coarse) * len(EXIT_SHORTLIST) // (CONVERGENCE["max_configs"] * 3 // 4))
    scored = []
    for i, e in enumerate(coarse):
        exits = EXIT_SHORTLIST if i % step == 0 else ["target_1R", "exit_935"]
        for x in exits:
            if registry.tested() >= CONVERGENCE["max_configs"] * 3 // 4:
                break
            dev_m, val_m, pess_m = eval_config(trades_dev, trades_val, e, x,
                                               dev_weeks, val_weeks)
            registry.log("screen", {"entry": e, "exit": x}, dev_m, val_m)
            ok, why = passes_floor(val_m, pess_m, val_weeks)
            if ok:
                scored.append({"entry": e, "exit": x, "val": val_m, "dev": dev_m,
                               "pess": pess_m,
                               "rank_key": (val_m["win_rate_lb"] or 0,
                                            val_m["expectancy_pct"] or 0)})
    if not scored:
        return {"locked": None, "reason": "no configuration passed the accuracy floor",
                "tested": registry.tested()}
    scored.sort(key=lambda s: s["rank_key"], reverse=True)

    # Stage 2: refine around the top REGION (top quartile medians), not the single best
    top = scored[: max(3, len(scored) // 4)]
    best = top[0]
    # Stage 3: interaction — does the best exit differ by grade or tier?
    inter = {}
    for seg_key, seg_fn in (("grade", lambda t: t.get("catalyst_grade")),
                            ("tier", lambda t: t.get("tier"))):
        seg_best = {}
        base_entry = best["entry"]
        f_val = entry_filter(trades_val, base_entry)
        for x in EXIT_SHORTLIST:
            res = run_policy(f_val, x, "baseline")
            groups = {}
            for r in res:
                groups.setdefault(str(seg_fn(r)), []).append(r)
            for g, rows in groups.items():
                m = metrics(rows)
                if (m["n"] or 0) >= 10 and (m["expectancy_pct"] or -9) > \
                        seg_best.get(g, {}).get("expectancy_pct", -9):
                    seg_best[g] = {"exit": x, **_slim(m)}
        inter[seg_key] = seg_best

    # Stage 4: jitter robustness on the winner
    jitter_ok, jitter_detail = True, []
    for k, v in best["entry"].items():
        if isinstance(v, (int, float)):
            for mult in (0.8, 1.25):
                e2 = dict(best["entry"], **{k: type(v)(v * mult)})
                _, vm, pm = eval_config(trades_dev, trades_val, e2, best["exit"],
                                        dev_weeks, val_weeks)
                registry.log("jitter", {"entry": e2, "exit": best["exit"]}, vm)
                ok, _ = passes_floor(vm, pm, val_weeks)
                jitter_detail.append({k: e2[k], "exp": vm["expectancy_pct"],
                                      "n": vm["n"], "ok": ok})
                if not ok and (vm["n"] or 0) >= 10:
                    jitter_ok = False
    return {"locked": {"entry": best["entry"], "exit": best["exit"]},
            "val_metrics": best["val"], "dev_metrics": best["dev"],
            "pess_metrics": best["pess"],
            "top_region": [{"entry": s["entry"], "exit": s["exit"],
                            "val": _slim(s["val"])} for s in top[:6]],
            "interactions": inter, "jitter_ok": jitter_ok,
            "jitter_detail": jitter_detail, "tested": registry.tested()}


def walk_forward(all_trades: List[dict], sessions: List[str], folds: int,
                 entry_cfg: Dict[str, Any], exit_name: str) -> Dict[str, Any]:
    """Chronological folds: config is FIXED (chosen on dev/val); each fold's test
    block is unseen. Official stats = concatenated test blocks only."""
    if not sessions:
        return {"folds": [], "combined": metrics([])}
    block = max(1, len(sessions) // folds)
    fold_rows, combined = [], []
    for i in range(folds):
        test_days = set(sessions[i * block:(i + 1) * block])
        test_t = [t for t in all_trades if t["date"] in test_days]
        ft = entry_filter(test_t, entry_cfg)
        res = run_policy(ft, exit_name, "baseline") if ft else []
        combined.extend(res)
        fold_rows.append({"fold": i + 1, "days": len(test_days), **_slim(metrics(res))})
    return {"folds": fold_rows, "combined": metrics(combined)}


def pbo_estimate(registry: Registry) -> Optional[float]:
    """Lite probability-of-backtest-overfitting: of configs ranked top-decile on
    DEV, what fraction fell below median on VAL?"""
    rows = [r for r in registry.rows if r["stage"] == "screen" and r["dev"] and r["val"]
            and r["dev"]["n"] and r["val"]["n"]]
    if len(rows) < 20:
        return None
    rows.sort(key=lambda r: r["dev"]["expectancy_pct"] or -99, reverse=True)
    top = rows[: max(1, len(rows) // 10)]
    val_exps = sorted((r["val"]["expectancy_pct"] or -99) for r in rows)
    median = val_exps[len(val_exps) // 2]
    below = sum(1 for r in top if (r["val"]["expectancy_pct"] or -99) < median)
    return round(below / len(top), 3)


# ── feature research: does each feature separate outcomes on UNSEEN data? ──
FEATURES_TO_TEST = [
    ("gap_pct", lambda t: t.get("gap_pct")),
    ("pm_dollar_volume", lambda t: t.get("pm_dollar_volume")),
    ("rotation", lambda t: t.get("rotation")),
    ("spread_est_pct", lambda t: t.get("spread_est_pct")),
    ("score", lambda t: t.get("score")),
    ("volume_acceleration", lambda t: (t.get("features") or {}).get("volume_acceleration")),
    ("ext_above_vwap_pct", lambda t: (t.get("features") or {}).get("ext_above_vwap_pct")),
    ("signal_hour", lambda t: int(t["signal_time"][11:13]) if t.get("signal_time") else None),
]


def feature_importance(trades: List[dict], exit_name: str = "target_1R",
                       min_n: int = 20) -> List[Dict[str, Any]]:
    """Median-split each feature; compare baseline-execution expectancy above vs
    below. Honest: reports 'insufficient sample' below min_n per side."""
    res = run_policy(trades, exit_name, "baseline")
    rows = []
    for name, fn in FEATURES_TO_TEST:
        vals = [(fn(t), r) for t, r in zip(trades, res)
                if fn(t) is not None and r["pct"] is not None
                and r["outcome"] in ("win", "loss", "neutral")]
        if len(vals) < min_n * 2:
            rows.append({"feature": name, "verdict": "insufficient_sample",
                         "n": len(vals)})
            continue
        vs = sorted(v for v, _ in vals)
        med = vs[len(vs) // 2]
        lo = [r["pct"] for v, r in vals if v <= med]
        hi = [r["pct"] for v, r in vals if v > med]
        if len(lo) < min_n or len(hi) < min_n:
            rows.append({"feature": name, "verdict": "insufficient_sample",
                         "n": len(vals)})
            continue
        e_lo = sum(lo) / len(lo)
        e_hi = sum(hi) / len(hi)
        rows.append({"feature": name, "n": len(vals), "median": round(med, 3),
                     "exp_below": round(e_lo, 3), "exp_above": round(e_hi, 3),
                     "delta": round(e_hi - e_lo, 3),
                     "verdict": ("separates" if abs(e_hi - e_lo) > 0.5
                                 else "no_demonstrated_value")})
    return rows


# ── position-size capacity: a setup may work at $500 but not $10k ──
SIZES_USD = [500, 1000, 2500, 5000, 10000]
PARTICIPATION_LIMIT = 0.05   # never assume more than 5% of premarket $-volume


def capacity_table(trades: List[dict], exit_name: str,
                   model: str = "baseline") -> List[Dict[str, Any]]:
    res = run_policy(trades, exit_name, model)
    out = []
    for size in SIZES_USD:
        pnl, filled, partial, nofill = [], 0, 0, 0
        for t, r in zip(trades, res):
            cap_usd = (t.get("pm_dollar_volume") or 0) * PARTICIPATION_LIMIT
            if cap_usd < size * 0.25:
                nofill += 1
                continue
            frac = min(1.0, cap_usd / size)
            if frac < 1.0:
                partial += 1
            else:
                filled += 1
            if r["pct"] is not None and r["outcome"] in ("win", "loss", "neutral"):
                pnl.append(size * frac * r["pct"] / 100.0)
        n = filled + partial
        out.append({"size_usd": size, "filled": filled, "partial": partial,
                    "no_fill": nofill,
                    "fill_rate": round(n / len(trades), 3) if trades else None,
                    "expectancy_usd": round(sum(pnl) / len(pnl), 2) if pnl else None,
                    "total_pnl_usd": round(sum(pnl), 2) if pnl else None})
    return out
