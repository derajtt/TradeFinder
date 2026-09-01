import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.bt.data import BtData, UniverseEod, trading_sessions
from app.bt.replay import SessionReplay
from app.bt.tournament import bootstrap_ci, metrics, run_policy, tournament
from app.db import SessionLocal

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")
HOLDOUT = trading_sessions("2026-07-01", "2026-08-31")
POOL = {"slippage_pct": 0.45, "min_pm_dollar_volume": 50_000,
        "max_ext_above_vwap_pct": 30.0, "rotation_hard_cap": 2.0}

async def main():
    data = BtData(SessionLocal, rps=2.5)
    uni = UniverseEod(data)
    syms = await data.universe_symbols()
    await uni.load(syms)
    rp = SessionReplay(data, ai_client=None, universe=uni)  # cached AI reused
    trades, summaries = [], []
    t0 = time.time()
    for i, d in enumerate(HOLDOUT):
        try:
            res = await rp.run_session(d, POOL)
            trades.extend(res["signals"])
            summaries.append({"date": d, "status": res["status"],
                              "signals": len(res["signals"])})
        except Exception as e:
            summaries.append({"date": d, "status": f"error:{type(e).__name__}"})
        if (i + 1) % 10 == 0:
            print(f"[holdout] {i+1}/{len(HOLDOUT)} | trades={len(trades)} "
                  f"api={data.api_calls} | {time.time()-t0:.0f}s", flush=True)
            json.dump({"trades": trades, "sessions": summaries},
                      open(f"{OUT}/dataset_holdout.json", "w"), default=str)
    json.dump({"trades": trades, "sessions": summaries},
              open(f"{OUT}/dataset_holdout.json", "w"), default=str)
    result = {"n_sessions": len(HOLDOUT), "pool_trades": len(trades),
              "tournament_top": {}, "note": "frozen baseline gates; ONE look"}
    if trades:
        t = tournament(trades, models=("baseline", "pessimistic"))
        rows = [(k, v["baseline"]) for k, v in t.items()
                if "baseline" in v and v["baseline"]["n"]]
        rows.sort(key=lambda r: (r[1]["win_rate_lb"] or 0), reverse=True)
        result["tournament_top"] = {k: m for k, m in rows[:8]}
        result["exit_935"] = metrics(run_policy(trades, "exit_935", "baseline"))
        result["ci"] = bootstrap_ci(run_policy(trades, "exit_935", "baseline"))
    json.dump(result, open(f"{OUT}/holdout_report.json", "w"), default=str, indent=1)
    print("HOLDOUT DONE", json.dumps({k: result[k] for k in
          ("n_sessions", "pool_trades")}))
    await data.close()

asyncio.run(main())
