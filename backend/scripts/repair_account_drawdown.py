"""Recompute every paper account's max drawdown from its own equity history.

`max_drawdown_pct` is a running minimum, so a single bogus equity mark — the
double-settlement and phantom-cash bugs both produced one — is remembered
forever.  Technical Confluence read -50.00% while its equity had never been
below $9,096 of a $10,000 peak.

The reference number is the deepest peak-to-trough dip in the recorded equity
snapshots (plus the current mark).  Snapshots are taken about every ten
minutes, so that reference UNDERSTATES a real drawdown slightly — a value only
a little deeper than the reference is therefore believable and is left alone.
Only a stored value the history cannot explain (deeper by --threshold points or
more) is treated as a fossil and repaired.  Accounts with no history are left
alone and reported, never silently zeroed.

    python scripts/repair_account_drawdown.py --dry-run
    python scripts/repair_account_drawdown.py --apply
"""
import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select                             # noqa: E402
from app.db import SessionLocal                           # noqa: E402
from app.models import EquitySnapshot, PaperAccount       # noqa: E402


def deepest_dip(series):
    """Worst peak-to-trough percentage over an equity series (<= 0.0)."""
    peak = None
    worst = 0.0
    for eq in series:
        if eq is None or eq <= 0:
            continue
        peak = eq if peak is None else max(peak, eq)
        if peak > 0:
            worst = min(worst, (eq - peak) / peak * 100.0)
    return round(worst, 2)


async def restore(pairs):
    """Write back specific recorded drawdowns.  Used to undo an over-eager
    repair run: 10-minute sampling cannot disprove a deeper recorded dip, so the
    deeper value is the honest one to keep."""
    async with SessionLocal() as db:
        accounts = {a.model_id: a for a in (await db.execute(select(PaperAccount))).scalars().all()}
        for mid, dd in pairs:
            acc = accounts.get(mid)
            if acc is None:
                print(f"{mid:24} missing"); continue
            print(f"{mid:24} {acc.max_drawdown_pct!s:>8} -> {dd!s:>8}")
            acc.max_drawdown_pct = dd
        await db.commit()
    print(f"restored {len(pairs)} account(s)")


async def main(apply_changes, threshold):
    async with SessionLocal() as db:
        accounts = (await db.execute(select(PaperAccount))).scalars().all()
        changed, skipped = [], []
        for acc in accounts:
            snaps = (await db.execute(
                select(EquitySnapshot.equity)
                .where(EquitySnapshot.model_id == acc.model_id)
                .order_by(EquitySnapshot.ts_utc))).scalars().all()
            if not snaps:
                skipped.append((acc.model_id, acc.max_drawdown_pct, "no equity history"))
                continue
            series = list(snaps) + [acc.equity]
            ref = deepest_dip(series)
            # The account is provably at least this far below its own peak right
            # now, whatever the sampled history missed.
            if acc.max_equity and acc.max_equity > 0:
                ref = min(ref, round((acc.equity - acc.max_equity) / acc.max_equity * 100, 2))
            stored = acc.max_drawdown_pct or 0.0
            if len(set(round(float(x), 2) for x in snaps)) <= 1:
                # A flat series means the account was never really marked, so it
                # is no evidence at all — refuse to overwrite a drawdown with 0.
                skipped.append((acc.model_id, stored,
                                "equity history is flat — no evidence either way"))
                continue
            if ref - stored >= threshold:          # stored is deeper than history allows
                changed.append((acc.model_id, stored, ref, len(snaps)))
                if apply_changes:
                    acc.max_drawdown_pct = ref
            else:
                skipped.append((acc.model_id, stored,
                                f"within sampling error of {ref}%"))
        if apply_changes:
            await db.commit()
    for mid, was, now, n in changed:
        print(f"{mid:24} {was!s:>8} -> {now!s:>8}  ({n} snapshots)")
    for mid, was, why in skipped:
        print(f"{mid:24} {was!s:>8} kept      ({why})")
    print(("applied " if apply_changes else "would change ") + f"{len(changed)} account(s); "
          f"{len(skipped)} left alone (threshold {threshold} points)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="points by which a stored drawdown must exceed the "
                         "history before it is treated as a fossil")
    ap.add_argument("--restore", default="",
                    help="comma list model_id=drawdown_pct, writing recorded "
                         "values back after an over-eager repair")
    a = ap.parse_args()
    if a.restore:
        pairs = [(kv.split("=")[0], float(kv.split("=")[1]))
                 for kv in a.restore.split(",") if "=" in kv]
        asyncio.run(restore(pairs))
    else:
        asyncio.run(main(a.apply, a.threshold))
