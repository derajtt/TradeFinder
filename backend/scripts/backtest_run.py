"""Autonomous backtest + optimization loop. Resumable via cached data; artifacts
land in backend/data/bt/. Run:  ../.venv/bin/python scripts/backtest_run.py [--quick]
"""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from app.bt.data import BtData, trading_sessions
from app.bt.replay import SessionReplay
from app.bt.optimize import (ACCURACY_FLOOR, CONVERGENCE, Registry, config_hash,
                             entry_filter, eval_config, passes_floor, pbo_estimate,
                             staged_search, walk_forward)
from app.bt.tournament import (bootstrap_ci, metrics, mfe_decay, run_policy,
                               segment, tournament)
from app.config import get_config
from app.db import SessionLocal
from app.strategy.catalyst import ANALYSIS_SCHEMA_V2, SYSTEM_PROMPT_V2
from app.strategy.versions import VERSIONS

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")
os.makedirs(OUT, exist_ok=True)
QUICK = "--quick" in sys.argv

# predefined chronological splits (documented before any search)
DEV = trading_sessions("2026-03-02", "2026-05-15")
VAL = trading_sessions("2026-05-18", "2026-06-30")
HOLDOUT = trading_sessions("2026-07-01", "2026-08-31")
if QUICK:
    DEV, VAL, HOLDOUT = DEV[:6], VAL[:3], HOLDOUT[:3]

POOL_SETTINGS = {  # widest defensible corner; optimizer narrows via entry_filter
    "slippage_pct": 0.45, "min_pm_dollar_volume": 50_000,
    "max_ext_above_vwap_pct": 30.0, "rotation_hard_cap": 2.0,
}
AI_BUDGET_USD = 10.0


class AI:
    def __init__(self):
        cfg = get_config()
        self.key = cfg.openai_api_key
        self.client = httpx.AsyncClient(timeout=45)
        self.spend = 0.0
        self.calls = 0

    async def __call__(self, evidence: str):
        if not self.key or self.spend >= AI_BUDGET_USD:
            return None
        try:
            r = await self.client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": "gpt-4o-mini", "temperature": 0,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT_V2},
                                   {"role": "user", "content": evidence[:8000]}],
                      "response_format": {"type": "json_schema", "json_schema": {
                          "name": "catalyst_extract_v2", "strict": True,
                          "schema": ANALYSIS_SCHEMA_V2}}})
            if r.status_code != 200:
                return None
            pl = r.json()
            u = pl.get("usage", {})
            self.spend += (u.get("prompt_tokens", 0) * .15 +
                           u.get("completion_tokens", 0) * .6) / 1e6
            self.calls += 1
            return json.loads(pl["choices"][0]["message"]["content"])
        except Exception:
            return None


async def build_dataset(days, tag, data, rp):
    trades, summaries, incomplete = [], [], 0
    t0 = time.time()
    for i, d in enumerate(days):
        try:
            res = await rp.run_session(d, POOL_SETTINGS)
        except Exception as e:
            summaries.append({"date": d, "status": f"error:{type(e).__name__}"})
            incomplete += 1
            continue
        if res["status"] != "done":
            incomplete += 1
        trades.extend(res["signals"])
        summaries.append({"date": d, "status": res["status"],
                          "cands": len(res["candidates"]),
                          "signals": len(res["signals"]),
                          "early": len(res["early"]), "rejects": len(res["rejects"])})
        if (i + 1) % 10 == 0 or i == len(days) - 1:
            el = time.time() - t0
            print(f"[{tag}] {i+1}/{len(days)} sessions | trades={len(trades)} "
                  f"api={data.api_calls} cache={data.cache_hits} "
                  f"ai=${rp_ai.spend:.2f} | {el:.0f}s", flush=True)
            json.dump({"trades": trades, "sessions": summaries},
                      open(f"{OUT}/dataset_{tag}.json", "w"), default=str)
    json.dump({"trades": trades, "sessions": summaries},
              open(f"{OUT}/dataset_{tag}.json", "w"), default=str)
    return trades, summaries, incomplete


