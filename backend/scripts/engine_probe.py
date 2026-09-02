#!/usr/bin/env python
"""Run every model engine against live data and report whether it CAN fire.

A model with no signals might simply be waiting for its setup — or it might be
structurally unable to fire, like the scalper whose BUY threshold sat above any
score the engine had ever produced. This distinguishes the two by exercising
each engine directly and reporting what it saw.
"""
import asyncio, os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.providers.fmp import FmpProvider
from app.strategy import platform as mplat
from app.strategy.engines import ENGINES, REGIME_ALLOW, regime as regime_fn
from app.strategy.registry import CRYPTO_UNIVERSE, ETF_UNIVERSE, MODELS, PAIRS
from app.settings_service import get_settings
from app.db import SessionLocal


async def main():
    fmp = FmpProvider()
    ctx_helper = mplat.ModelContext(fmp)
    async with SessionLocal() as db:
        settings = await get_settings(db)

    stock = list(ETF_UNIVERSE)
    crypto = list(CRYPTO_UNIVERSE)
    pair_legs = sorted({l for p in PAIRS for l in p} - set(stock))
    ctx = {"bars_daily": {}, "bars_5m": {}}
    print(f"loading bars for {len(stock)+len(crypto)+len(pair_legs)} symbols...")
    for s in stock + crypto + pair_legs:
        ctx["bars_daily"][s] = await ctx_helper.daily(s)
    for s in stock + crypto:
        ctx["bars_5m"][s] = await ctx_helper.m5(s)
    ctx["spy_daily"] = ctx["bars_daily"].get("SPY") or []
    ctx["earnings"] = await ctx_helper.earnings_today()
    try:
        ctx["insider_clusters"] = await ctx_helper.insider_clusters()
    except Exception:
        ctx["insider_clusters"] = {}

    reg = regime_fn(ctx)
    print(f"regime: {reg['state']} — {reg.get('why','')}\n")
    daily_ok = sum(1 for v in ctx["bars_daily"].values() if v)
    m5_ok = sum(1 for v in ctx["bars_5m"].values() if v)
    print(f"data: {daily_ok} symbols with daily bars, {m5_ok} with 5-min bars, "
          f"{len(ctx['earnings'])} earnings, "
          f"{len(ctx['insider_clusters'])} insider clusters\n")

    rows = []
    for mid, meta in MODELS.items():
        eng_name = meta["engine"]
        if eng_name == "scalper" or meta.get("own_worker"):
            rows.append((mid, eng_name, "-", "-", "separate worker (not this dispatcher)"))
            continue
        eng = ENGINES.get(eng_name)
        if eng is None:
            rows.append((mid, eng_name, "-", "-", "NO ENGINE IMPLEMENTATION"))
            continue
        allowed = REGIME_ALLOW.get(eng_name, {"trend", "range", "uncertain"})
        gated = reg["state"] not in allowed

        if eng_name == "pairs":
            syms = [f"{a}|{b}" for a, b in PAIRS]
        elif eng_name == "insider":
            syms = [s for s in ctx["insider_clusters"]][:25]
        elif eng_name == "earnings":
            syms = [s for s in ctx["earnings"]][:25]
        elif meta["asset_classes"] == ["crypto"]:
            syms = crypto
        elif "crypto" in meta["asset_classes"]:
            syms = stock + crypto
        else:
            syms = stock

        # load any bars this engine's universe needs but the shared ctx lacks
        for s in syms[:25]:
            base = s.split("|")[0]
            if base not in ctx["bars_daily"]:
                ctx["bars_daily"][base] = await ctx_helper.daily(base)

        fired = evaluated = errors = 0
        err_msg = ""
        for s in syms:
            base = s.split("|")[0]
            if not (ctx["bars_daily"].get(base) or ctx["bars_5m"].get(base)):
                continue
            evaluated += 1
            try:
                v = eng(ctx, s, {})
                if v and v.get("action") == "buy":
                    fired += 1
            except Exception as e:
                errors += 1
                if not err_msg:
                    err_msg = f"{type(e).__name__}: {e}"

        if not syms:
            note = "universe empty right now"
        elif evaluated == 0:
            note = "NO USABLE BARS for its universe"
        elif errors:
            note = f"{errors} ERROR(S): {err_msg[:60]}"
        elif fired:
            note = f"WOULD FIRE on {fired} symbol(s)"
        else:
            note = "evaluated cleanly, no setup right now"
        if gated:
            note += f"  [regime-gated: needs {sorted(allowed)}]"
        rows.append((mid, eng_name, str(evaluated), str(fired), note))

    print(f"{'model':26s} {'engine':12s} {'eval':>5s} {'fire':>5s}  note")
    print("-" * 104)
    for r in rows:
        print(f"{r[0][:26]:26s} {r[1][:12]:12s} {r[2]:>5s} {r[3]:>5s}  {r[4]}")

    broken = [r for r in rows if "NO ENGINE" in r[4] or "NO USABLE" in r[4]
              or "ERROR" in r[4]]
    print(f"\n{len(broken)} model(s) structurally unable to fire right now")
    for r in broken:
        print(f"  - {r[0]}: {r[4]}")
    await fmp.close()


if __name__ == "__main__":
    asyncio.run(main())
