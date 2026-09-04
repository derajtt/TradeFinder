#!/usr/bin/env python
"""Parameter sweep for every testable intraday engine, on the symbols actually traded.

Method (what keeps this from being curve-fitting):
  * Replay is bar-by-bar with no lookahead: each engine sees daily bars up to the
    PRIOR session and today's 5-minute bars up to the current one.
  * The universe is the ~190 movers the fleet actually traded Sep 1-3, not the
    calm core.
  * Ranking is expectancy (avg R) with a sample floor of n>=20. A default is
    overridden only if a setting beats it by >=0.05R at n>=20. Win rate is
    reported, never optimised.
  * Geometry (stop = max(mult*ATR5, floor%), target1 = t1 R) is chosen once,
    pooled across engines (n>=100); a per-engine override needs >=0.10R at
    n>=25 on top of that.
"""
import asyncio, itertools, json, os, sys, time, pathlib, statistics
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.bt import rev_data
from app.strategy import engines as E
from app.strategy.indicators import atr
from app.util.timeutil import ET

OUT = pathlib.Path(__file__).resolve().parents[2] / "data" / "sweep_out"; OUT.mkdir(parents=True, exist_ok=True)
SESSIONS = ["2026-09-01", "2026-09-02", "2026-09-03"]
SYMS = os.environ.get("SWEEP_SYMS", "").split() or """AAL ABTC ADCT ADIL AEHL AGX AI ALEC AMBR ANAB ANTX ASM AUR AVGO AZ BAK BBD BIAF BITO BMEA BMNR BRID BTGO BUUU CABO CABR CBIO CDE CETY CHPT CNH COIA CONL CPB CRCT CRDO CSHR CURV CYAB CYCN DAKT DELL DFDV DGNX DH DLTH DOO DPRO DSS DTST EBON ECRAF EHLD EMBC EMYB EOSE ETHA EWZ EXOD FAMI FCUV FISN FRMM FTH FWDI GALT GCO GFUZ GHI GORO GPRO GRSLF GTLB GYGY HCM HOOD HPE HSAI HWKN IBIT IFRX INMB INTC INTR IREN ISNPY JFB JLHL KORU LHAI LIDR LZ MARA MDIA MGNX MLYS MNTK MSTR MTDR MTEX MUU NAKA NCPL NDRA NIO NOK NTHI NTRB NU NVA NVD NXTC OABI OLLI ONDS OWLS PATH PCG PFE PLTA PLTR PLTU PLUG PPBT PRHI PRTS PSQH PTLE PTON PURR PXS PYXS QMLS QNRX RAIL RARE RDI REX RGC RGS RIG RR RSKD RZLV SBS SCOR SCTX SCWO SHMDW SID SION SLBT SLDP SMMT SNAP SOFI SOXL SOXS SPCX SPRY SPWH SQQQ SSL SST STUB SVCO SVRE SWVL TITN TKNO TLYS TMS TQQQ TRVG TSLL TTGPF TVA UAMY ULCC USEA UUU VALE VIOT WOOF XGN XTLB XXI YEXT YQ ZOOZ ZURA""".split()

ENGINES = {
    "technical_confluence": ("confluence", {"min_clusters": [2, 3, 4], "min_score": [45, 55, 65]}),
    "mean_reversion":       ("meanrev",    {"entry_z": [1.6, 2.2, 2.8]}),
    "opening_range_breakout": ("orb",      {"range_min": [10, 15, 30], "max_range_pct": [1.5, 2.5, 4.0]}),
    "breakout_finder":      ("breakout",   {"zone_tol": [0.6, 1.0], "min_touches": [2, 3], "max_ext_pct": [1.0, 2.0, 3.5], "min_rvol": [1.2, 1.5, 2.0]}),
    "gaussian_channel":     ("gaussian",   {"period": [14, 20, 30], "mult": [1.0, 1.4, 1.8], "max_ext_pct": [1.0, 2.0, 3.5]}),
    "exp_liquidity_vacuum": ("vacuum",     {}),
    "exp_open_drive":       ("opendrive",  {}),
    "exp_rs_reclaim":       ("rsreclaim",  {}),
    "trend_following":      ("trend",      {"channel": [10, 20, 40]}),
    "chart_patterns":       ("chartpat",   {"max_ext_pct": [1.5, 3.0, 5.0]}),
}
GEOM = {"mult": [1.5, 2.0, 2.5, 3.0], "floor": [0.02, 0.03, 0.04], "t1": [1.5, 2.0, 3.0]}
CUTOFF_MIN = 11 * 60 + 30
FLATTEN_MIN = 15 * 60 + 55
MIN_N, BEAT_DEFAULT, BEAT_POOLED = 20, 0.05, 0.10
SLIP = 0.004


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def to_engine_bar(b):
    ts = datetime.fromtimestamp(b["time"], tz=ET)
    return {"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"],
            "minute_of_day": ts.hour * 60 + ts.minute, "ts": ts, "date": ts.strftime("%Y-%m-%d")}


