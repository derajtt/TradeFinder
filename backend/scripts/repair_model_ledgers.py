"""One-time repair: void model positions wrongly closed by the scalper exit
engine (pre-fix) and refund their ledgers. Append-only audit preserved."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import select
from app.db import SessionLocal
from app.models import PaperAccount, PaperPosition, SignalEvent

SCALPER = ("primary", "accuracy", "aggressive", "penny")

async def main():
    async with SessionLocal() as db:
        pos = (await db.execute(select(PaperPosition).where(
            PaperPosition.status == "closed",
            PaperPosition.exit_reason == "hard_time_exit",
            ~PaperPosition.profile.in_(SCALPER)))).scalars().all()
        for p in pos:
            acc = (await db.execute(select(PaperAccount).where(
                PaperAccount.model_id == p.profile))).scalar_one_or_none()
            if acc:
                acc.cash = round(acc.cash + p.size_usd, 2)
                acc.equity = acc.cash
            p.status = "invalidated"
            p.exit_reason = "voided:wrong_exit_engine_bug"
            db.add(SignalEvent(signal_id=p.signal_id, event_type="correction",
                               detail={"reason": "position voided — scalper exit "
                                       "engine wrongly closed a model position "
                                       "before the profile-scope fix; ledger "
                                       "refunded", "refund_usd": p.size_usd}))
            print(f"voided {p.profile} {p.symbol} — refunded ${p.size_usd}")
        await db.commit()
    print("done:", len(pos), "positions repaired")

asyncio.run(main())
