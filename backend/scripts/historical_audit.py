"""Historical-data audit for the backtester: what does the CURRENT FMP plan provide?
Probes representative microcaps across dates. Redacted output; writes a coverage report."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from app.config import get_config

SYMS = ["GPRO", "RDHL", "CVKD", "AEHL", "AAPL"]

async def probe(c, name, url, params):
    try:
        r = await c.get(url, params=params)
        try:
            data = r.json()
        except Exception:
            data = None
        n = len(data) if isinstance(data, list) else (1 if data else 0)
        sample = None
        if isinstance(data, list) and data:
            sample = data[0]
        return {"probe": name, "status": r.status_code, "records": n, "sample": sample}
    except Exception as e:
        return {"probe": name, "status": 0, "error": type(e).__name__}

async def main():
    cfg = get_config()
    base = cfg.fmp_rest_base
    k = {"apikey": cfg.fmp_api_key}
    out = []
    async with httpx.AsyncClient(timeout=25) as c:
        # 1-min candles: today, last week, 1 month back, 6 months back
        for sym, frm, to in [("GPRO", "2026-09-01", "2026-09-01"),
                             ("GPRO", "2026-08-25", "2026-08-26"),
                             ("AAPL", "2026-08-03", "2026-08-04"),
                             ("AAPL", "2026-03-02", "2026-03-03")]:
            out.append(await probe(c, f"1min:{sym}:{frm}", f"{base}/historical-chart/1min",
                                   {**k, "symbol": sym, "from": frm, "to": to, "extended": "true"}))
        # 5-min and 1-hour candles
        out.append(await probe(c, "5min:GPRO:recent", f"{base}/historical-chart/5min",
                               {**k, "symbol": "GPRO", "from": "2026-08-25", "to": "2026-08-26"}))
        out.append(await probe(c, "1hour:GPRO:recent", f"{base}/historical-chart/1hour",
                               {**k, "symbol": "GPRO", "from": "2026-08-01", "to": "2026-08-26"}))
        # EOD daily history depth
        out.append(await probe(c, "eod:GPRO:full", f"{base}/historical-price-eod/full",
                               {**k, "symbol": "GPRO", "from": "2020-01-01", "to": "2026-09-01"}))
        out.append(await probe(c, "eod-light:RDHL", f"{base}/historical-price-eod/light",
                               {**k, "symbol": "RDHL", "from": "2025-01-01", "to": "2026-09-01"}))
        # historical news with timestamps
        out.append(await probe(c, "news:GPRO:aug", f"{base}/news/stock",
                               {**k, "symbols": "GPRO", "from": "2026-08-25", "to": "2026-08-31", "limit": 5}))
        # delisted companies
        out.append(await probe(c, "delisted", f"{base}/delisted-companies", {**k, "limit": 5}))
        # historical market cap / shares
        out.append(await probe(c, "hist-mktcap:GPRO", f"{base}/historical-market-capitalization",
                               {**k, "symbol": "GPRO", "limit": 5}))
        out.append(await probe(c, "shares-float-all:CVKD", f"{base}/historical/shares-float",
                               {**k, "symbol": "CVKD", "limit": 5}))
        # splits + symbol changes
        out.append(await probe(c, "splits:AEHL", f"{base}/splits", {**k, "symbol": "AEHL"}))
        out.append(await probe(c, "symbol-change", f"{base}/symbol-change", {**k, "limit": 5}))
    for r in out:
        s = r.get("sample")
        keys = list(s.keys())[:8] if isinstance(s, dict) else None
        print(f"{r['probe']:26} status={r['status']} n={r.get('records')} keys={keys}")
        if isinstance(s, dict) and 'date' in s:
            print(f"{'':28}first-row date={s.get('date')}")
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "..", "data",
                                      "historical_audit.json"), "w"), default=str, indent=1)

asyncio.run(main())