def simulate(bars, i, geom, mult_override=None):
    """Enter at next bar open after signal bar i; day geometry; walk to flatten."""
    if i + 1 >= len(bars): return None
    fill = bars[i + 1]["o"] * (1 + SLIP)
    a5 = atr(bars[max(0, i - 40):i + 1], 14) or fill * 0.01
    mult = mult_override if mult_override is not None else geom["mult"]
    r = min(max(mult * a5, fill * geom["floor"]), fill * 0.06)
    stop, t1, t2 = fill - r, fill + geom["t1"] * r, fill + 2 * geom["t1"] * r
    took1 = False; realized = 0.0; frac = 1.0
    for j in range(i + 1, len(bars)):
        b = bars[j]; hi, lo = b["h"], b["l"]
        if lo <= stop and hi >= t1 and not took1:      # ambiguous -> stop
            return realized + (stop * (1 - SLIP) - fill) / r * frac
        if lo <= stop:
            return realized + (stop * (1 - SLIP) - fill) / r * frac
        if not took1 and hi >= t1:
            realized += (t1 - fill) / r * 0.5; frac = 0.5; took1 = True; stop = fill
        if took1 and hi >= t2:
            return realized + (t2 - fill) / r * frac
        if b["minute_of_day"] >= FLATTEN_MIN:
            return realized + (b["c"] * (1 - SLIP) - fill) / r * frac
    return realized + (bars[-1]["c"] * (1 - SLIP) - fill) / r * frac


def wilson_lb(w, n):
    if not n: return 0.0
    p = w / n; z = 1.96
    return max(0.0, ((p + z*z/(2*n)) - z*((p*(1-p)/n + z*z/(4*n*n)) ** 0.5)) / (1 + z*z/n))


