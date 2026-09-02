#!/usr/bin/env python
"""Extreme Reversion historical study.

Fetches real bars once (disk-cached), then evaluates every configuration
in memory. Chronological splits only. Reports coverage honestly — a symbol or
timeframe with no data is reported as no data, never filled in.
"""
import asyncio, json, os, sys, time, itertools, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bt import rev_data, reversion_bt as B
from app.strategy import reversion as R

OUT = pathlib.Path(__file__).resolve().parents[2] / "data" / "rev_out"
OUT.mkdir(parents=True, exist_ok=True)

UNIVERSE = {
    "large_cap": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
                  "JPM", "XOM", "UNH", "V", "WMT"],
    "mid_cap": ["ROKU", "DKNG", "PINS", "PLTR", "SOFI", "RIVN"],
    "small_cap_liquid": ["MARA", "RIOT", "PLUG", "LCID"],
    "etf": ["SPY", "QQQ", "IWM", "XLE", "XLF", "SMH"],
    "crypto": ["BTCUSD", "ETHUSD", "SOLUSD"],
}
CLASS_OF = {s: k for k, v in UNIVERSE.items() for s in v}
ALL = [s for v in UNIVERSE.values() for s in v]

TIER_A = ["1day", "4hour", "1hour"]          # cheap, long history, every symbol
TIER_B = ["30min", "15min", "5min"]          # expensive, core subset
CORE = ["AAPL", "NVDA", "TSLA", "SPY", "QQQ", "MARA", "BTCUSD", "ETHUSD"]

INTRADAY_FROM, INTRADAY_TO = "2024-01-01", "2025-08-29"
DAILY_FROM, DAILY_TO = "2022-01-01", "2025-08-29"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


async def load_all(d: rev_data.RevData):
    store, coverage = {}, []
    jobs = [(s, tf) for s in ALL for tf in TIER_A] + \
           [(s, tf) for s in CORE for tf in TIER_B]
    for n, (sym, tf) in enumerate(jobs, 1):
        frm, to = (DAILY_FROM, DAILY_TO) if tf == "1day" else (INTRADAY_FROM, INTRADAY_TO)
        try:
            bars = await d.bars(sym, tf, frm, to)
        except Exception as e:
            log(f"  !! {sym} {tf}: {type(e).__name__} {e}")
            bars = []
        store[(sym, tf)] = bars
        coverage.append({"symbol": sym, "timeframe": tf, "bars": len(bars),
                         "asset_class": CLASS_OF[sym],
                         "first": bars[0]["date"] if bars else None,
                         "last": bars[-1]["date"] if bars else None})
        if n % 15 == 0 or n == len(jobs):
            log(f"  loaded {n}/{len(jobs)} series (api={d.api_calls} cache={d.cache_hits})")
    return store, coverage


def htf_for(store, sym, tf):
    h = B.htf_of(tf)
    if not h:
        return []
    hb = store.get((sym, h)) or []
    return B.htf_trend_series(hb) if hb else []


def run_config(store, params, series_keys, htf_cache):
    """All trades for one configuration across the requested series."""
    trades = []
    for (sym, tf) in series_keys:
        bars = store.get((sym, tf)) or []
        if len(bars) < 250:
            continue
        htf = htf_cache.get((sym, tf), [])
        sigs = R.scan(bars, params)
        for s in sigs:
            if s.get("status") == "CONFIRMED" and htf:
                s["snapshot"]["htf_trend"] = B.trend_at(htf, s.get("confirm_time"))
        hold = {"5min": 78, "15min": 26, "30min": 13, "1hour": 14,
                "4hour": 12, "1day": 10}.get(tf, 30)
        for t in B.simulate(bars, sigs, params, max_hold_bars=hold):
            t["symbol"] = sym
            t["timeframe"] = tf
            t["asset_class"] = CLASS_OF[sym]
            trades.append(t)
    return trades


async def main():
    t0 = time.time()
    d = rev_data.RevData(rps=float(os.environ.get("REV_RPS", "4")))
    log(f"loading bars: {len(ALL)} symbols × {TIER_A} + {len(CORE)} × {TIER_B}")
    store, coverage = await load_all(d)
    await d.close()
    got = [c for c in coverage if c["bars"] > 0]
    log(f"coverage: {len(got)}/{len(coverage)} series have data; "
        f"{sum(c['bars'] for c in coverage):,} bars total "
        f"(api={d.api_calls} cache_hits={d.cache_hits})")
    json.dump(coverage, open(OUT / "coverage.json", "w"), indent=1)

    keys = [(c["symbol"], c["timeframe"]) for c in got]
    htf_cache = {k: htf_for(store, *k) for k in keys}

    # ---------- 1. the three named variants, default parameters -----------
    log("running the three named variants at default parameters")
    results = {}
    for variant in ("video_baseline", "confirm", "adaptive"):
        p = R.params_for(variant)
        tr = run_config(store, p, keys, htf_cache)
        results[variant] = {"params": {k: v for k, v in p.items()},
                            "overall": B.metrics(tr),
                            "by_asset_class": B.breakdown(tr, "asset_class"),
                            "by_timeframe": B.breakdown(tr, "timeframe"),
                            "by_symbol": B.breakdown(tr, "symbol"),
                            "by_regime": B.breakdown(tr, "regime"),
                            "by_score_band": B.breakdown(tr, "score_band"),
                            "by_direction": B.breakdown(tr, "direction"),
                            "by_exit_reason": B.breakdown(tr, "exit_reason"),
                            "trades": tr}
        m = results[variant]["overall"]
        log(f"  {variant:15s} trades={m.get('trades',0):4d} "
            f"win={m.get('win_rate','-')}% exp_r={m.get('expectancy_r','-')} "
            f"pf={m.get('profit_factor','-')} sample={m.get('sample')}")

    json.dump({"coverage": coverage,
               "variants": {k: {kk: vv for kk, vv in v.items() if kk != "trades"}
                            for k, v in results.items()},
               "elapsed_s": round(time.time() - t0, 1)},
              open(OUT / "variants.json", "w"), indent=1, default=str)
    for k, v in results.items():
        json.dump(v["trades"], open(OUT / f"trades_{k}.json", "w"), default=str)
    log(f"wrote {OUT}/variants.json  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main())
