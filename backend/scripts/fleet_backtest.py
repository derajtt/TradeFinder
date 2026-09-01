"""Run the fleet backtest over the shared liquid universe + crypto and import
the results to the dashboard."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from app.bt.data import BtData
from app.bt.fleet import run_fleet_backtest
from app.db import SessionLocal
from app.strategy.registry import CRYPTO_UNIVERSE, ETF_UNIVERSE

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")

async def main():
    data = BtData(SessionLocal, rps=3.0)
    series = {}
    for s in ETF_UNIVERSE + CRYPTO_UNIVERSE:
        rows = await data.eod(s)
        if len(rows) > 150:
            series[s] = rows
    print(f"series loaded: {len(series)} symbols "
          f"(api={data.api_calls} cache={data.cache_hits})")
    # historical earnings surprises by date (entitled calendar, monthly chunks)
    earnings_by_date = {}
    from datetime import date, timedelta
    d0 = date(2025, 3, 1)
    while d0 < date(2026, 9, 1):
        d1 = min(d0 + timedelta(days=30), date(2026, 9, 1))
        key = f"earncal:{d0}:{d1}"
        payload = await data._cached(key, lambda a=str(d0), b=str(d1):
                                     data._fmp("earnings-calendar",
                                               {"from": a, "to": b}))
        for r in (payload if isinstance(payload, list) else []):
            a_, e_ = r.get("epsActual"), r.get("epsEstimated")
            if a_ is None or e_ in (None, 0):
                continue
            try:
                surp = (float(a_) - float(e_)) / abs(float(e_)) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            sym = str(r.get("symbol", "")).upper()
            if sym in series:
                earnings_by_date.setdefault(r.get("date"), {})[sym] = {
                    "surprise_pct": surp, "date": r.get("date"),
                    "quality": "ESTIMATED_CURRENT_CONSENSUS"}
        d0 = d1
    print(f"earnings map: {sum(len(v) for v in earnings_by_date.values())} events")
    res = run_fleet_backtest(series, earnings_by_date)
    json.dump(res, open(f"{OUT}/fleet_report.json", "w"), default=str, indent=1)
    print(f"fleet trades: {res['trades_total']} over {res['sessions']} sessions")
    for mid, m in sorted(res["by_model"].items()):
        print(f"  {mid:22} n={m['n']:>4} WR={m['win_rate']} (LB {m['win_rate_lb']}) "
              f"exp={m['expectancy_pct']}% PF={m['profit_factor']} "
              f"DD={m['max_drawdown_pct']}% amb={m['ambiguous']} "
              f"| h1={m['first_half_exp']} h2={m['second_half_exp']}")
    key = open(sys.argv[1]).read().strip() if len(sys.argv) > 1 else None
    if key:
        h = httpx.Client(timeout=60, headers={"X-API-Key": key})
        r = h.post(f"{sys.argv[2]}/api/backtest/import",
                   json={"kind": "fleet", "config": {"universe_n": len(series)},
                         "config_hash": "fleet-daily-v1", "result": res})
        print("import:", r.status_code)
    await data.close()

asyncio.run(main())
