"""Risk layer, Extreme Reversion, system health, and dataset administration."""
from __future__ import annotations

import json
import pathlib
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import (DatasetRun, PaperAccount, PaperPosition, ReversionSignal,
                      StrategyChangeLog, StrategyHeartbeat)
from ..risk.engine import (RISK_DEFAULTS, build_trade_plan, circuit_breaker_state,
                           correlation_group, portfolio_risk, size_position)
from ..risk.roadmap import build_roadmap
from ..settings_service import get_settings, update_settings
from ..strategy import reversion as REV
from ..strategy.registry import MODELS, RISK_MODELS
from ..util.timeutil import now_et, now_utc

router = APIRouter(prefix="/api")

def _data_dir() -> pathlib.Path:
    """Study outputs live at the repo root locally, but the container's build
    context is backend/, so the same relative path resolves differently there.
    Check the plausible locations rather than assuming one layout."""
    import os
    here = pathlib.Path(__file__).resolve()
    cands = []
    if os.environ.get("REV_OUT_DIR"):
        cands.append(pathlib.Path(os.environ["REV_OUT_DIR"]))
    cands += [
        here.parents[3] / "data" / "rev_out",     # local checkout
        pathlib.Path("/app/data_root/rev_out"),   # mounted repo data in Docker
        here.parents[2] / "data" / "rev_out",     # backend/data
    ]
    for c in cands:
        if c.is_dir():
            return c
    return cands[0]


DATA = _data_dir()
STALE_HEARTBEAT_S = 1800


def _load(name: str) -> Optional[dict]:
    p = _data_dir() / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return None


async def _risk_settings(db) -> Dict[str, Any]:
    s = await get_settings(db)
    raw = s.get("risk_settings") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    return {**RISK_DEFAULTS, **raw}


# ------------------------------------------------------------ risk config ---

