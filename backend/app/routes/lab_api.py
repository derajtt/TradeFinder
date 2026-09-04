"""Quant Lab API: read-only views over LabStrategy / LabRun / LabTrade rows plus
one admin action (stage change).

Every number returned here is either read from a stored row or derived from
stored rows in this module (a Wilson bound from stored wins/n, a drawdown from a
stored equity curve, expectancy from stored LabTrade r_multiples). Nothing is
invented when data is absent: missing metrics are returned as null and a
warning names what is missing.

LabRun.metrics keys this module reads (the harness writes them; aliases in
parentheses are accepted for robustness):
    trades (n, n_trades, count), wins (n_wins), losses, win_rate (fraction),
    expectancy (expectancy_r -- what the harness writes -- avg_r, exp_r; in R),
    profit_factor (pf), max_drawdown (max_dd,
    max_drawdown_pct, max_drawdown_r), sharpe, sortino, consistency
    (fraction of profitable months), avg_hold_bars (avg_hold), composite
    (composite_score).
LabRun.equity_curve is accepted as a list of numbers, of [t, value] pairs, or of
dicts with a time key (t/time/date/ts) and a value key (equity/value/v/cum_r/r).
"""
from __future__ import annotations

import importlib
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..lab.base import FAMILIES, STAGES
from ..models import LabRun, LabStrategy, LabTrade
from ..util.timeutil import ET

router = APIRouter(prefix="/api/lab")

OPTIONS_STATUS = {"status": "not available on data plan"}
SMALL_SAMPLE_N = 30
SORT_KEYS = ["composite", "expectancy", "profit_factor", "max_drawdown", "sharpe",
             "sortino", "trades", "consistency", "stocks", "crypto", "etf"]
MARKET_SORTS = {"stocks", "crypto", "etf"}
SPLIT_PRIORITY = ["oos", "validation", "all", "train"]
RESULT_KINDS = ("backtest", "walkforward")
LAB_REGIMES = ["trend_up", "trend_down", "range", "high_vol", "low_vol", "bear"]
_ID_RE = re.compile(r"^[a-z0-9_]+$")


# ── pure helpers ─────────────────────────────────────────────────────────────

def wilson_lb(wins: int, n: int, z: float = 1.96) -> Optional[float]:
    """Lower bound of the Wilson score interval for a win rate."""
    if n <= 0:
        return None
    p = wins / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((centre - rad) / den, 4)


def confidence_label(n: Optional[int]) -> str:
    """Sample-size confidence: <30 VERY LOW, 30-99 LOW, 100-499 MODERATE, 500+ HIGH."""
    if n is None or n < 30:
        return "VERY LOW"
    if n < 100:
        return "LOW"
    if n < 500:
        return "MODERATE"
    return "HIGH"


def _num(d: Dict[str, Any], *keys: str) -> Optional[float]:
    """First numeric value found under any alias; None when absent."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            return v
    return None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _et_day(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date().isoformat()


def _small_sample_warning(n: Optional[float]) -> Optional[str]:
    if n is None:
        return "no trade count stored for this run"
    if n < SMALL_SAMPLE_N:
        return f"small sample: {int(n)} trades (<{SMALL_SAMPLE_N}) - metrics are unreliable"
    return None


def _curve_points(curve: Any) -> List[Tuple[Any, float]]:
    """Normalise a stored equity curve into [(time_key, value)] without inventing
    values. Unparseable entries are skipped."""
    out: List[Tuple[Any, float]] = []
    if not isinstance(curve, list):
        return out
    for p in curve:
        t: Any = None          # None = the stored point carried no time key
        v: Any = None
        if isinstance(p, (int, float)) and not isinstance(p, bool):
            v = p
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            t, v = p[0], p[1]
        elif isinstance(p, dict):
            for tk in ("t", "time", "date", "ts"):
                if tk in p:
                    t = p[tk]
                    break
            for vk in ("equity", "value", "v", "cum_r", "r", "e"):
                if vk in p:
                    v = p[vk]
                    break
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((t, float(v)))
    return out


def _drawdown_series(values: Sequence[float]) -> Tuple[List[float], Optional[float]]:
    """Peak-to-trough drawdown in the curve's own units (<= 0), plus the worst."""
    dd: List[float] = []
    peak: Optional[float] = None
    for v in values:
        peak = v if peak is None else max(peak, v)
        dd.append(round(v - peak, 6))
    return dd, (min(dd) if dd else None)


