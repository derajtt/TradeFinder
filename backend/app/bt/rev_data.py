"""Historical bar fetcher for the Extreme Reversion study.

FMP caps every intraday request at ~390-424 bars regardless of the range asked
for, so history is assembled from chunked windows and cached on disk. Nothing
here invents a bar: if a window returns empty it stays empty and the coverage
report says so.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

BASE = "https://financialmodelingprep.com/stable"
CACHE = pathlib.Path(__file__).resolve().parents[3] / "data" / "rev_cache"
CACHE.mkdir(parents=True, exist_ok=True)

# calendar days per request that stay inside the bar cap, with margin
CHUNK_DAYS = {"5min": 5, "15min": 14, "30min": 28, "1hour": 55, "4hour": 200}


def _key() -> str:
    for line in open(pathlib.Path(__file__).resolve().parents[3] / ".env"):
        if line.startswith("FMP_API_KEY="):
            return line.strip().split("=", 1)[1]
    return os.environ.get("FMP_API_KEY", "")


class RevData:
    def __init__(self, rps: float = 4.0):
        self.client = httpx.AsyncClient(timeout=60)
        self.key = _key()
        self._gap = 1.0 / rps
        self._last = 0.0
        self.api_calls = 0
        self.cache_hits = 0

    async def close(self):
        await self.client.aclose()

    async def _throttle(self):
        now = asyncio.get_event_loop().time()
        wait = self._gap - (now - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = asyncio.get_event_loop().time()

    async def _get(self, path: str, params: Dict[str, Any],
                   attempts: int = 3) -> Any:
        for a in range(attempts):
            await self._throttle()
            try:
                r = await self.client.get(f"{BASE}/{path}",
                                          params={**params, "apikey": self.key})
                self.api_calls += 1
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (402, 403):
                    return {"__error__": r.status_code}
                if r.status_code == 429:
                    await asyncio.sleep(2 ** a * 2)
                    continue
            except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout):
                await asyncio.sleep(1.5 * (a + 1))
        return None

    def _cache_path(self, symbol: str, interval: str) -> pathlib.Path:
        return CACHE / f"{symbol.upper()}_{interval}.json"

    def _load(self, symbol: str, interval: str) -> Dict[str, Any]:
        p = self._cache_path(symbol, interval)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return {"bars": {}, "windows": []}
        return {"bars": {}, "windows": []}

    def _save(self, symbol: str, interval: str, blob: Dict[str, Any]):
        self._cache_path(symbol, interval).write_text(json.dumps(blob))

    @staticmethod
    def _norm(rows: List[dict]) -> List[dict]:
        out = []
        for r in rows or []:
            try:
                ds = r.get("date") or r.get("datetime")
                if not ds:
                    continue
                dt = datetime.strptime(ds, "%Y-%m-%d %H:%M:%S") if " " in ds \
                    else datetime.strptime(ds, "%Y-%m-%d")
                out.append({"date": ds, "time": int(dt.timestamp()),
                            "o": float(r["open"]), "h": float(r["high"]),
                            "l": float(r["low"]), "c": float(r["close"]),
                            "v": float(r.get("volume") or 0)})
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def bars(self, symbol: str, interval: str, start: str,
                   end: str, extended: bool = False) -> List[dict]:
        """Ascending closed bars in [start, end]. Cached windows are never refetched.
        `extended` includes pre/post-market for intraday intervals (own cache key)."""
        cache_iv = f"{interval}_ext" if (extended and interval != "1day") else interval
        blob = self._load(symbol, cache_iv)
        bars: Dict[str, dict] = blob.get("bars", {})
        done = set(blob.get("windows", []))

        if interval == "1day":
            # Tag by range: a bare "eod" tag made an older cached history look
            # complete, so a later request for newer dates silently returned
            # the stale slice (empty) instead of fetching.
            tag = f"eod:{start}:{end}"
            if tag not in done:
                j = await self._get("historical-price-eod/full",
                                    {"symbol": symbol, "from": start, "to": end})
                if isinstance(j, list):
                    for b in self._norm(j):
                        bars[b["date"]] = b
                    done.add(tag)
                    self._save(symbol, interval,
                               {"bars": bars, "windows": sorted(done)})
                elif isinstance(j, dict) and j.get("__error__"):
                    return []
            else:
                self.cache_hits += 1
        else:
            step = CHUNK_DAYS.get(interval, 30)
            d0 = datetime.strptime(start, "%Y-%m-%d").date()
            d1 = datetime.strptime(end, "%Y-%m-%d").date()
            cur = d0
            dirty = False
            while cur <= d1:
                nxt = min(cur + timedelta(days=step - 1), d1)
                tag = f"{cur}:{nxt}"
                if tag in done:
                    self.cache_hits += 1
                    cur = nxt + timedelta(days=1)
                    continue
                params = {"symbol": symbol, "from": str(cur), "to": str(nxt)}
                if extended:
                    params["extended"] = "true"
                j = await self._get(f"historical-chart/{interval}", params)
                if isinstance(j, dict) and j.get("__error__"):
                    return []
                if isinstance(j, list):
                    for b in self._norm(j):
                        bars[b["date"]] = b
                    done.add(tag)          # empty windows are real (holidays) — recorded
                    dirty = True
                cur = nxt + timedelta(days=1)
            if dirty:
                self._save(symbol, cache_iv, {"bars": bars, "windows": sorted(done)})

        rows = sorted(bars.values(), key=lambda b: b["time"])
        lo = datetime.strptime(start, "%Y-%m-%d").timestamp()
        hi = datetime.strptime(end, "%Y-%m-%d").timestamp() + 86400
        return [b for b in rows if lo <= b["time"] <= hi]

    def coverage(self, symbol: str, interval: str) -> Dict[str, Any]:
        blob = self._load(symbol, interval)
        rows = sorted(blob.get("bars", {}).values(), key=lambda b: b["time"])
        if not rows:
            return {"symbol": symbol, "interval": interval, "bars": 0}
        return {"symbol": symbol, "interval": interval, "bars": len(rows),
                "first": rows[0]["date"], "last": rows[-1]["date"]}
