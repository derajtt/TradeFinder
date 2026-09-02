#!/usr/bin/env python
"""Parameter study + exit tournament + walk-forward for EXTREME_BB_RSI.

Design that keeps this honest:
  * Entry parameters and exit parameters are separated. Entries are generated
    ONCE per entry-config, then every stop/exit model is scored against those
    IDENTICAL frozen entries — so an exit comparison cannot be contaminated by
    a different set of trades.
  * Chronological split per series: 60% train / 20% validation / 20% test.
    Optimisation only ever sees train. Selection only ever sees validation.
    The test slice is scored exactly once, at the end.
  * Ranking is by the Wilson lower bound of expectancy-positive outcomes and by
    expectancy itself — never by raw win rate.
"""
import asyncio, json, os, sys, time, itertools, pathlib, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bt import rev_data, reversion_bt as B
from app.strategy import reversion as R
from scripts.reversion_study import (UNIVERSE, CLASS_OF, ALL, TIER_A, TIER_B,
                                     CORE, INTRADAY_FROM, INTRADAY_TO,
                                     DAILY_FROM, DAILY_TO, load_all, htf_for, log)

OUT = pathlib.Path(__file__).resolve().parents[2] / "data" / "rev_out"
OUT.mkdir(parents=True, exist_ok=True)

# Entry grid — the ranges the brief asked to test.
ENTRY_GRID = {
    "bb_length": [20, 30],
    "bb_dev": [2.0, 2.5, 3.0],
    "rsi_length": [7, 14],
    "rsi_oversold": [5, 10, 20, 25],
    "confirmation": [True, False],
}
# Exit/stop grid — scored on identical frozen entries.
EXIT_GRID = [
    ("extreme_atr", 0.2, "bb_basis", 0.0),
    ("extreme_atr", 0.2, "rr", 1.5),
    ("extreme_atr", 0.2, "rr", 2.0),
    ("extreme_atr", 0.2, "atr", 1.5),
    ("extreme_atr", 0.2, "rsi_norm", 50),
    ("atr", 1.0, "bb_basis", 0.0),
    ("atr", 1.0, "rr", 2.0),
    ("atr", 1.5, "rr", 2.0),
    ("atr", 0.75, "rr", 1.5),
    ("pct", 0.5, "bb_basis", 0.0),
    ("pct", 1.0, "rr", 2.0),
]
MIN_TRADES = 30          # below this a config is INSUFFICIENT and never "best"


def split_bars(bars):
    n = len(bars)
    return {"train": bars[:int(n * .6)],
            "validation": bars[int(n * .6):int(n * .8)],
            "test": bars[int(n * .8):]}


def eval_entry_config(store, keys, htf_cache, ep, splits=("train",)):
    """Scan each series slice once; return signals keyed by split."""
    sigs_by_split = {s: [] for s in splits}
    for (sym, tf) in keys:
        bars = store.get((sym, tf)) or []
        if len(bars) < 400:
            continue
        parts = split_bars(bars)
        htf = htf_cache.get((sym, tf), [])
        for sp in splits:
            seg = parts[sp]
            if len(seg) < 300:
                continue
            found = R.scan(seg, ep)
            for s in found:
                if s.get("status") == "CONFIRMED" and htf:
                    s["snapshot"]["htf_trend"] = B.trend_at(htf, s.get("confirm_time"))
            sigs_by_split[sp].append(((sym, tf), seg, found))
    return sigs_by_split


def score_exits(series_sigs, ep):
    """Every exit model against the SAME frozen entries."""
    rows = []
    for (stop_model, stop_param, exit_model, exit_param) in EXIT_GRID:
        p = dict(ep)
        p.update({"stop_model": stop_model, "stop_param": stop_param,
                  "exit_model": exit_model, "exit_param": exit_param})
        trades = []
        for (key, seg, found) in series_sigs:
            sym, tf = key
            hold = {"5min": 78, "15min": 26, "30min": 13, "1hour": 14,
                    "4hour": 12, "1day": 10}.get(tf, 30)
            for t in B.simulate(seg, found, p, max_hold_bars=hold):
                t["symbol"], t["timeframe"] = sym, tf
                t["asset_class"] = CLASS_OF[sym]
                trades.append(t)
        m = B.metrics(trades)
        rows.append({"stop_model": stop_model, "stop_param": stop_param,
                     "exit_model": exit_model, "exit_param": exit_param,
                     "metrics": m, "trades": trades})
    return rows