def trade_stats(trades: Sequence[LabTrade]) -> Dict[str, Any]:
    """Aggregate closed LabTrade rows (those with a stored r_multiple)."""
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    n = len(rs)
    wins = sum(1 for r in rs if r > 0)
    losses = sum(1 for r in rs if r < 0)
    gross_profit = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    holds = [(t.exit_time - t.entry_time).total_seconds() / 60.0
             for t in trades if t.entry_time and t.exit_time]
    return {
        "n": n, "wins": wins, "losses": losses, "open": len(trades) - n,
        "win_rate": round(wins / n, 4) if n else None,
        "expectancy": round(sum(rs) / n, 4) if n else None,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "total_r": round(sum(rs), 4) if n else None,
        "avg_hold_minutes": round(sum(holds) / len(holds), 1) if holds else None,
        "wilson_lb": wilson_lb(wins, n),
        "confidence": confidence_label(n),
        "small_sample": n < SMALL_SAMPLE_N,
        "warning": _small_sample_warning(n) if n < SMALL_SAMPLE_N else None,
    }


def run_summary(run: LabRun) -> Dict[str, Any]:
    """Flatten one LabRun into the fields the UI compares across strategies."""
    m = run.metrics or {}
    n = _num(m, "trades", "n", "n_trades", "count")
    wins = _num(m, "wins", "n_wins")
    win_rate = _num(m, "win_rate")
    if wins is None and win_rate is not None and n:
        frac = win_rate if win_rate <= 1 else win_rate / 100.0
        wins = round(frac * n)
    if win_rate is None and wins is not None and n:
        win_rate = round(wins / n, 4)
    return {
        "run_id": run.id, "kind": run.kind, "split": run.split,
        "market": run.market, "timeframe": run.timeframe,
        "period_start": run.period_start, "period_end": run.period_end,
        "created_at": _iso(run.created_at), "params": run.params or {},
        "n": int(n) if n is not None else None,
        "wins": int(wins) if wins is not None else None,
        "losses": _num(m, "losses", "n_losses"),
        "win_rate": win_rate,
        "expectancy": _num(m, "expectancy", "expectancy_r", "avg_r", "exp_r"),
        "profit_factor": _num(m, "profit_factor", "pf"),
        "max_drawdown": _num(m, "max_drawdown", "max_dd", "max_drawdown_pct", "max_drawdown_r"),
        "sharpe": _num(m, "sharpe"), "sortino": _num(m, "sortino"),
        "consistency": _num(m, "consistency"),
        "avg_hold_bars": _num(m, "avg_hold_bars", "avg_hold"),
        "composite": _num(m, "composite", "composite_score"),
        "wilson_lb": wilson_lb(int(wins), int(n)) if (wins is not None and n) else None,
        "confidence": confidence_label(int(n) if n is not None else None),
        "small_sample": n is None or n < SMALL_SAMPLE_N,
        "warning": _small_sample_warning(n),
        "metrics": m, "costs": run.costs or {}, "data_coverage": run.data_coverage or {},
    }


def _sort_key(run: LabRun) -> Tuple[Any, int]:
    return (run.created_at or datetime.min.replace(tzinfo=timezone.utc), run.id or 0)


def latest_runs(runs: Sequence[LabRun]) -> Dict[Tuple[str, str, str, str], LabRun]:
    """Latest run per (strategy, market, timeframe, split) among result kinds."""
    out: Dict[Tuple[str, str, str, str], LabRun] = {}
    for r in sorted(runs, key=_sort_key, reverse=True):
        if r.kind not in RESULT_KINDS:
            continue
        key = (r.strategy_id, r.market, r.timeframe, r.split or "all")
        out.setdefault(key, r)
    return out


def headline_run(strat: LabStrategy, runs: Sequence[LabRun]) -> Optional[LabRun]:
    """The run a strategy is judged on: most honest split available (oos first),
    at the stored best market/timeframe when such a run exists, else the
    newest run of that split."""
    latest = latest_runs([r for r in runs if r.strategy_id == strat.strategy_id])
    for split in SPLIT_PRIORITY:
        cands = [r for (sid, mk, tf, sp), r in latest.items() if sp == split]
        if not cands:
            continue
        pref = [r for r in cands if r.market == strat.best_market
                and r.timeframe == strat.best_timeframe]
        pool = pref or cands
        return max(pool, key=_sort_key)
    return None


