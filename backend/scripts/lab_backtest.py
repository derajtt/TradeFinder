#!/usr/bin/env python
"""Quant Lab backtest runner.

Discovers every strategy module, sweeps its parameter grid on TRAIN, selects on
VALIDATION, reports OOS once, runs the Sep-2026 movers as a FORWARD split, then
writes data/lab_out/<strategy_id>.json + leaderboard.json and persists
LabStrategy / LabRun / LabTrade rows. A strategy that raises is logged and
skipped; the run never dies because one idea is broken.

  python scripts/lab_backtest.py --quick
  python scripts/lab_backtest.py --strategies s07_rsi2_trend_filter --timeframes 1hour,1day
  python scripts/lab_backtest.py --markets stocks,etf --workers 6 --no-db
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lab import backtest as B  # noqa: E402
from app.lab import registry  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT_DEFAULT = REPO / "data" / "lab_out"
SPY_RANGE = ("2023-06-01", "2026-09-03")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategies", default="", help="comma-separated strategy ids (default: all)")
    ap.add_argument("--timeframes", default=",".join(B.TIMEFRAMES))
    ap.add_argument("--markets", default=",".join(B.UNIVERSE))
    ap.add_argument("--symbols", default="", help="restrict the core universe to these symbols")
    ap.add_argument("--quick", action="store_true",
                    help="subset: default params + one-step neighbours, %s, 40 forward movers"
                         % ",".join(B.QUICK_SYMBOLS))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--lookback", type=int, default=800, help="bars handed to signal() per call")
    ap.add_argument("--exit-model", default="scale_out", choices=["scale_out", "t1_full"])
    ap.add_argument("--no-db", action="store_true", help="skip LabStrategy/LabRun/LabTrade persistence")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--list", action="store_true", help="list discovered strategies and exit")
    ap.add_argument("--rebuild-leaderboard", action="store_true",
                    help="rebuild leaderboard.json and the stored composites from the s*.json files "
                         "already in --out, without re-running any backtest")
    return ap.parse_args(argv)


def _csv(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _dt(epoch: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc) if epoch is not None else None


def serial_map(fn: Callable, jobs: Sequence[dict]) -> Iterable[dict]:
    return [fn(j) for j in jobs]


async def ensure_spy_daily() -> None:
    """SPY daily must span 2023-06-01..2026-09-03 for regimes. The only fetch
    this runner is allowed to make; everything else is cache-only."""
    rows = B.read_cache("SPY", "1day")
    if rows and rows[0]["date"] <= SPY_RANGE[0] and rows[-1]["date"] >= "2026-09-01":
        return
    log("SPY daily cache incomplete — fetching the allowed range once")
    from app.bt.rev_data import RevData
    d = RevData(rps=3.0)
    try:
        got = await d.bars("SPY", "1day", *SPY_RANGE)
        log(f"SPY daily: {len(got)} bars")
    finally:
        await d.close()
    B.spy_context.cache_clear()


# ----------------------------------------------------------- persistence ----

def _run_row(models, sid: str, c: Dict[str, Any], split: str, kind: str, metrics: Dict[str, Any],
             extra: Optional[Dict[str, Any]] = None):
    rep = c["splits"].get(split, {})
    a, b = B.SPLITS.get(split, ("", ""))
    return models.LabRun(
        strategy_id=sid, market=c["market"], timeframe=c["timeframe"], kind=kind,
        params=c["selected_params"], split=split, period_start=a, period_end=b,
        metrics=_jsonable(metrics), by_regime=rep.get("by_regime", {}) if kind == "backtest" else {},
        by_session=rep.get("by_session", {}) if kind == "backtest" else {},
        by_symbol=rep.get("by_symbol", {}) if kind == "backtest" else {},
        equity_curve=rep.get("equity_curve", []) if kind == "backtest" else [],
        monthly=rep.get("monthly", {}) if kind == "backtest" else {},
        costs=(extra or {}).get("costs", {}),
        data_coverage=(extra or {}).get("coverage", {}))


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))


def _fit(value: Any, width: int) -> str:
    """Fit a label into a fixed-width String column without silent truncation:
    'insufficient data' becomes 'insufficient', or 'n/a' where even that is too long."""
    s = str(value or "")
    if s == "insufficient data":
        s = "insufficient"
    return s if len(s) <= width else "n/a"


async def persist(result: Dict[str, Any]) -> None:
    from sqlalchemy import delete, select

    from app import models
    from app.db import SessionLocal

    sid = result["strategy_id"]
    async with SessionLocal() as s:
        row = (await s.execute(select(models.LabStrategy).where(models.LabStrategy.strategy_id == sid))
               ).scalar_one_or_none()
        if row is None:
            row = models.LabStrategy(strategy_id=sid, optimization_count=0)
            s.add(row)
        best_key = f"{result.get('best_market')}/{result.get('best_timeframe')}"
        best = result["combos"].get(best_key) or (next(iter(result["combos"].values()), None))
        row.name, row.family, row.category = result["name"], result["family"], result["category"]
        row.hypothesis, row.markets, row.timeframes = result["hypothesis"], result["markets"], result["timeframes"]
        row.hold, row.stop_method, row.version = result["hold"], result["stop_method"], result["version"]
        if row.stage not in B.PAPER_STAGES:          # paper-derived stages are never overwritten here
            row.stage = result["stage"]
        row.stage_reason = result.get("stage_reason", "")
        row.optimization_count = (row.optimization_count or 0) + (1 if result["combos"] else 0)
        row.params = _jsonable(best["selected_params"]) if best else {}
        row.best_market = _fit(result.get("best_market"), 16)
        row.best_timeframe = _fit(result.get("best_timeframe"), 8)
        row.best_regime = _fit(result.get("best_regime"), 24)
        row.worst_regime = _fit(result.get("worst_regime"), 24)
        row.composite_score = float(result.get("composite_score") or 0.0)

        for c in result["combos"].values():
            extra = {"costs": result["costs"],
                     "coverage": {"symbols": c["symbols"], "series": c["coverage"],
                                  "forward_series": c.get("forward_coverage", []),
                                  "counts": c.get("counts", {})}}
            for split in ("train", "validation", "oos", "forward"):
                m = c["splits"][split]["metrics"]
                if m["n"] == 0 and split != "oos":
                    continue
                s.add(_run_row(models, sid, c, split, "backtest", m, extra))
            s.add(_run_row(models, sid, c, "train", "robustness",
                           {"robustness": c["robustness"], "grid": c["grid"], "stage": c["stage"],
                            "stage_reason": c["stage_reason"]}, extra))
            s.add(_run_row(models, sid, c, "oos", "montecarlo", c["monte_carlo"], extra))

        # backtest-cohort trades are regenerated by every run; paper/live cohorts are untouched
        await s.execute(delete(models.LabTrade).where(models.LabTrade.strategy_id == sid,
                                                      models.LabTrade.cohort == "backtest"))
        for c in result["combos"].values():
            for split in ("oos", "forward"):
                for t in c["trades"][split]:
                    s.add(models.LabTrade(
                        strategy_id=sid, cohort="backtest", split=split, market=t["market"],
                        symbol=t["symbol"], timeframe=t["timeframe"], direction=t["direction"],
                        signal_time=_dt(t["signal_time"]), entry_time=_dt(t["entry_time"]),
                        exit_time=_dt(t.get("exit_time")), entry_price=t["entry"],
                        stop_price=t["stop"], target_1=t["target1"], target_2=t["target2"],
                        exit_price=t.get("exit_price"), exit_reason=t.get("exit_reason", ""),
                        mfe_r=t.get("mfe_r"), mae_r=t.get("mae_r"), return_pct=t.get("return_pct"),
                        r_multiple=t.get("r_multiple"), pnl_usd=None, result=t["result"],
                        regime=t.get("regime", ""), session_bucket=t.get("session", ""),
                        confidence=t.get("confidence"), reasons=_jsonable(t.get("reasons", [])),
                        invalidation=t.get("invalidation", ""), features=_jsonable(t.get("features", {})),
                        params=_jsonable(t.get("params", {}))))
        await s.commit()


async def persist_summaries(results: Sequence[Dict[str, Any]]) -> None:
    """Composite + stage / best-of fields onto existing LabStrategy rows (paper-
    derived stages are never overwritten)."""
    from sqlalchemy import select

    from app import models
    from app.db import SessionLocal
    async with SessionLocal() as s:
        for r in results:
            row = (await s.execute(select(models.LabStrategy).where(models.LabStrategy.strategy_id == r["strategy_id"]))
                   ).scalar_one_or_none()
            if row is None:
                continue
            row.composite_score = float(r.get("composite_score") or 0.0)
            if row.stage not in B.PAPER_STAGES:
                row.stage = r["stage"]
            row.stage_reason = r.get("stage_reason", "")
            row.best_market = _fit(r.get("best_market"), 16)
            row.best_timeframe = _fit(r.get("best_timeframe"), 8)
            row.best_regime = _fit(r.get("best_regime"), 24)
            row.worst_regime = _fit(r.get("worst_regime"), 24)
        await s.commit()


# ------------------------------------------------------------ leaderboard ----

def build_leaderboard(results: List[Dict[str, Any]], skipped: Dict[str, str]) -> Dict[str, Any]:
    """One row per strategy (its best OOS combo, else its first combo) ranked by
    the composite; plus every combo for the per-market/timeframe view."""
    rows, combo_rows = [], []
    for r in results:
        key = f"{r.get('best_market')}/{r.get('best_timeframe')}"
        c = r["combos"].get(key) or next(iter(r["combos"].values()), None)
        oos = c["splits"]["oos"]["metrics"] if c else B.metrics([])
        rows.append({"strategy_id": r["strategy_id"], "name": r["name"], "family": r["family"],
                     "stage": r["stage"], "stage_reason": r.get("stage_reason", ""),
                     "best_market": r.get("best_market"), "best_timeframe": r.get("best_timeframe"),
                     "best_regime": r.get("best_regime"), "worst_regime": r.get("worst_regime"),
                     "params": c["selected_params"] if c else {},
                     "robustness": c["robustness"] if c else None,
                     "oos": B.compact(oos), "oos_sortino": oos.get("sortino"),
                     "oos_consistency": oos.get("consistency"),
                     "forward": B.compact(c["splits"]["forward"]["metrics"]) if c else None,
                     "mc_p5_x1.5": ((c or {}).get("monte_carlo", {}).get("stress", {})
                                    .get("slip_x1.5", {}) or {}).get("expectancy_p5"),
                     "errors": len(r.get("errors", []))})
        for ck, c in r["combos"].items():
            combo_rows.append({"strategy_id": r["strategy_id"], "combo": ck, "stage": c["stage"],
                               "params": c["selected_params"], "robustness": c["robustness"],
                               "train": B.compact(c["splits"]["train"]["metrics"]),
                               "validation": B.compact(c["splits"]["validation"]["metrics"]),
                               "oos": B.compact(c["splits"]["oos"]["metrics"]),
                               "forward": B.compact(c["splits"]["forward"]["metrics"])})
    # Only strategies whose best combo reached the OOS sample floor (_best_of's
    # min_n) are ranked: a 1-trade OOS with PF=100 must not outrank a 40-trade
    # edge. Everything else carries composite 0.0 and sorts below the ranked set.
    ranked = [r for r in rows if r["best_market"] != "insufficient data"]
    scores = B.composite_scores([{**r["oos"], "sortino": r["oos_sortino"], "consistency": r["oos_consistency"]}
                                 for r in ranked]) if ranked else []
    for row in rows:
        row["composite_score"], row["ranked"] = 0.0, False
    for row, sc in zip(ranked, scores):
        row["composite_score"], row["ranked"] = round(sc, 4), True
    rows.sort(key=lambda x: (not x["ranked"], -x["composite_score"], x["strategy_id"]))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "splits": B.SPLITS,
            "composite_components": list(B.COMPOSITE_COMPONENTS), "strategies": rows,
            "combos": combo_rows, "skipped": skipped,
            "markets": {"available": sorted(B.UNIVERSE), "pairs": dict(B.PAIRS),
                        "options": {"status": "not available on data plan"}}}


# ------------------------------------------------------------------- main ----

async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    found, errors = registry.load_report()
    loaded = {s.id: (s.meta, s.signal) for s in found}
    for stem, why in errors.items():
        log(f"SKIP {stem}: {why}")
    if args.list:
        for sid, (meta, _) in sorted(loaded.items()):
            print(f"{sid:36s} {meta.family:16s} {','.join(meta.markets):18s} {','.join(meta.timeframes)}")
        return 0
    if args.rebuild_leaderboard:
        results = [json.loads(p.read_text()) for p in sorted(out.glob("s*.json"))]
        if not results:
            log(f"no strategy results in {out}")
            return 1
        return await finish(results, dict(errors), out, persist_db=not args.no_db)
    want = _csv(args.strategies)
    ids = [s for s in sorted(loaded) if not want or s in want]
    for w in want:
        if w not in loaded:
            log(f"SKIP {w}: not discovered")
    if not ids:
        log("no strategies to run")
        return 1
    await ensure_spy_daily()
    markets, tfs = _csv(args.markets), _csv(args.timeframes)
    symbols = _csv(args.symbols) or (B.QUICK_SYMBOLS if args.quick else None)
    fwd = B.forward_symbols()
    if args.quick:
        fwd = fwd[:40]
    log(f"strategies={len(ids)} markets={markets} timeframes={tfs} symbols={symbols or 'all'} "
        f"forward_movers={len(fwd)} workers={args.workers} db={'off' if args.no_db else 'on'}")

    pool = ProcessPoolExecutor(max_workers=args.workers) if args.workers > 1 else None
    map_fn = (lambda fn, jobs: pool.map(fn, jobs, chunksize=1)) if pool else serial_map
    results: List[Dict[str, Any]] = []
    skipped: Dict[str, str] = dict(errors)
    try:
        for sid in ids:
            meta, _ = loaded[sid]
            t0 = time.time()
            try:
                res = B.run_strategy(meta, markets, tfs, map_fn, symbols_only=symbols, quick=args.quick,
                                     lookback=args.lookback, exit_model=args.exit_model,
                                     forward_syms=fwd, logger=log)
            except Exception as exc:  # one broken strategy never stops the run
                skipped[sid] = f"run failed: {type(exc).__name__}: {exc}"
                log(f"SKIP {sid}: {skipped[sid]}")
                continue
            res["elapsed_s"] = round(time.time() - t0, 1)
            (out / f"{sid}.json").write_text(json.dumps(res, default=str, indent=1))
            results.append(res)
            log(f"{sid}: stage={res['stage']} best={res.get('best_market')}/{res.get('best_timeframe')} "
                f"in {res['elapsed_s']}s")
            if not args.no_db:
                try:
                    await persist(res)
                except Exception as exc:
                    log(f"DB persist failed for {sid}: {type(exc).__name__}: {exc}")
    finally:
        if pool:
            pool.shutdown(wait=True)

    return await finish(results, skipped, out, persist_db=not args.no_db)


async def finish(results: List[Dict[str, Any]], skipped: Dict[str, str], out: pathlib.Path,
                 persist_db: bool = True) -> int:
    """Leaderboard + composites from finished per-strategy results; stage and
    best-of fields are recomputed from the combos so a rebuild stays consistent."""
    for res in results:
        if res.get("combos"):
            res.update(B.summarize(res["combos"]))
    board = build_leaderboard(results, skipped)
    (out / "leaderboard.json").write_text(json.dumps(board, default=str, indent=1))
    scores = {r["strategy_id"]: r["composite_score"] for r in board["strategies"]}
    for res in results:                                 # keep per-strategy files consistent
        res["composite_score"] = scores.get(res["strategy_id"], 0.0)
        (out / f"{res['strategy_id']}.json").write_text(json.dumps(res, default=str, indent=1))
    if persist_db and scores:
        try:
            await persist_summaries(results)
        except Exception as exc:
            log(f"DB summary update failed: {type(exc).__name__}: {exc}")
    n_ranked = sum(1 for r in board["strategies"] if r.get("ranked"))
    log(f"leaderboard: {len(board['strategies'])} strategies ({n_ranked} ranked, "
        f"{len(board['strategies']) - n_ranked} insufficient OOS data) -> {out / 'leaderboard.json'}")
    for row in board["strategies"]:
        log(f"  #{row['rank']:<3} {row['strategy_id']:34s} {row['stage']:16s} composite={row['composite_score']:.3f} "
            f"oos n={row['oos']['n']} e={row['oos']['expectancy_r']}"
            + ("" if row.get("ranked") else "  [unranked: insufficient OOS data]"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