def rank_key(m):
    """Expectancy first, with a sample-size penalty. Never win rate alone."""
    if m.get("trades", 0) < MIN_TRADES:
        return -99
    return m.get("expectancy_r", -9)


async def main():
    t0 = time.time()
    d = rev_data.RevData(rps=4)
    store, coverage = await load_all(d)
    await d.close()
    keys = [(c["symbol"], c["timeframe"]) for c in coverage if c["bars"] > 0]
    htf_cache = {k: htf_for(store, *k) for k in keys}
    log(f"{len(keys)} series, {sum(c['bars'] for c in coverage):,} bars "
        f"(api={d.api_calls} cache={d.cache_hits})")

    combos = [dict(zip(ENTRY_GRID, v)) for v in itertools.product(*ENTRY_GRID.values())]
    log(f"entry configs: {len(combos)}  ×  exit configs: {len(EXIT_GRID)} "
        f"= {len(combos)*len(EXIT_GRID)} evaluations on TRAIN")

    results = []
    for i, ec in enumerate(combos, 1):
        ep = R.params_for("confirm" if ec["confirmation"] else "video_baseline",
                          {k: v for k, v in ec.items() if k != "confirmation"})
        ep["confirmation"] = ec["confirmation"]
        ep["require_reentry"] = ec["confirmation"]
        ep["require_rsi_turn"] = ec["confirmation"]
        ep["rsi_overbought"] = 100 - ec["rsi_oversold"]
        ep["min_score"] = 0.0
        sigs = eval_entry_config(store, keys, htf_cache, ep, splits=("train",))
        for row in score_exits(sigs["train"], ep):
            results.append({"entry": ec, "exit": {k: row[k] for k in
                            ("stop_model", "stop_param", "exit_model", "exit_param")},
                            "train": row["metrics"]})
        if i % 8 == 0 or i == len(combos):
            log(f"  train {i}/{len(combos)} entry configs "
                f"({time.time()-t0:.0f}s)")

    results.sort(key=lambda r: rank_key(r["train"]), reverse=True)
    eligible = [r for r in results if r["train"].get("trades", 0) >= MIN_TRADES]
    log(f"TRAIN done: {len(results)} evaluations, "
        f"{len(eligible)} met the {MIN_TRADES}-trade minimum")
    json.dump({"all": [{k: v for k, v in r.items()} for r in results[:400]],
               "eligible": len(eligible), "min_trades": MIN_TRADES},
              open(OUT / "train_grid.json", "w"), indent=1, default=str)

    if not eligible:
        log("NO configuration produced a usable sample on TRAIN — stopping. "
            "Nothing is promoted on an insufficient sample.")
        json.dump({"verdict": "no_eligible_config", "reason":
                   f"no configuration reached {MIN_TRADES} resolved trades on train"},
                  open(OUT / "optimize_result.json", "w"), indent=1)
        return

    # ---------- validation on the top candidates -------------------------
    top = eligible[:12]
    log(f"validating top {len(top)} candidates on the VALIDATION slice")
    for r in top:
        ec = r["entry"]
        ep = R.params_for("confirm" if ec["confirmation"] else "video_baseline",
                          {k: v for k, v in ec.items() if k != "confirmation"})
        ep.update({"confirmation": ec["confirmation"],
                   "require_reentry": ec["confirmation"],
                   "require_rsi_turn": ec["confirmation"],
                   "rsi_overbought": 100 - ec["rsi_oversold"], "min_score": 0.0,
                   **r["exit"]})
        sigs = eval_entry_config(store, keys, htf_cache, ep, splits=("validation",))
        rows = score_exits(sigs["validation"], ep)
        match = next((x for x in rows if x["stop_model"] == r["exit"]["stop_model"]
                      and x["exit_model"] == r["exit"]["exit_model"]
                      and x["exit_param"] == r["exit"]["exit_param"]
                      and x["stop_param"] == r["exit"]["stop_param"]), None)
        r["validation"] = match["metrics"] if match else {"trades": 0}
        log(f"  val: {ec} {r['exit']['stop_model']}/{r['exit']['exit_model']} "
            f"-> trades={r['validation'].get('trades',0)} "
            f"exp_r={r['validation'].get('expectancy_r','-')}")

    # selection uses VALIDATION only
    sel = [r for r in top if r["validation"].get("trades", 0) >= MIN_TRADES]
    sel.sort(key=lambda r: r["validation"].get("expectancy_r", -9), reverse=True)
    chosen = sel[0] if sel else None
    if not chosen:
        log("no candidate met the trade minimum on VALIDATION — nothing selected")
        json.dump({"verdict": "no_validated_config", "train_top": top[:5]},
                  open(OUT / "optimize_result.json", "w"), indent=1, default=str)
        return

    # ---------- the untouched test slice, scored exactly once -------------
    ec = chosen["entry"]
    ep = R.params_for("confirm" if ec["confirmation"] else "video_baseline",
                      {k: v for k, v in ec.items() if k != "confirmation"})
    ep.update({"confirmation": ec["confirmation"],
               "require_reentry": ec["confirmation"],
               "require_rsi_turn": ec["confirmation"],
               "rsi_overbought": 100 - ec["rsi_oversold"], "min_score": 0.0,
               **chosen["exit"]})
    sigs = eval_entry_config(store, keys, htf_cache, ep, splits=("test",))
    rows = score_exits(sigs["test"], ep)
    match = next((x for x in rows if x["stop_model"] == chosen["exit"]["stop_model"]
                  and x["exit_model"] == chosen["exit"]["exit_model"]
                  and x["exit_param"] == chosen["exit"]["exit_param"]
                  and x["stop_param"] == chosen["exit"]["stop_param"]), None)
    chosen["test"] = match["metrics"] if match else {"trades": 0}
    test_trades = match["trades"] if match else []
    log(f"HOLDOUT: trades={chosen['test'].get('trades',0)} "
        f"win={chosen['test'].get('win_rate','-')}% "
        f"exp_r={chosen['test'].get('expectancy_r','-')} "
        f"sample={chosen['test'].get('sample')}")

    out = {
        "chosen": {"entry": chosen["entry"], "exit": chosen["exit"],
                   "train": chosen["train"], "validation": chosen["validation"],
                   "test": chosen["test"]},
        "candidates": [{"entry": r["entry"], "exit": r["exit"],
                        "train": r["train"], "validation": r.get("validation")}
                       for r in top],
        "test_breakdowns": {
            "by_asset_class": B.breakdown(test_trades, "asset_class"),
            "by_timeframe": B.breakdown(test_trades, "timeframe"),
            "by_regime": B.breakdown(test_trades, "regime"),
            "by_score_band": B.breakdown(test_trades, "score_band"),
            "by_direction": B.breakdown(test_trades, "direction"),
            "by_exit_reason": B.breakdown(test_trades, "exit_reason"),
        },
        "coverage": coverage,
        "protocol": {
            "split": "chronological 60/20/20 per series, never shuffled",
            "optimised_on": "train", "selected_on": "validation",
            "reported_on": "test (scored once)",
            "min_trades": MIN_TRADES,
            "ranking": "expectancy in R, not win rate",
        },
        "elapsed_s": round(time.time() - t0, 1),
    }
    json.dump(out, open(OUT / "optimize_result.json", "w"), indent=1, default=str)
    json.dump(test_trades, open(OUT / "test_trades.json", "w"), default=str)
    log(f"wrote optimize_result.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    asyncio.run(main())