def _strategy_meta_grid(strategy_id: str) -> Optional[Dict[str, Any]]:
    """param_grid lives in the strategy module's META, not in the DB. Read it
    from code when the module exists; never guess."""
    if not _ID_RE.match(strategy_id or ""):
        return None
    try:
        mod = importlib.import_module(f"app.lab.strategies.{strategy_id}")
    except Exception:
        return None
    meta = getattr(mod, "META", None)
    grid = getattr(meta, "param_grid", None)
    return dict(grid) if isinstance(grid, dict) else None


def _strategy_row(s: LabStrategy) -> Dict[str, Any]:
    return {
        "strategy_id": s.strategy_id, "name": s.name, "family": s.family,
        "category": s.category, "hold": s.hold, "stop_method": s.stop_method,
        "stage": s.stage, "stage_reason": s.stage_reason,
        "composite": s.composite_score, "version": s.version,
        "markets": s.markets or [], "timeframes": s.timeframes or [],
        "best_market": s.best_market or None, "best_timeframe": s.best_timeframe or None,
        "best_regime": s.best_regime or None, "worst_regime": s.worst_regime or None,
        "optimization_count": s.optimization_count,
        "created_at": _iso(s.created_at), "updated_at": _iso(s.updated_at),
    }


def _map_scheduler_regime(reg: Optional[Dict[str, Any]]) -> Optional[str]:
    """Translate the live regime controller's labels into lab regime labels."""
    if not reg:
        return None
    state = reg.get("state")
    if state == "trend":
        return "trend_up" if reg.get("dir") == "up" else "trend_down"
    if state == "range":
        return "range"
    if state == "high_risk":
        return "high_vol"
    return None


# ── data access ───────────────────────────────────────────────────────────────

async def _all_strategies(db: AsyncSession) -> List[LabStrategy]:
    return list((await db.execute(
        select(LabStrategy).order_by(desc(LabStrategy.composite_score), LabStrategy.strategy_id)
    )).scalars().all())


async def _runs_for(db: AsyncSession, ids: Optional[Sequence[str]] = None) -> List[LabRun]:
    q = select(LabRun)
    if ids is not None:
        q = q.where(LabRun.strategy_id.in_(list(ids)))
    return list((await db.execute(q.order_by(desc(LabRun.created_at), desc(LabRun.id)))).scalars().all())


async def _forward_trades_by_strategy(db: AsyncSession,
                                      ids: Sequence[str]) -> Dict[str, List[LabTrade]]:
    """Paper/live LabTrade rows grouped by strategy, in one query."""
    out: Dict[str, List[LabTrade]] = defaultdict(list)
    if not ids:
        return out
    rows = (await db.execute(
        select(LabTrade).where(LabTrade.strategy_id.in_(list(ids)),
                               LabTrade.cohort.in_(["paper", "live"]))
    )).scalars().all()
    for t in rows:
        out[t.strategy_id].append(t)
    return out


async def _forward_trades(db: AsyncSession, strategy_id: str) -> List[LabTrade]:
    return (await _forward_trades_by_strategy(db, [strategy_id])).get(strategy_id, [])