async def main():
    global rp_ai
    data = BtData(SessionLocal, rps=3.0)
    rp_ai = AI()
    rp = SessionReplay(data, ai_client=rp_ai)
    print(f"splits: DEV={len(DEV)} VAL={len(VAL)} HOLDOUT={len(HOLDOUT)} sessions")

    dev_trades, dev_sum, dev_inc = await build_dataset(DEV, "dev", data, rp)
    val_trades, val_sum, val_inc = await build_dataset(VAL, "val", data, rp)
    print(f"pool: dev={len(dev_trades)} val={len(val_trades)} trades "
          f"(sessions incomplete: dev={dev_inc} val={val_inc})")

    reg = Registry()
    dev_weeks, val_weeks = len(DEV) / 5.0, len(VAL) / 5.0
    r1 = staged_search(dev_trades, val_trades, dev_weeks, val_weeks, reg)
    rounds, converged_because = 1, ""
    best = r1
    if r1["locked"] is None:
        converged_because = "no config passed the accuracy floor on stage-1 screen"
    else:
        # round 2: neighborhood refinement around the locked entry (midpoints)
        e = dict(r1["locked"]["entry"])
        neigh = []
        for k, v in e.items():
            if isinstance(v, (int, float)):
                neigh += [dict(e, **{k: type(v)(v * 0.9)}),
                          dict(e, **{k: type(v)(v * 1.1)})]
        improved = False
        base_exp = r1["val_metrics"]["expectancy_pct"] or 0
        for e2 in neigh:
            dm, vm, pm = eval_config(dev_trades, val_trades, e2,
                                     r1["locked"]["exit"], dev_weeks, val_weeks)
            reg.log("refine", {"entry": e2, "exit": r1["locked"]["exit"]}, dm, vm)
            ok, _ = passes_floor(vm, pm, val_weeks)
            if ok and (vm["win_rate_lb"] or 0) > (best["val_metrics"]["win_rate_lb"] or 0) \
               and (vm["expectancy_pct"] or 0) >= base_exp - 0.05:
                best = dict(r1, locked={"entry": e2, "exit": r1["locked"]["exit"]},
                            val_metrics=vm, pess_metrics=pm)
                improved = (vm["expectancy_pct"] or 0) - base_exp >= \
                    CONVERGENCE["min_delta_expectancy_pct"]
        rounds = 2
        converged_because = ("round-2 refinement gain below min_delta "
                             if not improved else "budget/stability satisfied")
    pbo = pbo_estimate(reg)

    result = {"versions": VERSIONS, "splits": {"dev": [DEV[0], DEV[-1]],
              "val": [VAL[0], VAL[-1]], "holdout": [HOLDOUT[0], HOLDOUT[-1]]},
              "pool": {"dev_trades": len(dev_trades), "val_trades": len(val_trades)},
              "configs_tested": reg.tested(), "rounds": rounds,
              "converged_because": converged_because, "pbo": pbo,
              "floors": ACCURACY_FLOOR, "convergence": CONVERGENCE,
              "search": {k: v for k, v in best.items() if k != "jitter_detail"},
              "jitter_detail": best.get("jitter_detail"),
              "api_calls": data.api_calls, "cache_hits": data.cache_hits,
              "ai_calls": rp_ai.calls, "ai_spend_usd": round(rp_ai.spend, 2)}

    if best.get("locked"):
        lock = best["locked"]
        wf = walk_forward(dev_trades + val_trades, DEV + VAL, 5,
                          lock["entry"], lock["exit"])
        result["walk_forward"] = wf
        # exit tournament + MFE decay on locked-entry trades (dev+val)
        pool_locked = entry_filter(dev_trades + val_trades, lock["entry"])
        result["tournament"] = tournament(pool_locked)
        result["mfe_decay"] = mfe_decay(pool_locked)
        result["by_time"] = segment(run_policy(pool_locked, lock["exit"], "baseline"),
                                    lambda r: r["signal_time"][11:13])
        # ---- HOLDOUT: fetched and evaluated ONCE, after locking ----
        hold_trades, hold_sum, hold_inc = await build_dataset(HOLDOUT, "holdout", data, rp)
        ht = entry_filter(hold_trades, lock["entry"])
        hold_res = run_policy(ht, lock["exit"], "baseline") if ht else []
        hold_pess = run_policy(ht, lock["exit"], "pessimistic") if ht else []
        result["holdout"] = {"sessions": len(HOLDOUT), "incomplete": hold_inc,
                             "pool_trades": len(hold_trades),
                             "locked_trades": len(ht),
                             "baseline": metrics(hold_res),
                             "pessimistic": metrics(hold_pess),
                             "ci": bootstrap_ci(hold_res)}
        hb = result["holdout"]["baseline"]
        ok_hold = (hb["n"] or 0) >= 10 and (hb["expectancy_pct"] or -9) > -0.2
        result["primary"] = {
            "strategy_version": f"v2.1.0+{config_hash(lock)}",
            "entry": lock["entry"], "exit": lock["exit"],
            "mode": "primary_paper" if ok_hold else "experimental_paper",
            "holdout_pass": ok_hold,
        }
        json.dump(result["primary"], open(f"{OUT}/primary_policy.json", "w"), indent=1)
    json.dump(result, open(f"{OUT}/final_report.json", "w"), default=str, indent=1)
    print("DONE", json.dumps({k: result[k] for k in
          ("configs_tested", "rounds", "converged_because", "pbo",
           "api_calls", "ai_spend_usd")}, default=str))
    await data.close()

rp_ai = None
asyncio.run(main())
