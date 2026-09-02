"""Live paper-trading engine: primary frozen policy + shadow exit policies.
Operates on tracked quote observations; never touches signal-time immutables."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BuySignal, PaperPosition, ShadowExit, SignalEvent
from ..util.timeutil import ET, now_utc
from .platform import get_account
from .registry import MODELS
from .execution import simulate_sell_price
from .versions import STRATEGY_VERSION

# frozen primary policy parameters (documented; not claimed optimal)
PRIMARY_POLICY = {
    "partial_frac_at_1r": 0.5,
    "time_exits_et": ["09:31", "09:35", "09:45", "10:00"],
    "hard_exit_et": "10:30",
    "size_usd": 1000.0,
}
SHADOW_POLICIES = ["scalp_5m", "scalp_10m", "scalp_15m", "scalp_30m",
                   "target_1R", "target_2R", "exit_pre_open_929",
                   "exit_open_930", "exit_935", "exit_945", "exit_1000",
                   "vwap_loss", "dd8_from_high", "half_1R_hold_open"]


async def open_position(db: AsyncSession, sig: BuySignal,
                        setup: Dict[str, Any]) -> Optional[PaperPosition]:
    if sig.sim_fill_price is None:
        return None
    exists = (await db.execute(select(PaperPosition.id).where(
        PaperPosition.signal_id == sig.id))).first()
    if exists:
        return None
    pos = PaperPosition(signal_id=sig.id, symbol=sig.symbol,
                        strategy_version=STRATEGY_VERSION,
                        entry_fill=sig.sim_fill_price,
                        stop=setup.get("stop"), target1=setup.get("target1"),
                        target2=setup.get("target2"),
                        size_usd=PRIMARY_POLICY["size_usd"],
                        events=[{"t": now_utc().isoformat(), "e": "opened",
                                 "fill": sig.sim_fill_price}])
    pos.profile = getattr(sig, "profile", None) or "primary"
    db.add(pos)
    for pol in SHADOW_POLICIES:
        db.add(ShadowExit(signal_id=sig.id, policy=pol))
    await db.flush()
    # Debit the profile ledger. Without this the scalper had no ledger at all:
    # its equity never moved, so its competition card sat at a permanent
    # $10,000 with zero trades no matter how it actually performed.
    acc = await get_account(db, pos.profile)
    acc.cash = round(acc.cash - pos.size_usd, 2)
    db.add(SignalEvent(signal_id=sig.id, event_type="paper_opened",
                       detail={"fill": sig.sim_fill_price, "stop": pos.stop,
                               "t1": pos.target1, "t2": pos.target2}))
    return pos


def _et_min(dt) -> int:
    e = dt.astimezone(ET)
    return e.hour * 60 + e.minute


async def update_positions(db: AsyncSession, quotes: Dict[str, dict],
                           settings: Dict[str, Any]) -> List[dict]:
    """One tick of the primary policy + shadow exits from live observations.
    quotes: symbol -> {price, bid, ask, provider_ts}."""
    updates = []
    now = now_utc()
    m_now = _et_min(now)
    # Scalper-strategy positions = everything that is NOT a registry model id.
    # strategy.platform.settle_positions owns the registry ids. Defining the two
    # sets as complements means a newly added profile can never be settled by
    # both engines, and can never be silently settled by neither.
    open_pos = (await db.execute(select(PaperPosition).where(
        PaperPosition.status == "open",
        PaperPosition.profile.notin_(list(MODELS.keys()))))).scalars().all()
    for pos in open_pos:
        q = quotes.get(pos.symbol)
        if not q or not q.get("price"):
            continue
        px = q["price"]
        bid = q.get("bid") or px
        sig = await db.get(BuySignal, pos.signal_id)
        ev = list(pos.events or [])
        risk = max(1e-9, pos.entry_fill - (pos.stop or pos.entry_fill * 0.95))

        book = {"credited": 0.0, "pnl": 0.0}

        def sell(price, reason, frac):
            """Each slice is booked at the price that slice filled at."""
            fillp = simulate_sell_price(price, {"slippage_pct":
                                                settings.get("slippage_pct", 0.4)})
            r_piece = (fillp - pos.entry_fill) / risk * frac
            pos.realized_r = round(pos.realized_r + r_piece, 3)
            pos.remaining_frac = round(pos.remaining_frac - frac, 3)
            slice_pnl = (fillp - pos.entry_fill) / pos.entry_fill * pos.size_usd * frac
            book["credited"] += fillp * frac * (pos.size_usd / pos.entry_fill)
            book["pnl"] += slice_pnl
            ev.append({"t": now.isoformat(), "e": reason, "px": fillp,
                       "frac": frac, "r_piece": round(r_piece, 3),
                       "pnl": round(slice_pnl, 2)})
            if pos.remaining_frac <= 0.001:
                pos.status = "closed"
                pos.exit_reason = reason
                pos.closed_at = now
                pos.exit_fill = fillp

        # catalyst-disproven / dilution / data kill-switch handled upstream via
        # signal invalidation; here: stop, 1R partial, 2R, time ladder, hard exit
        if pos.stop and px <= pos.stop and pos.remaining_frac > 0:
            sell(pos.stop, "stop", pos.remaining_frac)
        else:
            t1 = pos.target1
            took_1r = any(e.get("e") == "partial_1R" for e in ev)
            if t1 and px >= t1 and not took_1r and pos.remaining_frac > 0:
                sell(t1, "partial_1R", PRIMARY_POLICY["partial_frac_at_1r"]
                     * pos.remaining_frac)
                pos.stop = pos.entry_fill  # move stop to break-even after partial
                ev.append({"t": now.isoformat(), "e": "stop_to_breakeven"})
            t2 = pos.target2
            if pos.status == "open" and t2 and px >= t2 and pos.remaining_frac > 0:
                sell(t2, "target_2R", pos.remaining_frac)
            if pos.status == "open":
                hard = PRIMARY_POLICY["hard_exit_et"]
                hh, mm = int(hard[:2]), int(hard[3:])
                if m_now >= hh * 60 + mm:
                    sell(bid, "hard_time_exit", pos.remaining_frac)
        pos.events = ev
        if book["credited"] or pos.status == "closed":
            acc = await get_account(db, pos.profile or "primary")
            acc.cash = round(acc.cash + book["credited"], 2)
            acc.realized_pnl = round(acc.realized_pnl + book["pnl"], 2)
            if pos.status == "closed":
                acc.trades_closed += 1
                if pos.realized_r > 0.05:
                    acc.wins += 1
        updates.append({"symbol": pos.symbol, "status": pos.status,
                        "profile": pos.profile,
                        "realized_r": pos.realized_r,
                        "remaining": pos.remaining_frac})

        # shadow exits (fire once each when their condition first met)
        shadows = (await db.execute(select(ShadowExit).where(
            ShadowExit.signal_id == pos.signal_id,
            ShadowExit.status == "pending"))).scalars().all()
        opened = sig.initiated_at if sig else now
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        age_min = (now - opened).total_seconds() / 60.0
        for sh in shadows:
            fire = None
            p = sh.policy
            if p.startswith("scalp_"):
                mins = float(p.split("_")[1].rstrip("m"))
                if age_min >= mins:
                    fire = ("time", bid)
            elif p == "target_1R" and pos.target1 and px >= pos.target1:
                fire = ("t1", pos.target1)
            elif p == "target_2R" and pos.target2 and px >= pos.target2:
                fire = ("t2", pos.target2)
            elif p == "exit_pre_open_929" and m_now >= 569:
                fire = ("pre_open", bid)
            elif p == "exit_open_930" and m_now >= 570:
                fire = ("open", bid)
            elif p == "exit_935" and m_now >= 575:
                fire = ("935", bid)
            elif p == "exit_945" and m_now >= 585:
                fire = ("945", bid)
            elif p == "exit_1000" and m_now >= 600:
                fire = ("1000", bid)
            elif p == "vwap_loss":
                pass  # needs vwap stream; evaluated in nightly research replay
            elif p == "dd8_from_high":
                hi = sig.since_signal_high if sig else None
                if hi and px <= hi * 0.92 and hi > pos.entry_fill:
                    fire = ("dd8", px)
            elif p == "half_1R_hold_open":
                if m_now >= 575:
                    fire = ("935_after_partial", bid)
            if fire:
                fillp = simulate_sell_price(fire[1], {"slippage_pct":
                                                      settings.get("slippage_pct", 0.4)})
                sh.exit_price = fillp
                sh.exited_at = now
                sh.pct = round((fillp - pos.entry_fill) / pos.entry_fill * 100, 3)
                sh.r_multiple = round((fillp - pos.entry_fill) / risk, 3)
                sh.status = "done"
        # stops for shadow policies: if stop is hit first, close all pending at stop
        if pos.stop and px <= (pos.stop or 0):
            for sh in shadows:
                if sh.status == "pending":
                    fillp = simulate_sell_price(pos.stop, {"slippage_pct":
                                                settings.get("slippage_pct", 0.4)})
                    sh.exit_price = fillp
                    sh.exited_at = now
                    sh.pct = round((fillp - pos.entry_fill) / pos.entry_fill * 100, 3)
                    sh.r_multiple = round((fillp - pos.entry_fill) / risk, 3)
                    sh.status = "done_stop"
    await db.flush()
    return updates