def _by_market_breakdown(s: LabStrategy, runs: Sequence[LabRun],
                         forward: Sequence[LabTrade]) -> Tuple[Dict[str, Any], List[str]]:
    latest = latest_runs([r for r in runs if r.strategy_id == s.strategy_id])
    fwd_runs: Dict[Tuple[str, str], LabRun] = {}
    for r in sorted(runs, key=_sort_key, reverse=True):      # newest first, keep first seen
        if r.strategy_id == s.strategy_id and r.split == "forward":
            fwd_runs.setdefault((r.market, r.timeframe), r)
    fwd_trades: Dict[Tuple[str, str], List[LabTrade]] = defaultdict(list)
    for t in forward:
        fwd_trades[(t.market, t.timeframe)].append(t)

    markets = set(s.markets or []) | {k[1] for k in latest} | {k[0] for k in fwd_trades}
    warnings: List[str] = []
    out: Dict[str, Any] = {}
    for mk in sorted(markets):
        if mk == "options":
            continue
        tfs = ({k[2] for k in latest if k[1] == mk} | {k[1] for k in fwd_trades if k[0] == mk}
               | set(s.timeframes or []))
        per_tf: Dict[str, Any] = {}
        for tf in sorted(tfs):
            cell: Dict[str, Any] = {}
            for split in ("train", "validation", "oos"):
                r = latest.get((s.strategy_id, mk, tf, split))
                cell[split] = run_summary(r) if r else None
                if r:
                    w = cell[split]["warning"]
                    if w:
                        warnings.append(f"{mk}/{tf}/{split}: {w}")
            fr = fwd_runs.get((mk, tf))
            if fr:
                cell["forward"] = {**run_summary(fr), "source": "lab_run"}
            else:
                ft = fwd_trades.get((mk, tf), [])
                cell["forward"] = ({**trade_stats(ft), "source": "lab_trades"}
                                   if ft else None)
            if any(cell.values()):
                per_tf[tf] = cell
        if per_tf:
            out[mk] = {"timeframes": per_tf}
    out["options"] = dict(OPTIONS_STATUS)
    return out, warnings


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/strategies")
async def list_strategies(db: AsyncSession = Depends(get_session)):
    strats = await _all_strategies(db)
    ids = [s.strategy_id for s in strats]
    runs = await _runs_for(db, ids) if strats else []
    forward_by_id = await _forward_trades_by_strategy(db, ids)
    rows = []
    for s in strats:
        by_market, warnings = _by_market_breakdown(s, runs, forward_by_id.get(s.strategy_id, []))
        head = headline_run(s, runs)
        rows.append({
            **_strategy_row(s),
            "headline": run_summary(head) if head else None,
            "by_market": by_market,
            "warnings": warnings,
            "runs_count": sum(1 for r in runs if r.strategy_id == s.strategy_id),
        })
    return {"strategies": rows, "count": len(rows), "families": FAMILIES,
            "stages": STAGES, "options": dict(OPTIONS_STATUS)}


@router.get("/strategies/{strategy_id}")
async def strategy_detail(strategy_id: str, db: AsyncSession = Depends(get_session)):
    s = (await db.execute(select(LabStrategy).where(LabStrategy.strategy_id == strategy_id))
         ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "unknown lab strategy")
    runs = await _runs_for(db, [strategy_id])
    forward = await _forward_trades(db, strategy_id)
    head = headline_run(s, runs)
    by_market, warnings = _by_market_breakdown(s, runs, forward)
    robustness = [run_summary(r) for r in runs if r.kind == "robustness"][:50]
    mc = next((r for r in runs if r.kind == "montecarlo"), None)
    trades = list((await db.execute(
        select(LabTrade).where(LabTrade.strategy_id == strategy_id)
        .order_by(desc(LabTrade.signal_time), desc(LabTrade.id)).limit(50))).scalars().all())
    pts = _curve_points(head.equity_curve) if head else []
    dd, worst = _drawdown_series([v for _, v in pts])
    return {
        **_strategy_row(s),
        "hypothesis": s.hypothesis,
        "params": s.params or {},
        "param_grid": _strategy_meta_grid(strategy_id),
        "headline": run_summary(head) if head else None,
        "by_market": by_market,
        "warnings": warnings,
        "robustness": robustness,
        "monte_carlo": ({"run_id": mc.id, "created_at": _iso(mc.created_at),
                         "market": mc.market, "timeframe": mc.timeframe,
                         "metrics": mc.metrics or {}} if mc else None),
        "by_regime": (head.by_regime or {}) if head else {},
        "by_session": (head.by_session or {}) if head else {},
        "by_symbol": (head.by_symbol or {}) if head else {},
        "equity_curve": (head.equity_curve or []) if head else [],
        "drawdown_curve": dd,
        "max_drawdown_from_curve": worst,
        "monthly": (head.monthly or {}) if head else {},
        "runs": [run_summary(r) for r in runs if r.kind in RESULT_KINDS],
        "recent_trades": [{
            "id": t.id, "cohort": t.cohort, "split": t.split, "market": t.market,
            "symbol": t.symbol, "timeframe": t.timeframe, "direction": t.direction,
            "signal_time": _iso(t.signal_time), "entry_time": _iso(t.entry_time),
            "exit_time": _iso(t.exit_time), "entry_price": t.entry_price,
            "stop_price": t.stop_price, "target_1": t.target_1, "target_2": t.target_2,
            "exit_price": t.exit_price, "exit_reason": t.exit_reason,
            "mfe_r": t.mfe_r, "mae_r": t.mae_r, "return_pct": t.return_pct,
            "r_multiple": t.r_multiple, "pnl_usd": t.pnl_usd, "result": t.result,
            "regime": t.regime, "session_bucket": t.session_bucket,
            "confidence": t.confidence, "reasons": t.reasons or [],
            "invalidation": t.invalidation, "features": t.features or {},
        } for t in trades],
        "forward": trade_stats(forward) if forward else None,
    }


