#!/usr/bin/env python
"""Void model-fleet closes caused by inverted trade geometry.

Positions whose target sat below the fill closed on the first tick at a loss
labelled 'target2', and a breakeven stop-out measured R against a zero-width
risk. None of these were decisions the model made — they were level bugs. They
are voided with an audit event, cash refunded, and the account's closed/win
counters recomputed from the surviving closed positions.

Dry run by default; --apply to write.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import select
from app.db import SessionLocal
from app.models import PaperAccount, PaperPosition
from app.util.timeutil import now_utc

APPLY = "--apply" in sys.argv


def _bad(p):
    """Geometry that could never have been a real trade."""
    if p.target1 is not None and p.target1 <= p.entry_fill:
        return f"target1 {p.target1} <= fill {p.entry_fill}"
    if p.target2 is not None and p.target2 <= p.entry_fill:
        return f"target2 {p.target2} <= fill {p.entry_fill}"
    if p.realized_r is not None and abs(p.realized_r) > 50:
        return f"realized_r {p.realized_r} from a zero-width risk"
    return None


async def main():
    async with SessionLocal() as db:
        closed = (await db.execute(select(PaperPosition).where(
            PaperPosition.status == "closed"))).scalars().all()
        bad = [(p, _bad(p)) for p in closed]
        bad = [(p, why) for p, why in bad if why]
        if not bad:
            print("no geometry-bug closes found"); return
        print(f"{'APPLYING' if APPLY else 'DRY RUN'} — {len(bad)} bad close(s)\n")
        refund = {}
        for p, why in bad:
            print(f"  #{p.id} {p.profile}/{p.symbol} exit={p.exit_reason} r={p.realized_r}  -> {why}")
            refund[p.profile] = refund.get(p.profile, 0.0) + p.size_usd
            if APPLY:
                ev = list(p.events or [])
                ev.append({"t": now_utc().isoformat(), "e": "voided_geometry_bug",
                           "detail": why, "refund": round(p.size_usd, 2)})
                p.events = ev
                p.status = "voided"
                p.exit_reason = f"voided:geometry_bug"
        print()
        for prof, amt in sorted(refund.items()):
            acc = (await db.execute(select(PaperAccount).where(
                PaperAccount.model_id == prof))).scalars().first()
            if not acc:
                continue
            # the void refunds the ORIGINAL stake; proceeds already credited on
            # close are reversed so cash returns to what it was before the trade
            proceeds = sum(sum(e.get("pnl", 0) for e in (p.events or []))
                           for p, _ in bad if p.profile == prof)
            new_cash = acc.cash - proceeds  # remove booked pnl
            # recompute counters from surviving closed positions
            survivors = [c for c in closed if c.profile == prof and
                         c not in [b for b, _ in bad]]
            n_closed = len(survivors)
            n_wins = sum(1 for c in survivors if (c.realized_r or 0) > 0.05)
            pnl_surv = sum(sum(e.get("pnl", 0) for e in (c.events or [])) for c in survivors)
            print(f"  {prof}: cash {acc.cash:,.2f} -> {new_cash:,.2f} | "
                  f"closed {acc.trades_closed} -> {n_closed} | wins {acc.wins} -> {n_wins}")
            if APPLY:
                acc.cash = round(new_cash, 2)
                acc.realized_pnl = round(pnl_surv, 2)
                acc.trades_closed = n_closed
                acc.wins = n_wins
        if APPLY:
            await db.commit(); print("\ncommitted — voided rows kept with audit events")
        else:
            print("\ndry run — pass --apply to write")

asyncio.run(main())