async def main():
    t0 = time.time()
    d = rev_data.RevData(rps=3.5)
    # ── data
    data = {}; spy_daily = await d.bars("SPY", "1day", "2026-05-01", SESSIONS[-1])
    for k, sym in enumerate(SYMS, 1):
        try:
            daily = await d.bars(sym, "1day", "2026-05-01", SESSIONS[-1])
            m5 = await d.bars(sym, "5min", SESSIONS[0], SESSIONS[-1], extended=True)
        except Exception as e:
            log(f"  !! {sym}: {e}"); continue
        if len(daily) >= 30 and m5:
            data[sym] = (daily, m5)
        if k % 40 == 0: log(f"  fetched {k}/{len(SYMS)} (api={d.api_calls} cache={d.cache_hits})")
    await d.close()
    log(f"data: {len(data)} symbols with bars ({time.time()-t0:.0f}s)")

    # ── entries per engine x param-config (independent of geometry)
    entries = {}   # (mid, cfg_key) -> list of (sym, day, signal_idx, bars_today)
    for mid, (fn_name, grid) in ENGINES.items():
        fn = E.ENGINES[fn_name]
        keys = list(grid); combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())] or [{}]
        for cfg in combos:
            ck = json.dumps(cfg, sort_keys=True); entries[(mid, ck)] = []
        for sym, (daily, m5all) in data.items():
            eb = [to_engine_bar(b) for b in m5all]
            for day in SESSIONS:
                today = [b for b in eb if b["date"] == day]
                if len(today) < 20: continue
                dprior = [{"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "date": b["date"]}
                          for b in daily if b["date"] < day]
                spy_prior = [{"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "date": b["date"]}
                             for b in spy_daily if b["date"] < day]
                if len(dprior) < 30: continue
                for cfg in combos:
                    ck = json.dumps(cfg, sort_keys=True); fired = False
                    for i in range(len(today)):
                        mod = today[i]["minute_of_day"]
                        if mod < 570 or mod > CUTOFF_MIN: continue
                        ctx = {"bars_daily": {sym: dprior}, "bars_5m": {sym: today[:i + 1]}, "spy_daily": spy_prior}
                        try: v = fn(ctx, sym, cfg)
                        except Exception: v = None
                        if v and v.get("action") == "buy":
                            entries[(mid, ck)].append((sym, day, i, today)); fired = True; break
                    # one entry per symbol-day per config (matches live idempotency)
        log(f"  entries: {mid} -> " + ", ".join(f"{len(entries[(mid, json.dumps(c, sort_keys=True))])}" for c in combos) + f"  ({time.time()-t0:.0f}s)")

    # ── geometry: pooled choice at default params, then per-engine sweep
    def score(rows):
        n = len(rows)
        if not n: return {"n": 0}
        w = sum(1 for r in rows if r > 0.05)
        return {"n": n, "exp_r": round(statistics.mean(rows), 3), "win_pct": round(100 * w / n, 1),
                "wilson_lb": round(100 * wilson_lb(w, n), 1), "total_r": round(sum(rows), 1)}

    geoms = [dict(zip(GEOM, v)) for v in itertools.product(*GEOM.values())]
    default_ck = {mid: json.dumps({}, sort_keys=True) if not grid else
                  json.dumps({k: (lambda vals, mid=mid, k=k: vals[[1, 1, 1, 1][0]] if False else None)(v) for k, v in grid.items()}, sort_keys=True)
                  for mid, (fn, grid) in ENGINES.items()}
    # default config = the engine's own cfg.get defaults, i.e. the empty dict {} — every grid includes it implicitly
    for (mid, ck), lst in list(entries.items()):
        pass
    # ensure the empty (default) config is evaluated for every engine
    for mid, (fn_name, grid) in ENGINES.items():
        ck = json.dumps({}, sort_keys=True)
        if (mid, ck) not in entries:
            fn = E.ENGINES[fn_name]; entries[(mid, ck)] = []
            for sym, (daily, m5all) in data.items():
                eb = [to_engine_bar(b) for b in m5all]
                for day in SESSIONS:
                    today = [b for b in eb if b["date"] == day]
                    if len(today) < 20: continue
                    dprior = [{"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "date": b["date"]} for b in daily if b["date"] < day]
                    spy_prior = [{"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"], "date": b["date"]} for b in spy_daily if b["date"] < day]
                    if len(dprior) < 30: continue
                    for i in range(len(today)):
                        mod = today[i]["minute_of_day"]
                        if mod < 570 or mod > CUTOFF_MIN: continue
                        ctx = {"bars_daily": {sym: dprior}, "bars_5m": {sym: today[:i + 1]}, "spy_daily": spy_prior}
                        try: v = fn(ctx, sym, cfg := {})
                        except Exception: v = None
                        if v and v.get("action") == "buy":
                            entries[(mid, ck)].append((sym, day, i, today)); break

    pooled = {}
    for g in geoms:
        rows = []
        for mid in ENGINES:
            for (sym, day, i, today) in entries[(mid, json.dumps({}, sort_keys=True))]:
                r = simulate(today, i, g)
                if r is not None: rows.append(r)
        pooled[json.dumps(g)] = score(rows)
    best_geom = max((g for g in geoms if pooled[json.dumps(g)].get("n", 0) >= 100),
                    key=lambda g: pooled[json.dumps(g)]["exp_r"], default={"mult": 2.0, "floor": 0.03, "t1": 1.5})
    log(f"pooled geometry choice: {best_geom} -> {pooled[json.dumps(best_geom)]}")

    report = {"sessions": SESSIONS, "symbols": len(data), "geometry_pooled": {"chosen": best_geom, "table": pooled},
              "engines": {}, "method": __doc__.strip()}
    for mid, (fn_name, grid) in ENGINES.items():
        table = []
        for (m, ck), lst in entries.items():
            if m != mid: continue
            rows = [r for r in (simulate(today, i, best_geom) for (_, _, i, today) in lst) if r is not None]
            table.append({"params": json.loads(ck), **score(rows)})
        table.sort(key=lambda t: (t.get("n", 0) >= MIN_N, t.get("exp_r", -9)), reverse=True)
        default = next(t for t in table if t["params"] == {})
        best = next((t for t in table if t.get("n", 0) >= MIN_N), None)
        chosen = default
        if best and best["params"] != {} and default.get("n", 0) and best["exp_r"] >= default.get("exp_r", -9) + BEAT_DEFAULT:
            chosen = best
        # per-engine geometry override
        geo_override = None
        base_rows = [r for r in (simulate(today, i, best_geom) for (_, _, i, today) in entries[(mid, json.dumps(chosen["params"], sort_keys=True))]) if r is not None]
        base = score(base_rows)
        if base.get("n", 0) >= 25:
            for g in geoms:
                rows = [r for r in (simulate(today, i, g) for (_, _, i, today) in entries[(mid, json.dumps(chosen["params"], sort_keys=True))]) if r is not None]
                sc = score(rows)
                if sc.get("n", 0) >= 25 and sc["exp_r"] >= base["exp_r"] + BEAT_POOLED and (geo_override is None or sc["exp_r"] > geo_override[1]["exp_r"]):
                    geo_override = (g, sc)
        report["engines"][mid] = {"default": default, "chosen": chosen, "overrode_default": chosen is not default,
                                  "geometry_override": ({"geom": geo_override[0], **geo_override[1]} if geo_override else None),
                                  "table": table[:12]}
        log(f"  {mid:24s} default n={default.get('n',0)} exp={default.get('exp_r')} | chosen {chosen['params']} n={chosen.get('n',0)} exp={chosen.get('exp_r')} win={chosen.get('win_pct')} | geo_override={geo_override[0] if geo_override else None}")
    json.dump(report, open(OUT / "engine_sweep.json", "w"), indent=1, default=str)
    log(f"wrote {OUT/'engine_sweep.json'} ({time.time()-t0:.0f}s)")

asyncio.run(main())