def _market_expectancy(s: LabStrategy, runs: Sequence[LabRun]) -> Dict[str, Optional[float]]:
    """Best-split expectancy per market, from the latest run of that split."""
    latest = latest_runs([r for r in runs if r.strategy_id == s.strategy_id])
    out: Dict[str, Optional[float]] = {}
    for mk in sorted({k[1] for k in latest}):
        for split in SPLIT_PRIORITY:
            cands = [r for (sid, m, tf, sp), r in latest.items() if m == mk and sp == split]
            if cands:
                out[mk] = run_summary(max(cands, key=_sort_key))["expectancy"]
                break
    return out


def leaderboard_rows(strats: Sequence[LabStrategy], runs: Sequence[LabRun]) -> List[Dict[str, Any]]:
    rows = []
    for s in strats:
        head = headline_run(s, runs)
        hs = run_summary(head) if head else None
        rows.append({
            "strategy_id": s.strategy_id, "name": s.name, "family": s.family,
            "stage": s.stage, "composite": s.composite_score,
            "best_market": s.best_market or None, "best_timeframe": s.best_timeframe or None,
            "split": hs["split"] if hs else None,
            "market": hs["market"] if hs else None,
            "timeframe": hs["timeframe"] if hs else None,
            "trades": hs["n"] if hs else None,
            "win_rate": hs["win_rate"] if hs else None,
            "wilson_lb": hs["wilson_lb"] if hs else None,
            "confidence": confidence_label(hs["n"] if hs else None),
            "expectancy": hs["expectancy"] if hs else None,
            "profit_factor": hs["profit_factor"] if hs else None,
            "max_drawdown": hs["max_drawdown"] if hs else None,
            "sharpe": hs["sharpe"] if hs else None,
            "sortino": hs["sortino"] if hs else None,
            "consistency": hs["consistency"] if hs else None,
            "by_market_expectancy": _market_expectancy(s, runs),
            "small_sample": (hs["small_sample"] if hs else True),
            "warning": (hs["warning"] if hs else "no result runs stored"),
        })
    return rows


def sort_leaderboard(rows: List[Dict[str, Any]], sort: str) -> List[Dict[str, Any]]:
    if sort == "win_rate":
        raise HTTPException(400, "win_rate is not a permitted sort key; sort by "
                                 "expectancy or wilson-adjusted metrics instead")
    if sort not in SORT_KEYS:
        raise HTTPException(400, f"sort must be one of {', '.join(SORT_KEYS)}")

    def val(r: Dict[str, Any]) -> Optional[float]:
        if sort in MARKET_SORTS:
            return r["by_market_expectancy"].get(sort)
        return r.get(sort)

    if sort == "max_drawdown":   # smaller magnitude is better
        return sorted(rows, key=lambda r: (val(r) is None, abs(val(r) or 0.0)))
    return sorted(rows, key=lambda r: (val(r) is None, -(val(r) or 0.0)))


@router.get("/leaderboard")
async def leaderboard(sort: str = "composite", db: AsyncSession = Depends(get_session)):
    strats = await _all_strategies(db)
    runs = await _runs_for(db, [s.strategy_id for s in strats]) if strats else []
    rows = leaderboard_rows(strats, runs)
    # The composite is a rank-average computed across the whole field at the end
    # of a full backtest run.  Until that has happened every strategy carries
    # 0.0, and ranking by it would put an arbitrary — possibly losing —
    # strategy first while looking authoritative.
    scored = [r for r in rows if (r.get("composite") or 0.0) > 0.0]
    note = ""
    effective = sort
    if not scored:
        for r in rows:
            r["composite"] = None
        if sort == "composite":
            effective = "expectancy"
            note = ("Composite scores are not computed yet — they need a "
                    "completed run across the whole field.  Ranked by "
                    "out-of-sample expectancy instead.")
    rows = sort_leaderboard(rows, effective)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"sort": sort, "sorted_by": effective, "note": note,
            "composites_ready": bool(scored),
            "sort_keys": SORT_KEYS, "rows": rows,
            "confidence_scale": {"VERY LOW": "<30 trades", "LOW": "30-99",
                                 "MODERATE": "100-499", "HIGH": "500+"}}


