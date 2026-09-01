"""Live FMP/SEC/OpenAI endpoint diagnostics. Output is redacted — never prints keys."""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx

from app.config import get_config


async def probe(client, name, url, params):
    t0 = time.monotonic()
    try:
        r = await client.get(url, params=params)
        lat = int((time.monotonic() - t0) * 1000)
        n = None
        sample = None
        try:
            data = r.json()
            n = len(data) if isinstance(data, list) else 1
            if isinstance(data, list) and data:
                sample = list(data[0].keys())[:12]
            elif isinstance(data, dict):
                sample = list(data.keys())[:12]
        except Exception:
            pass
        return {"endpoint": name, "status": r.status_code, "latency_ms": lat,
                "records": n, "fields": sample}
    except Exception as e:
        return {"endpoint": name, "status": 0, "error": type(e).__name__}


async def main():
    cfg = get_config()
    base = cfg.fmp_rest_base
    k = {"apikey": cfg.fmp_api_key}
    async with httpx.AsyncClient(timeout=20) as c:
        tests = [
            ("biggest-gainers", f"{base}/biggest-gainers", k),
            ("most-actives", f"{base}/most-actives", k),
            ("batch-exchange-quote:NASDAQ", f"{base}/batch-exchange-quote", {**k, "exchange": "NASDAQ", "short": "true"}),
            ("quote:AAPL", f"{base}/quote", {**k, "symbol": "AAPL"}),
            ("aftermarket-quote:AAPL", f"{base}/aftermarket-quote", {**k, "symbol": "AAPL"}),
            ("historical-chart/1min:AAPL", f"{base}/historical-chart/1min", {**k, "symbol": "AAPL", "extended": "true"}),
            ("news/stock-latest", f"{base}/news/stock-latest", {**k, "limit": 5}),
            ("news/press-releases-latest", f"{base}/news/press-releases-latest", {**k, "limit": 5}),
            ("profile:AAPL", f"{base}/profile", {**k, "symbol": "AAPL"}),
            ("shares-float:AAPL", f"{base}/shares-float", {**k, "symbol": "AAPL"}),
            ("splits:AAPL", f"{base}/splits", {**k, "symbol": "AAPL"}),
        ]
        results = [await probe(c, *t) for t in tests]
        sec = await probe(c, "sec:company_tickers",
                          "https://www.sec.gov/files/company_tickers.json", None)
        # SEC needs UA header
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": cfg.sec_user_agent}) as c2:
            sec = await probe(c2, "sec:company_tickers",
                              "https://www.sec.gov/files/company_tickers.json", None)
            sec["fields"] = None
            sub = await probe(c2, "sec:submissions:AAPL",
                              "https://data.sec.gov/submissions/CIK0000320193.json", None)
        results += [sec, sub]
        if cfg.openai_api_key:
            async with httpx.AsyncClient(timeout=30) as c3:
                t0 = time.monotonic()
                r = await c3.get("https://api.openai.com/v1/models",
                                 headers={"Authorization": f"Bearer {cfg.openai_api_key}"})
                ids = []
                if r.status_code == 200:
                    ids = [m["id"] for m in r.json().get("data", [])][:400]
                results.append({"endpoint": "openai:models", "status": r.status_code,
                                "latency_ms": int((time.monotonic() - t0) * 1000),
                                "records": len(ids)})
                wanted = [m for m in ids if any(x in m for x in ("gpt-5", "gpt-4o", "o4", "gpt-4.1"))][:12]
                results.append({"endpoint": "openai:candidate-models", "status": 200,
                                "fields": wanted})
        for r in results:
            print(json.dumps(r))


asyncio.run(main())
