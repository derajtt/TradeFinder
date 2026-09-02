#!/usr/bin/env python
"""Void duplicate paper positions created by restart-triggered rebalances.

A model held the same symbol twice because the daily/weekly/monthly cadence
guards lived only in memory: every deploy reset them and the next pass re-ran
the rebalance on a new session date, clearing the per-day fingerprint.

Keeps the FIRST position in each (profile, symbol) group, voids the later ones,
and refunds their committed cash. Every void appends an audit event — nothing is
deleted, so the record of what happened survives.

Dry run by default; pass --apply to write.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BuySignal, PaperPosition
from app.strategy.platform import get_account
from app.util.timeutil import now_utc

APPLY = "--apply" in sys.argv


async def main():
    async with SessionLocal() as db:
        rows = (await db.execute(select(PaperPosition).where(
            PaperPosition.status == "open").order_by(PaperPosition.id))).scalars().all()
        groups = {}
        for p in rows:
            groups.setdefault((p.profile, p.symbol), []).append(p)

        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        if not dupes:
            print("no duplicate open positions found")
            return
        print(f"{'APPLYING' if APPLY else 'DRY RUN'} — "
              f"{len(dupes)} duplicated (profile, symbol) group(s)\n")

        refunds = {}
        for (profile, symbol), plist in sorted(dupes.items()):
            keep, drop = plist[0], plist[1:]
            print(f"{profile}/{symbol}: keeping #{keep.id} "
                  f"(opened {keep.opened_at:%m-%d %H:%M}, ${keep.size_usd:,.2f})")
            for p in drop:
                print(f"    voiding #{p.id} opened {p.opened_at:%m-%d %H:%M} "
                      f"${p.size_usd:,.2f}")
                refunds[profile] = refunds.get(profile, 0.0) + p.size_usd
                if APPLY:
                    ev = list(p.events or [])
                    ev.append({"t": now_utc().isoformat(), "e": "voided_duplicate",
                               "detail": "duplicate of position "
                                         f"#{keep.id}; created by a restart-"
                                         "triggered rebalance. Cash refunded, "
                                         "not counted as a trade.",
                               "refund": round(p.size_usd, 2)})
                    p.events = ev
                    p.status = "voided"
                    p.exit_reason = "voided_duplicate"
                    p.closed_at = now_utc()
                    p.remaining_frac = 0.0
                    sig = await db.get(BuySignal, p.signal_id)
                    if sig:
                        sig.lifecycle = "INVALIDATED"
                        sig.status = "invalidated"

        print("\nledger refunds:")
        for profile, amt in sorted(refunds.items()):
            acc = await get_account(db, profile)
            print(f"  {profile}: cash ${acc.cash:,.2f} -> ${acc.cash + amt:,.2f} "
                  f"(+${amt:,.2f})")
            if APPLY:
                acc.cash = round(acc.cash + amt, 2)

        if APPLY:
            await db.commit()
            print("\ncommitted. Voided positions are retained with an audit event; "
                  "they are not counted as trades and never scored as wins or losses.")
        else:
            print("\ndry run — pass --apply to write")


if __name__ == "__main__":
    asyncio.run(main())