@router.get("/risk/settings")
async def risk_settings(db: AsyncSession = Depends(get_session)):
    cfg = await _risk_settings(db)
    return {"settings": cfg, "defaults": RISK_DEFAULTS,
            "risk_pct_options": [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
            "warn_above_pct": 2.0,
            "note": "Risk above 2% per trade is not recommended. Position size "
                    "is always derived from the stop distance, never the other "
                    "way round."}


@router.put("/risk/settings")
async def update_risk_settings(payload: Dict[str, Any] = Body(...),
                               db: AsyncSession = Depends(get_session)):
    cur = await _risk_settings(db)
    allowed = set(RISK_DEFAULTS)
    bad = [k for k in payload if k not in allowed]
    if bad:
        raise HTTPException(400, f"unknown risk setting(s): {', '.join(sorted(bad))}")
    merged = {**cur, **payload}
    for k in ("default_risk_pct", "max_risk_pct", "max_total_open_risk_pct"):
        if merged.get(k) is not None and float(merged[k]) > 10:
            raise HTTPException(400, f"{k} above 10% is refused as a safety limit")
    if float(merged["default_risk_pct"]) > float(merged["max_risk_pct"]):
        raise HTTPException(400, "default risk cannot exceed the maximum risk")
    await update_settings(db, {"risk_settings": merged})
    return {"ok": True, "settings": merged,
            "warning": ("Risk per trade above 2% is aggressive and is not "
                        "recommended." if float(merged["default_risk_pct"]) > 2
                        else None)}


@router.post("/risk/calculator")
async def risk_calculator(payload: Dict[str, Any] = Body(...),
                          db: AsyncSession = Depends(get_session)):
    """Standalone sizing tool, and the what-if engine. Changes nothing."""
    cfg = await _risk_settings(db)
    try:
        equity = float(payload.get("account_equity") or cfg["account_equity"])
        risk_pct = float(payload.get("risk_pct") or cfg["default_risk_pct"])
        entry = float(payload["entry"])
        stop = float(payload["stop"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "entry and stop are required numbers")
    direction = payload.get("direction", "long")
    s = size_position(equity, risk_pct, entry, stop, direction,
                      max_position_pct=float(cfg["max_position_pct"]),
                      allow_fractional=bool(payload.get("allow_fractional",
                                                        cfg["allow_fractional"])),
                      leverage=float(payload.get("leverage", cfg["leverage"])),
                      slippage_pct=float(cfg["slippage_pct"]),
                      commission_pct=float(cfg["commission_pct"]))
    targets = payload.get("targets") or []
    risk_unit = abs(entry - stop)
    rr = [{"price": float(t), "r": round(abs(float(t) - entry) / risk_unit, 2)}
          for t in targets if risk_unit > 0]
    return {"sizing": s, "targets": rr,
            "explanation": (
                f"Risking {risk_pct:.2f}% of ${equity:,.0f} is "
                f"${equity * risk_pct / 100:,.2f}. Your stop is "
                f"${risk_unit:,.4f} away from entry, so "
                f"${equity * risk_pct / 100:,.2f} ÷ ${risk_unit:,.4f} = "
                f"{s.get('uncapped_quantity', 0):,.2f} units before account limits."
                if s.get("valid") else s.get("reason", ""))}


# ------------------------------------------------------ extreme reversion ---

def _sig_row(r: ReversionSignal, brief: bool = False) -> Dict[str, Any]:
    base = {
        "signal_uid": r.signal_uid, "symbol": r.symbol,
        "asset_class": r.asset_class, "timeframe": r.timeframe,
        "direction": r.direction, "variant": r.variant,
        "strategy_version": r.strategy_version, "dataset_run": r.dataset_run,
        "score": r.signal_score, "score_band": r.score_band,
        "status": r.status, "win_loss": r.win_loss,
        "entry": r.entry_price, "stop": r.stop_price,
        "entry_zone": [r.entry_zone_low, r.entry_zone_high],
        "no_chase": r.no_chase_price,
        "targets": r.targets_json or [],
        "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "regime": r.market_regime, "session": r.session_bucket,
        "actionable": bool((r.trade_plan or {}).get("actionable")),
        "no_trade_reason": (r.trade_plan or {}).get("reason"),
    }
    if brief:
        return base
    base.update({
        "roadmap": r.roadmap or {}, "trade_plan": r.trade_plan or {},
        "why": r.explain_lines or [], "score_parts": r.score_parts or {},
        "events": r.events or [],
        "indicators": r.indicator_snapshot_json or {},
        "parameters": r.parameters_json or {},
        "exit": {"price": r.exit_price, "reason": r.exit_reason,
                 "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                 "gross_pct": r.gross_return_pct, "net_pct": r.net_return_pct,
                 "r_multiple": r.r_multiple, "mfe_r": r.mfe_r, "mae_r": r.mae_r},
    })
    return base


@router.get("/reversion/signals")
async def reversion_signals(status: Optional[str] = None, limit: int = 60,
                            variant: Optional[str] = None,
                            db: AsyncSession = Depends(get_session)):
    q = select(ReversionSignal).order_by(desc(ReversionSignal.id)).limit(min(limit, 300))
    if status == "live":
        q = q.where(ReversionSignal.status.in_(
            ("CONFIRMED", "ENTRY_ZONE", "ACTIVE", "TP1_HIT", "TP2_HIT")))
    elif status:
        q = q.where(ReversionSignal.status == status)
    if variant:
        q = q.where(ReversionSignal.variant == variant)
    rows = (await db.execute(q)).scalars().all()
    return {"signals": [_sig_row(r) for r in rows],
            "variants": {k: {"label": v["label"], "note": v["note"],
                             "version": REV.VERSIONS[k]}
                         for k, v in REV.VARIANTS.items()},
            "score_bands": REV.SCORE_BANDS}


@router.get("/reversion/signals/{signal_uid}")
async def reversion_signal(signal_uid: str,
                           db: AsyncSession = Depends(get_session)):
    r = (await db.execute(select(ReversionSignal).where(
        ReversionSignal.signal_uid == signal_uid))).scalars().first()
    if not r:
        raise HTTPException(404, "signal not found")
    return _sig_row(r)


def _agg(rows: List[ReversionSignal]) -> Dict[str, Any]:
    res = [r for r in rows if r.win_loss in ("WIN", "LOSS", "BREAKEVEN", "AMBIGUOUS")]
    n = len(res)
    if not n:
        return {"signals": len(rows), "resolved": 0,
                "sample": "INSUFFICIENT DATA",
                "note": "no resolved trades yet"}
    wins = [r for r in res if r.win_loss == "WIN"]
    losses = [r for r in res if r.win_loss == "LOSS"]
    rs = [r.r_multiple or 0 for r in res]
    aw = statistics.mean([r.net_return_pct or 0 for r in wins]) if wins else 0.0
    al = abs(statistics.mean([r.net_return_pct or 0 for r in losses])) if losses else 0.0
    wr = len(wins) / n
    gw = sum(r.net_return_pct or 0 for r in wins)
    gl = abs(sum(r.net_return_pct or 0 for r in losses))
    return {
        "signals": len(rows), "resolved": n, "wins": len(wins),
        "losses": len(losses),
        "ambiguous": sum(1 for r in res if r.win_loss == "AMBIGUOUS"),
        "breakeven": sum(1 for r in res if r.win_loss == "BREAKEVEN"),
        "win_rate": round(wr * 100, 2),
        "avg_win_pct": round(aw, 3), "avg_loss_pct": round(al, 3),
        "expectancy_pct": round(wr * aw - (1 - wr) * al, 4),
        "expectancy_r": round(statistics.mean(rs), 4) if rs else 0.0,
        "profit_factor": round(gw / gl, 3) if gl > 0 else None,
        "avg_mfe_r": round(statistics.mean([r.mfe_r or 0 for r in res]), 3),
        "avg_mae_r": round(statistics.mean([r.mae_r or 0 for r in res]), 3),
        "sample": ("INSUFFICIENT DATA" if n < 30 else "EARLY" if n < 100
                   else "MODERATE SAMPLE" if n < 500 else "STRONGER SAMPLE"),
    }


def _group(rows: List[ReversionSignal], attr: str) -> Dict[str, Any]:
    g: Dict[Any, List[ReversionSignal]] = {}
    for r in rows:
        g.setdefault(getattr(r, attr, None) or "unknown", []).append(r)
    return {str(k): _agg(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


@router.get("/reversion/performance")
async def reversion_performance(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(ReversionSignal))).scalars().all()
    paper = [r for r in rows if r.cohort == "paper"]
    bt = _load("optimize_result.json")
    variants = _load("variants.json")
    return {
        "paper": {
            "overall": _agg(paper),
            "by_variant": _group(paper, "variant"),
            "by_asset_class": _group(paper, "asset_class"),
            "by_timeframe": _group(paper, "timeframe"),
            "by_symbol": _group(paper, "symbol"),
            "by_regime": _group(paper, "market_regime"),
            "by_score_band": _group(paper, "score_band"),
            "by_session": _group(paper, "session_bucket"),
            "by_direction": _group(paper, "direction"),
        },
        "backtest": bt, "variant_study": variants,
        "separation_note": ("Backtest, paper and live-observed results are stored "
                            "and reported separately and are never blended into a "
                            "single figure."),
    }


@router.get("/reversion/config")
async def reversion_config(db: AsyncSession = Depends(get_session)):
    s = await get_settings(db)
    cfg = s.get("model_settings") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except (ValueError, TypeError):
            cfg = {}
    return {"current": cfg.get("extreme_reversion") or {"variant": "adaptive"},
            "defaults": REV.DEFAULTS,
            "variants": REV.VARIANTS,
            "versions": REV.VERSIONS,
            "test_ranges": {
                "bb_length": [15, 20, 25, 30],
                "bb_dev": [2.0, 2.25, 2.5, 2.75, 3.0, 3.25],
                "rsi_length": [7, 9, 14, 21],
                "rsi_oversold": [5, 10, 15, 20, 25],
                "rsi_overbought": [75, 80, 85, 90, 95],
            },
            "score_weights": REV.SCORE_WEIGHTS}


# ---------------------------------------------------------- system health ---

def _status_of(hb: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """A strategy is only LIVE if it actually ran recently. Stale heartbeats
    render OFFLINE rather than leaving old numbers looking current."""
    if not hb:
        return "UNKNOWN"
    if hb.get("status") == "DISABLED":
        return "DISABLED"
    seen = hb.get("last_seen_at")
    if seen:
        try:
            age = (now_utc() - datetime.fromisoformat(seen)).total_seconds()
        except (TypeError, ValueError):
            age = None
        if age is not None and age > STALE_HEARTBEAT_S:
            return "OFFLINE"
    if hb.get("status") == "ERROR":
        return "ERROR"
    if hb.get("errors") and not hb.get("symbols_with_data"):
        return "ERROR"
    if hb.get("status") in ("NO_DATA",):
        return "NO_DATA"
    if hb.get("status") == "WAITING":
        return "WAITING"
    if hb.get("status") == "PAPER_LIVE":
        return "PAPER LIVE"
    return "LIVE" if hb.get("symbols_with_data") else "WAITING"


@router.get("/health/strategies")
async def strategy_health(request: Request,
                          db: AsyncSession = Depends(get_session)):
    shared = request.app.state.shared
    sched = shared.get("scheduler")
    live = dict(getattr(sched, "model_health", {}) or {})
    persisted = {h.strategy_id: h for h in
                 (await db.execute(select(StrategyHeartbeat))).scalars().all()}
    accounts = {a.model_id: a for a in
                (await db.execute(select(PaperAccount))).scalars().all()}
    from .api import LEDGER_ALIAS, SCALPER_PROFILES
    from ..models import BuySignal
    from ..util.timeutil import now_et
    # Signals-today from the database, not the worker's memory: the in-memory
    # counter reset to zero on every restart, so each deploy made every finder
    # look idle for the rest of the session.
    today = str(now_et().date())
    sig_rows = (await db.execute(
        select(BuySignal.profile, func.count(BuySignal.id))
        .where(BuySignal.session_date == today, BuySignal.is_demo == False,  # noqa: E712
               BuySignal.lifecycle == "ACTIONABLE_BUY")
        .group_by(BuySignal.profile))).all()
    sig_today = {p: n for p, n in sig_rows}
    scalper_today = sum(n for p, n in sig_today.items() if p in SCALPER_PROFILES)

    rows = []
    for mid, meta in MODELS.items():
        hb = live.get(mid) or {}
        p = persisted.get(mid)
        if not hb and p:
            hb = {"status": p.status, "last_seen_at":
                  p.last_heartbeat_at.isoformat() if p.last_heartbeat_at else None,
                  "last_scan_at": p.last_scan_at.isoformat() if p.last_scan_at else None,
                  "symbols_scanned": p.symbols_scanned,
                  "symbols_with_data": p.symbols_with_data,
                  "signals_today": p.signals_today, "errors": p.errors_today,
                  "skip_reason": p.skip_reason, **(p.detail or {})}
        acc = accounts.get(mid) or accounts.get(LEDGER_ALIAS.get(mid, ""))
        rows.append({
            "id": mid, "name": meta["name"], "engine": meta["engine"],
            "cadence": meta.get("cadence"), "color": meta.get("color"),
            "asset_classes": meta.get("asset_classes"),
            "risk_model": RISK_MODELS.get(mid, "standard"),
            "own_worker": bool(meta.get("own_worker")),
            "status": _status_of(hb, meta),
            "last_scan_at": hb.get("last_scan_at"),
            "last_seen_at": hb.get("last_seen_at"),
            "symbols_scanned": hb.get("symbols_scanned", 0),
            "symbols_with_data": hb.get("symbols_with_data", 0),
            "signals_today": (scalper_today if mid == "premarket_scalper"
                              else sig_today.get(mid, 0)),
            "errors": hb.get("errors", 0),
            "skip_reason": hb.get("skip_reason"),
            "universe": hb.get("universe"),
            # Gate counters, so a strategy that scanned and refused everything
            # can be read from the dashboard instead of from the database.
            "gates": {k: hb[k] for k in
                      ("window_rejects", "cap_rejects", "item_rejects",
                       "quote_rejects", "spread_rejects", "rejected_this_pass")
                      if hb.get(k)} or None,
            "feed": hb.get("feed"),
            "equity": acc.equity if acc else None,
            "trades_closed": acc.trades_closed if acc else 0,
        })
    state = getattr(sched, "state", {}) or {}
    # Expose the worker's live insider cache so an empty universe can be read
    # directly instead of inferred from logs.
    mctx = getattr(sched, "mctx", None)
    ins = getattr(mctx, "_insiders", (0.0, {})) if mctx else (0.0, {})
    import time as _t
    insider_cache = {"clusters": len(ins[1] or {}), "head": list(ins[1] or {})[:6],
                     "age_s": round(_t.monotonic() - ins[0]) if ins[0] else None}
    for r in rows:
        if r["id"] == "insider_cluster":
            r["insider_cache"] = insider_cache
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "strategies": sorted(rows, key=lambda r: (r["status"] != "LIVE",
                                                  r["status"] != "PAPER LIVE",
                                                  r["name"])),
        "counts": counts,
        "scheduler": {
            "phase": state.get("phase"), "cycles": state.get("cycles"),
            "last_cycle_at": state.get("last_cycle_at"),
            "last_cycle_ok": state.get("last_cycle_ok", True),
            "last_error": state.get("last_error"),
            "next_run_at": state.get("next_run_at"),
        },
        "regime": getattr(sched, "last_regime", None),
        "stale_after_seconds": STALE_HEARTBEAT_S,
        "legend": {
            "LIVE": "scanning and able to emit signals now",
            "PAPER LIVE": "scanning and recording paper signals; no real orders",
            "WAITING": "healthy but outside its cadence or regime window",
            "NO_DATA": "ran, but no series returned usable bars",
            "OFFLINE": "no heartbeat inside the staleness window",
            "DISABLED": "switched off in settings",
            "ERROR": "last pass raised",
        },
    }


@router.get("/accuracy")
async def accuracy_dashboard(db: AsyncSession = Depends(get_session)):
    """Platform-wide research table. Never ranked by win rate alone."""
    rev = (await db.execute(select(ReversionSignal))).scalars().all()
    accounts = {a.model_id: a for a in
                (await db.execute(select(PaperAccount))).scalars().all()}
    from .api import LEDGER_ALIAS

    bt = _load("optimize_result.json") or {}
    chosen = (bt.get("chosen") or {})
    rows = []
    for mid, meta in MODELS.items():
        acc = accounts.get(mid) or accounts.get(LEDGER_ALIAS.get(mid, ""))
        row = {"id": mid, "name": meta["name"], "color": meta.get("color"),
               "version": None, "risk_model": RISK_MODELS.get(mid, "standard"),
               "paper_trades": acc.trades_closed if acc else 0,
               "paper_win_rate": (round(100 * acc.wins / acc.trades_closed, 2)
                                  if acc and acc.trades_closed else None),
               "equity": acc.equity if acc else None,
               "max_drawdown_pct": acc.max_drawdown_pct if acc else None,
               "backtest_win_rate": None, "oos_win_rate": None,
               "expectancy_r": None, "profit_factor": None, "avg_r": None,
               "sample": "INSUFFICIENT DATA"}
        if mid == "extreme_reversion":
            paper = _agg([r for r in rev if r.cohort == "paper"])
            row.update({
                "version": REV.VERSIONS.get(
                    (chosen.get("entry") or {}).get("variant", "adaptive"), "1.2.0"),
                "paper_trades": paper.get("resolved", 0),
                "paper_win_rate": paper.get("win_rate"),
                "expectancy_r": paper.get("expectancy_r"),
                "profit_factor": paper.get("profit_factor"),
                "sample": paper.get("sample", "INSUFFICIENT DATA"),
                "backtest_win_rate": (chosen.get("train") or {}).get("win_rate"),
                "oos_win_rate": (chosen.get("test") or {}).get("win_rate"),
                "oos_expectancy_r": (chosen.get("test") or {}).get("expectancy_r"),
                "oos_sample": (chosen.get("test") or {}).get("sample"),
            })
        rows.append(row)
    return {"rows": rows,
            "sortable": ["expectancy_r", "paper_win_rate", "oos_win_rate",
                         "max_drawdown_pct", "paper_trades"],
            "note": ("Backtest, out-of-sample and paper columns are separate "
                     "measurements and are never combined. Ranking by win rate "
                     "alone is deliberately not offered as the default.")}


# ------------------------------------------------- datasets & change log ----

@router.get("/strategies/{strategy_id}/datasets")
async def datasets(strategy_id: str, db: AsyncSession = Depends(get_session)):
    runs = (await db.execute(select(DatasetRun).where(
        DatasetRun.strategy_id == strategy_id)
        .order_by(desc(DatasetRun.run_number)))).scalars().all()
    changes = (await db.execute(select(StrategyChangeLog).where(
        StrategyChangeLog.strategy_id == strategy_id)
        .order_by(desc(StrategyChangeLog.id)).limit(50))).scalars().all()
    cur = next((r for r in runs if not r.archived), None)
    return {
        "current_run": cur.run_number if cur else 1,
        "last_reset": cur.started_at.isoformat() if cur else None,
        "runs": [{"run_number": r.run_number, "scope": r.scope,
                  "label": r.label, "reason": r.reason,
                  "archived": r.archived,
                  "started_at": r.started_at.isoformat(),
                  "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                  "stats": r.stats_snapshot} for r in runs],
        "change_log": [{"version": c.version,
                        "changed_at": c.changed_at.isoformat(),
                        "changes": c.changes, "reason": c.reason,
                        "old": c.old_parameters, "new": c.new_parameters,
                        "dataset_action": c.dataset_action} for c in changes],
    }


@router.post("/strategies/{strategy_id}/changelog")
async def add_change_log(strategy_id: str, payload: Dict[str, Any] = Body(...),
                         db: AsyncSession = Depends(get_session)):
    """Record a parameter or logic change against a strategy.

    Every material change should leave a trace with its reasoning, so a shift in
    performance can later be attributed to the change that caused it rather than
    to noise.
    """
    if not payload.get("changes"):
        raise HTTPException(400, "changes is required")
    action = payload.get("dataset_action", "continue")
    if action not in ("continue", "new_dataset"):
        raise HTTPException(400, "dataset_action must be continue|new_dataset")
    row = StrategyChangeLog(
        strategy_id=strategy_id,
        version=str(payload.get("version") or "")[:16],
        changes=str(payload["changes"]),
        reason=str(payload.get("reason") or ""),
        old_parameters=payload.get("old_parameters") or {},
        new_parameters=payload.get("new_parameters") or {},
        dataset_action=action,
        actor=str(payload.get("actor") or "admin")[:48],
    )
    db.add(row)
    await db.commit()
    return {"ok": True, "id": row.id, "changed_at": row.changed_at.isoformat(),
            "dataset_action": action}


@router.post("/strategies/{strategy_id}/reset")
async def reset_dataset(strategy_id: str, payload: Dict[str, Any] = Body(...),
                        db: AsyncSession = Depends(get_session)):
    """Archive the current dataset and start a fresh one.

    The default is ARCHIVE, not delete: current statistics reset while the
    research survives. Permanent deletion is a separate, explicit second step.
    """
    if payload.get("confirm") != "RESET":
        raise HTTPException(400, 'type RESET to confirm this action')
    scope = payload.get("scope", "paper")
    if scope not in ("paper", "backtest", "live_observed", "all"):
        raise HTTPException(400, "scope must be paper|backtest|live_observed|all")
    permanent = bool(payload.get("permanent"))
    if permanent and payload.get("confirm_permanent") != "DELETE FOREVER":
        raise HTTPException(400, 'permanent deletion needs confirm_permanent="DELETE FOREVER"')

    runs = (await db.execute(select(DatasetRun).where(
        DatasetRun.strategy_id == strategy_id,
        DatasetRun.scope == scope))).scalars().all()
    cur = next((r for r in runs if not r.archived), None)
    nxt = (max([r.run_number for r in runs]) + 1) if runs else 2

    snapshot = {}
    archived_rows = 0
    if strategy_id == "extreme_reversion" and scope in ("paper", "all"):
        rows = (await db.execute(select(ReversionSignal).where(
            ReversionSignal.cohort == "paper",
            ReversionSignal.dataset_run == (cur.run_number if cur else 1)
        ))).scalars().all()
        snapshot = _agg(rows)
        archived_rows = len(rows)
        if permanent:
            for r in rows:
                await db.delete(r)
        # otherwise rows keep their dataset_run tag and simply fall out of
        # the current view — archived, not destroyed

    if cur:
        cur.archived = True
        cur.ended_at = now_utc()
        cur.stats_snapshot = snapshot
    else:
        db.add(DatasetRun(strategy_id=strategy_id, run_number=1, scope=scope,
                          label="Run #1", archived=True, ended_at=now_utc(),
                          stats_snapshot=snapshot))
    db.add(DatasetRun(strategy_id=strategy_id, run_number=nxt, scope=scope,
                      label=payload.get("label") or f"Run #{nxt}",
                      reason=payload.get("reason", ""), started_at=now_utc()))
    db.add(StrategyChangeLog(strategy_id=strategy_id, version="",
                             changes=f"dataset reset ({scope})",
                             reason=payload.get("reason", ""),
                             dataset_action="new_dataset",
                             actor=payload.get("actor", "admin")))
    await db.commit()
    return {"ok": True, "scope": scope, "new_run": nxt,
            "archived_rows": archived_rows,
            "permanently_deleted": permanent,
            "message": (f"Archived {archived_rows} record(s) into run "
                        f"#{cur.run_number if cur else 1} and started run #{nxt}."
                        if not permanent else
                        f"Permanently deleted {archived_rows} record(s).")}


@router.get("/risk/portfolio")
async def risk_portfolio(db: AsyncSession = Depends(get_session)):
    cfg = await _risk_settings(db)
    rows = (await db.execute(select(PaperPosition).where(
        PaperPosition.status == "open"))).scalars().all()
    pos = []
    for p in rows:
        risk_unit = abs((p.entry_fill or 0) - (p.stop or 0))
        qty = (p.size_usd / p.entry_fill) if p.entry_fill else 0
        pos.append({"symbol": p.symbol, "direction": "long",
                    "profile": p.profile,
                    "open_risk_dollars": round(risk_unit * qty
                                               * (p.remaining_frac or 1), 2),
                    "correlation_group": correlation_group(p.symbol)})
    accs = (await db.execute(select(PaperAccount))).scalars().all()
    # Every strategy runs its own $10k ledger, so summing their risk against one
    # $10k account read as "26% of a 3% ceiling" when no single account was over.
    eq_by_profile = {a.model_id: float(a.equity or 0) for a in accs}
    fleet_equity = sum(eq_by_profile.values()) or float(cfg["account_equity"])
    pf = portfolio_risk(pos, fleet_equity)
    pf["equity_basis"] = round(fleet_equity, 2)
    pf["accounts"] = len(eq_by_profile) or 1

    ceiling = float(cfg["max_total_open_risk_pct"])
    by_account = []
    for prof, eq in sorted(eq_by_profile.items()):
        risk = round(sum(p["open_risk_dollars"] for p in pos if p["profile"] == prof), 2)
        if not risk:
            continue
        by_account.append({"profile": prof, "open_risk": risk, "equity": round(eq, 2),
                           "open_risk_pct": round(risk / eq * 100, 3) if eq else None,
                           "positions": sum(1 for p in pos if p["profile"] == prof)})
    by_account.sort(key=lambda r: r["open_risk_pct"] or 0, reverse=True)
    worst = by_account[0] if by_account else None
    # Each ledger is capped separately, so the binding constraint is the account
    # closest to its own ceiling — not the fleet total.
    headroom = round(ceiling - (worst["open_risk_pct"] if worst else 0.0), 3)

    dd = max((a.max_drawdown_pct or 0) for a in accs) if accs else 0.0
    breaker = circuit_breaker_state(cfg, drawdown_pct=dd)
    return {"portfolio": pf, "positions": pos,
            "by_account": by_account, "worst_account": worst,
            "limits": {
                "max_total_open_risk_pct": cfg["max_total_open_risk_pct"],
                "max_correlated_risk_pct": cfg["max_correlated_risk_pct"],
                "max_sector_risk_pct": cfg["max_sector_risk_pct"],
                "daily_loss_limit_pct": cfg["daily_loss_limit_pct"],
                "weekly_loss_limit_pct": cfg["weekly_loss_limit_pct"]},
            "circuit_breaker": breaker,
            "headroom_pct": headroom,
            "headroom_basis": ("worst single strategy account"
                               if worst else "no open paper trades")}