@router.get("/compare")
async def compare(ids: str = "", db: AsyncSession = Depends(get_session)):
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    if not wanted:
        raise HTTPException(400, "ids is required, e.g. ?ids=a,b,c")
    if len(wanted) > 6:
        raise HTTPException(400, "compare at most 6 strategies at once")
    strats = list((await db.execute(
        select(LabStrategy).where(LabStrategy.strategy_id.in_(wanted)))).scalars().all())
    found = {s.strategy_id: s for s in strats}
    missing = [i for i in wanted if i not in found]
    if not strats:
        raise HTTPException(404, f"no lab strategies found for {', '.join(wanted)}")
    runs = await _runs_for(db, list(found))
    cols = []
    market_sets: List[set] = []
    for sid in wanted:
        s = found.get(sid)
        if not s:
            continue
        head = headline_run(s, runs)
        hs = run_summary(head) if head else None
        pts = _curve_points(head.equity_curve) if head else []
        dd, worst = _drawdown_series([v for _, v in pts])
        run_markets = sorted({r.market for r in runs if r.strategy_id == sid})
        market_sets.append(set(s.markets or []) | set(run_markets))
        forward = await _forward_trades(db, sid)
        cols.append({
            **_strategy_row(s),
            "headline": hs,
            "equity_curve": (head.equity_curve or []) if head else [],
            "drawdown_curve": dd,
            "max_drawdown_from_curve": worst,
            "monthly": (head.monthly or {}) if head else {},
            "win_rate": hs["win_rate"] if hs else None,
            "wilson_lb": hs["wilson_lb"] if hs else None,
            "profit_factor": hs["profit_factor"] if hs else None,
            "expectancy": hs["expectancy"] if hs else None,
            "n": hs["n"] if hs else None,
            "confidence": confidence_label(hs["n"] if hs else None),
            "max_drawdown": hs["max_drawdown"] if hs else None,
            "avg_hold_bars": hs["avg_hold_bars"] if hs else None,
            "avg_hold_minutes_forward": trade_stats(forward)["avg_hold_minutes"] if forward else None,
            "markets_declared": s.markets or [],
            "markets_with_runs": run_markets,
        })
    common = sorted(set.intersection(*market_sets)) if market_sets else []
    same_family = [[a["strategy_id"], b["strategy_id"]]
                   for i, a in enumerate(cols) for b in cols[i + 1:]
                   if a["family"] == b["family"]]
    return {"strategies": cols, "missing": missing,
            "market_compatibility": {
                "common_markets": common,
                "all_share_a_market": bool(common),
                "distinct_families": len({c["family"] for c in cols}),
                "same_family_pairs": same_family,
                "options": dict(OPTIONS_STATUS)}}


@router.get("/ensemble")
async def ensemble(db: AsyncSession = Depends(get_session)):
    """Symbol-day agreement across FAMILIES. Two strategies of one family on
    the same symbol-day count as one voice; a family's observation for that
    symbol-day is the mean r_multiple of its closed trades there."""
    strats = await _all_strategies(db)
    family_of = {s.strategy_id: s.family for s in strats}
    trades = list((await db.execute(
        select(LabTrade).where(LabTrade.cohort.in_(["backtest", "paper"]))
        .order_by(LabTrade.signal_time))).scalars().all())

    groups: Dict[Tuple[str, str, str], Dict[str, List[LabTrade]]] = defaultdict(lambda: defaultdict(list))
    for t in trades:
        fam = family_of.get(t.strategy_id)
        if not fam or not t.signal_time:
            continue
        groups[(t.symbol, _et_day(t.signal_time), t.direction or "long")][fam].append(t)

    buckets: Dict[str, List[float]] = {"1": [], "2": [], "3+": []}
    days = []
    for (sym, day, direction), fams in groups.items():
        k = len(fams)
        label = "1" if k == 1 else ("2" if k == 2 else "3+")
        obs = []
        for fam, ts in fams.items():
            rs = [t.r_multiple for t in ts if t.r_multiple is not None]
            if rs:
                obs.append(sum(rs) / len(rs))
        buckets[label].extend(obs)
        if k >= 2:
            days.append({
                "symbol": sym, "day": day, "direction": direction, "families_agreeing": k,
                "families": sorted(fams),
                "strategies": sorted({t.strategy_id for ts in fams.values() for t in ts}),
                "tracked_outcome": ({"n_families_closed": len(obs),
                                     "mean_r": round(sum(obs) / len(obs), 4)} if obs
                                    else {"n_families_closed": 0, "mean_r": None}),
            })
    days.sort(key=lambda d: (d["day"], d["symbol"]), reverse=True)

    def summarise(rs: List[float]) -> Dict[str, Any]:
        n = len(rs)
        wins = sum(1 for r in rs if r > 0)
        gp = sum(r for r in rs if r > 0)
        gl = -sum(r for r in rs if r < 0)
        return {"n": n, "wins": wins, "expectancy": round(sum(rs) / n, 4) if n else None,
                "win_rate": round(wins / n, 4) if n else None,
                "profit_factor": round(gp / gl, 3) if gl > 0 else None,
                "wilson_lb": wilson_lb(wins, n), "confidence": confidence_label(n),
                "small_sample": n < SMALL_SAMPLE_N}

    singles = summarise(buckets["1"])
    agreeing = summarise(buckets["2"] + buckets["3+"])
    improved: Optional[bool] = None
    delta: Optional[float] = None
    if singles["expectancy"] is not None and agreeing["expectancy"] is not None:
        delta = round(agreeing["expectancy"] - singles["expectancy"], 4)
        improved = delta > 0
    return {
        "families": FAMILIES,
        "by_agreement": {"1": singles, "2": summarise(buckets["2"]),
                         "3+": summarise(buckets["3+"]), "2+": agreeing},
        "agreement_improves_expectancy": improved,
        "expectancy_delta_vs_singles": delta,
        "symbol_days_total": len(groups),
        "symbol_days_with_agreement": len(days),
        "recent_agreements": days[:50],
        "note": ("agreement counts distinct families only; strategies sharing a family "
                 "never add a second vote"),
    }


