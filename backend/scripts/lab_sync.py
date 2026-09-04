"""Move Quant Lab results between databases.

The backtest runs on the Mac (where the bar cache lives); the dashboard reads
the droplet's Postgres.  Export here, import there — no re-computation, and no
FMP calls from the droplet while the live scanner is working.

  local :  python scripts/lab_sync.py export data/lab_sync.json
  droplet: python scripts/lab_sync.py import /tmp/lab_sync.json

Import is idempotent: lab_strategies upsert on strategy_id; lab_runs and
lab_trades for a strategy are replaced wholesale so a re-import never doubles
the trade history.
"""
import asyncio
import json
import sys
from datetime import datetime

sys.path.insert(0, ".")

from sqlalchemy import delete, select                    # noqa: E402
from app.db import SessionLocal                          # noqa: E402
from app.models import LabRun, LabStrategy, LabTrade     # noqa: E402

TABLES = (LabStrategy, LabRun, LabTrade)


def _cols(model):
    return [c.name for c in model.__table__.columns if c.name != "id"]


def _enc(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _dec(model, name, v):
    if v is None:
        return None
    col = model.__table__.columns[name]
    if str(col.type).startswith("DATETIME") or col.type.__class__.__name__ == "DateTime":
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None
    return v


async def do_export(path):
    out = {}
    async with SessionLocal() as db:
        for model in TABLES:
            cols = _cols(model)
            rows = (await db.execute(select(model))).scalars().all()
            out[model.__tablename__] = [{c: _enc(getattr(r, c)) for c in cols} for r in rows]
    with open(path, "w") as fh:
        json.dump(out, fh)
    print("exported " + ", ".join(f"{k}={len(v)}" for k, v in out.items()) + f" -> {path}")


async def do_import(path):
    with open(path) as fh:
        data = json.load(fh)
    strat_ids = sorted({r["strategy_id"] for r in data.get("lab_strategies", [])})
    async with SessionLocal() as db:
        # replace runs/trades for the strategies present in the dump
        for model in (LabRun, LabTrade):
            if strat_ids:
                await db.execute(delete(model).where(model.strategy_id.in_(strat_ids)))
        existing = {s.strategy_id: s for s in (await db.execute(select(LabStrategy))).scalars().all()}
        for row in data.get("lab_strategies", []):
            obj = existing.get(row["strategy_id"]) or LabStrategy(strategy_id=row["strategy_id"])
            for c in _cols(LabStrategy):
                if c in row:
                    setattr(obj, c, _dec(LabStrategy, c, row[c]))
            if obj not in existing.values():
                db.add(obj)
        for model in (LabRun, LabTrade):
            cols = _cols(model)
            for row in data.get(model.__tablename__, []):
                db.add(model(**{c: _dec(model, c, row.get(c)) for c in cols}))
        await db.commit()
    print("imported " + ", ".join(f"{k}={len(v)}" for k, v in data.items()))


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("export", "import"):
        print(__doc__)
        raise SystemExit(2)
    fn = do_export if sys.argv[1] == "export" else do_import
    asyncio.run(fn(sys.argv[2]))


if __name__ == "__main__":
    main()
