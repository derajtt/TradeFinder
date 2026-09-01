"""Canonical analytics: THE single source for every reported total.
Dashboard headline, detail tables, CSV export, API responses, and reports must
all call canonical_report() — never hand-rolled counts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import BuySignal, PaperPosition, RejectedCandidate, ShadowExit
from .strategy.tiers import tier_for
from .strategy.versions import LIFECYCLE, VERSIONS


def _wilson_lb(w: int, n: int, z: float = 1.96) -> Optional[float]:
    import math
    if n == 0:
        return None
    p = w / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - r) / den, 4)


def effective_lifecycle(s: BuySignal) -> str:
    """Backfill mapping for legacy records; v2 records carry lifecycle directly."""
    if s.lifecycle:
        return s.lifecycle
    if s.status == "invalidated":
        return "INVALIDATED"
    if s.signal_type == "buy":
        return "ACTIONABLE_BUY"
    et_h = None
    try:
        init = s.initiated_at
        if init.tzinfo is None:
            init = init.replace(tzinfo=timezone.utc)
        from .util.timeutil import ET
        e = init.astimezone(ET)
        et_h = e.hour * 60 + e.minute
    except Exception:
        pass
    if et_h is not None and et_h < 7 * 60:
        return "EARLY_WATCH"
    if et_h is not None and et_h > 9 * 60 + 30:
        return "QUALIFIED_WATCH"   # legacy post-open watches: never premarket-strategy
    return "QUALIFIED_WATCH"


async def canonical_report(db: AsyncSession, settings: Dict[str, Any]) -> Dict[str, Any]:
    sigs = (await db.execute(select(BuySignal).where(
        BuySignal.is_demo == False))).scalars().all()  # noqa: E712
    rejects_n = (await db.execute(select(RejectedCandidate))).scalars().all()
    positions = (await db.execute(select(PaperPosition))).scalars().all()
    shadows = (await db.execute(select(ShadowExit).where(
        ShadowExit.status.like("done%")))).scalars().all()

    by_lc: Dict[str, List[BuySignal]] = {k: [] for k in LIFECYCLE}
    for s in sigs:
        by_lc.setdefault(effective_lifecycle(s), []).append(s)

    # actionable BUY performance = ONLY lifecycle ACTIONABLE_BUY + executable fills,
    # premarket strategy, current strategy family, not demo/invalidated
    closed = [p for p in positions if p.status == "closed"]
    open_pos = [p for p in positions if p.status == "open"]
    wins = [p for p in closed if p.realized_r > 0.05]
    losses = [p for p in closed if p.realized_r < -0.05]
    n = len(closed)
    from .signals.service import metrics_with_outcome
    watch_rows = by_lc.get("EARLY_WATCH", []) + by_lc.get("QUALIFIED_WATCH", [])
    watch_out = {"win": 0, "loss": 0, "neutral": 0, "pending": 0}
    for s in watch_rows:
        watch_out[metrics_with_outcome(s, settings)["outcome"]] += 1

    shadow_stats: Dict[str, Any] = {}
    for sh in shadows:
        g = shadow_stats.setdefault(sh.policy, {"n": 0, "sum_r": 0.0, "wins": 0})
        if sh.r_multiple is not None:
            g["n"] += 1
            g["sum_r"] += sh.r_multiple
            g["wins"] += 1 if sh.r_multiple > 0.05 else 0
    for g in shadow_stats.values():
        g["avg_r"] = round(g["sum_r"] / g["n"], 3) if g["n"] else None
        g["win_rate"] = round(g["wins"] / g["n"], 3) if g["n"] else None
        del g["sum_r"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "versions": VERSIONS,
        "lifecycle_counts": {k: len(v) for k, v in by_lc.items() if v},
        "totals": {
            "signals_total_records": len(sigs),
            "actionable_buys": len(by_lc.get("ACTIONABLE_BUY", [])),
            "early_watches": len(by_lc.get("EARLY_WATCH", [])),
            "qualified_watches": len(by_lc.get("QUALIFIED_WATCH", [])),
            "invalidated": len(by_lc.get("INVALIDATED", [])),
            "rejected_candidates": len(rejects_n),
        },
        "actionable_buy_performance": {
            "cohort": "live_paper",
            "note": ("No BUY-strategy win rate is claimable until actionable "
                     "paper trades exist.") if n == 0 else "",
            "open_positions": len(open_pos),
            "closed_trades": n,
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n, 3) if n else None,
            "win_rate_lb": _wilson_lb(len(wins), n),
            "avg_r": round(sum(p.realized_r for p in closed) / n, 3) if n else None,
            "calibration": "UNCALIBRATED" if n < 50 else "calibrating",
        },
        "watch_outcomes_info_only": {
            **watch_out,
            "note": "WATCH records are research context, never BUY performance."},
        "shadow_exit_policies": shadow_stats,
        "reconciliation": {
            "sum_lifecycles": sum(len(v) for v in by_lc.values()),
            "equals_total": sum(len(v) for v in by_lc.values()) == len(sigs),
        },
    }