def select_portfolio(strats: Sequence[LabStrategy], top_n: int = 5,
                     max_per_family: int = 2) -> List[LabStrategy]:
    chosen: List[LabStrategy] = []
    per_family: Dict[str, int] = defaultdict(int)
    for s in sorted(strats, key=lambda x: (-(x.composite_score or 0.0), x.strategy_id)):
        if per_family[s.family] >= max_per_family:
            continue
        chosen.append(s)
        per_family[s.family] += 1
        if len(chosen) >= top_n:
            break
    return chosen


def combine_curves(legs: Dict[str, List[Tuple[Any, float]]]) -> Dict[str, Any]:
    """Equal-risk combination: every leg is sized so 1R is the same dollar
    amount, so the portfolio is the mean of each leg's cumulative gain since its
    own start. Legs are aligned on their time keys when all carry them, else on
    bar index; a leg is forward-filled between its own points."""
    if not legs:
        return {"points": [], "max_drawdown": None, "final": None}
    all_have_time = all(all(t is not None for t, _ in pts) for pts in legs.values())
    series: Dict[str, Dict[Any, float]] = {}
    for sid, pts in legs.items():
        base = pts[0][1]
        series[sid] = {(t if all_have_time else i): v - base for i, (t, v) in enumerate(pts)}
    keys = sorted({k for s in series.values() for k in s}, key=lambda k: (str(type(k)), k))
    last: Dict[str, float] = {sid: 0.0 for sid in series}
    combined: List[float] = []
    for k in keys:
        for sid, s in series.items():
            if k in s:
                last[sid] = s[k]
        combined.append(round(sum(last.values()) / len(last), 6))
    dd, worst = _drawdown_series(combined)
    return {"points": [{"t": (k if all_have_time else int(k)), "value": v, "drawdown": d}
                       for k, v, d in zip(keys, combined, dd)],
            "max_drawdown": worst, "final": combined[-1] if combined else None,
            "aligned_on": "time" if all_have_time else "index"}


@router.get("/portfolio")
async def portfolio(db: AsyncSession = Depends(get_session)):
    strats = await _all_strategies(db)
    chosen = select_portfolio(strats)
    runs = await _runs_for(db, [s.strategy_id for s in chosen]) if chosen else []
    legs: Dict[str, List[Tuple[Any, float]]] = {}
    rows = []
    best_single: Optional[Dict[str, Any]] = None
    for s in chosen:
        head = headline_run(s, runs)
        hs = run_summary(head) if head else None
        pts = _curve_points(head.equity_curve) if head else []
        _, worst = _drawdown_series([v for _, v in pts])
        if pts:
            legs[s.strategy_id] = pts
        rows.append({**_strategy_row(s), "headline": hs, "weight": None,
                     "has_curve": bool(pts), "max_drawdown_from_curve": worst,
                     "gain_from_curve": (pts[-1][1] - pts[0][1]) if pts else None})
        if pts and (best_single is None or rows[-1]["gain_from_curve"] > best_single["gain_from_curve"]):
            best_single = {"strategy_id": s.strategy_id, "gain_from_curve": rows[-1]["gain_from_curve"],
                           "max_drawdown_from_curve": worst,
                           "max_drawdown_stored": hs["max_drawdown"] if hs else None}
    for r in rows:
        r["weight"] = round(1.0 / len(legs), 4) if (legs and r["has_curve"]) else None
    combined = combine_curves(legs) if legs else None
    fam_counts: Dict[str, int] = defaultdict(int)
    for s in chosen:
        fam_counts[s.family] += 1
    return {
        "constraint": {"top_n": 5, "max_per_family": 2, "sizing": "equal-risk (equal 1R per leg)"},
        "legs": rows, "families": dict(fam_counts),
        "combined": combined,
        "best_single": best_single,
        "diversification_benefit": (round(combined["max_drawdown"] - best_single["max_drawdown_from_curve"], 6)
                                    if combined and best_single and combined["max_drawdown"] is not None
                                    and best_single["max_drawdown_from_curve"] is not None else None),
        "note": (None if legs else "no stored equity curves among the selected legs; "
                                  "nothing to combine"),
    }


def _current_regime(request: Optional[Request]) -> Dict[str, Any]:
    sched = None
    try:
        sched = (request.app.state.shared or {}).get("scheduler") if request is not None else None
    except AttributeError:
        sched = None
    raw = getattr(sched, "last_regime", None) if sched is not None else None
    return {"label": _map_scheduler_regime(raw), "source": "scheduler" if raw else "none",
            "raw": raw}


@router.get("/regimes")
async def regimes(request: Request, db: AsyncSession = Depends(get_session)):
    current = _current_regime(request)
    if current["label"] is None:
        last = (await db.execute(select(LabTrade).where(LabTrade.regime != "")
                                 .order_by(desc(LabTrade.signal_time)).limit(1))).scalar_one_or_none()
        if last:
            current = {"label": last.regime, "source": "latest_lab_trade",
                       "as_of": _iso(last.signal_time), "raw": current["raw"]}
    strats = await _all_strategies(db)
    runs = await _runs_for(db, [s.strategy_id for s in strats]) if strats else []
    rows = []
    for s in strats:
        head = headline_run(s, runs)
        by_regime = (head.by_regime or {}) if head else {}
        rows.append({
            "strategy_id": s.strategy_id, "name": s.name, "family": s.family, "stage": s.stage,
            "best_regime": s.best_regime or None, "worst_regime": s.worst_regime or None,
            "by_regime": by_regime,
            "in_current_regime": by_regime.get(current["label"]) if current["label"] else None,
            "favoured_now": (current["label"] == s.best_regime) if (current["label"] and s.best_regime) else None,
            "avoid_now": (current["label"] == s.worst_regime) if (current["label"] and s.worst_regime) else None,
        })
    return {"current": current, "regimes": LAB_REGIMES, "strategies": rows}


@router.post("/strategies/{strategy_id}/stage")
async def set_stage(strategy_id: str, payload: Dict[str, Any] = Body(...),
                    db: AsyncSession = Depends(get_session)):
    """Admin: move a strategy to a new lifecycle stage. The reason is appended
    to stage_reason so the history of promotions and demotions is never lost.
    Protected like every /api route by the API-key guard in main.py."""
    stage = str(payload.get("stage") or "").upper()
    reason = str(payload.get("reason") or "").strip()
    if stage not in STAGES:
        raise HTTPException(400, f"stage must be one of {', '.join(STAGES)}")
    if not reason:
        raise HTTPException(400, "reason is required")
    s = (await db.execute(select(LabStrategy).where(LabStrategy.strategy_id == strategy_id))
         ).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "unknown lab strategy")
    actor = str(payload.get("actor") or "admin")[:48]
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{ts}] {s.stage} -> {stage} ({actor}): {reason}"
    s.stage_reason = (s.stage_reason + "\n" + line) if s.stage_reason else line
    previous = s.stage
    s.stage = stage
    await db.commit()
    return {"ok": True, "strategy_id": strategy_id, "previous_stage": previous,
            "stage": stage, "stage_reason": s.stage_reason}
