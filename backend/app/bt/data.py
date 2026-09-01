"""Backtester data layer: cached, rate-limited access to historical sources.
Every payload is cached in bt_cache so reruns cost zero API calls.
Resolution truth: 5-minute bars WITH premarket (1-min is not in the plan)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import get_config
from ..models import BtCache
from ..util.timeutil import is_trading_day

ET = ZoneInfo("America/New_York")

SPREAD_EST_TABLE = [  # (tier_hi, est_spread_pct) conservative historical estimates
    (0.25, 3.0), (0.50, 2.2), (1.00, 1.8), (2.50, 1.4), (5.01, 1.2),
]


def est_spread_pct(price: float, pm_dollar_vol: float) -> float:
    base = next((s for hi, s in SPREAD_EST_TABLE if price < hi), 1.0)
    if pm_dollar_vol < 100_000:
        base *= 1.6
    elif pm_dollar_vol > 1_000_000:
        base *= 0.7
    return round(base, 2)


class BtData:
    def __init__(self, session_factory, rps: float = 3.0):
        self.sf = session_factory
        cfg = get_config()
        self.base = cfg.fmp_rest_base
        self.key = cfg.fmp_api_key
        self.sec_ua = cfg.sec_user_agent
        self.client = httpx.AsyncClient(timeout=30)
        self._last_call = 0.0
        self._min_gap = 1.0 / rps
        self.api_calls = 0
        self.cache_hits = 0

    async def close(self):
        await self.client.aclose()

    async def _cached(self, key: str, fetch, ttl_days: int = 3650):
        async with self.sf() as db:
            row = (await db.execute(select(BtCache).where(BtCache.cache_key == key))
                   ).scalar_one_or_none()
            if row is not None:
                self.cache_hits += 1
                return row.payload.get("data")
        wait = self._min_gap - (_time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = _time.monotonic()
        data = await fetch()
        self.api_calls += 1
        async with self.sf() as db:
            db.add(BtCache(cache_key=key, payload={"data": data}))
            try:
                await db.commit()
            except Exception:
                await db.rollback()
        return data

    async def _get_retry(self, url: str, params=None, headers=None, attempts=3):
        last = None
        for a in range(attempts):
            try:
                r = await self.client.get(url, params=params, headers=headers)
                if r.status_code in (429, 500, 502, 503):
                    await asyncio.sleep(1.5 ** (a + 1))
                    continue
                return r
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(1.5 ** (a + 1))
        raise last or RuntimeError("retries exhausted")

    async def _fmp(self, path: str, params: Dict[str, Any]):
        p = dict(params)
        p["apikey"] = self.key
        try:
            r = await self._get_retry(f"{self.base}/{path}", params=p)
        except Exception:
            return None
        if r.status_code != 200:
            return {"__status": r.status_code}
        try:
            return r.json()
        except Exception:
            return None

    # ── sources ──
    async def five_min_bars(self, symbol: str, d: str) -> List[dict]:
        key = f"5min:{symbol}:{d}"
        data = await self._cached(key, lambda: self._fmp(
            "historical-chart/5min", {"symbol": symbol, "from": d, "to": d,
                                      "extended": "true"}))
        out = []
        for row in data if isinstance(data, list) else []:
            try:
                ts = datetime.fromisoformat(row["date"]).replace(tzinfo=ET)
            except (ValueError, KeyError):
                continue
            out.append({"ts": ts, "minute_of_day": ts.hour * 60 + ts.minute,
                        "o": float(row["open"]), "h": float(row["high"]),
                        "l": float(row["low"]), "c": float(row["close"]),
                        "v": float(row.get("volume") or 0)})
        out.sort(key=lambda b: b["ts"])
        return out

    async def global_news(self, d: str) -> List[dict]:
        key = f"news:{d}"
        data = await self._cached(key, lambda: self._fmp(
            "news/stock", {"from": d, "to": d, "limit": 250}))
        out = []
        for row in data if isinstance(data, list) else []:
            try:
                pub = datetime.fromisoformat(row["publishedDate"]).replace(tzinfo=ET)
            except (ValueError, KeyError, TypeError):
                continue
            out.append({"symbol": str(row.get("symbol") or "").upper(),
                        "published_et": pub, "title": row.get("title") or "",
                        "text": (row.get("text") or "")[:1200],
                        "site": row.get("site") or "", "url": row.get("url") or ""})
        return out

    async def eod(self, symbol: str) -> List[dict]:
        key = f"eod:{symbol}"
        data = await self._cached(key, lambda: self._fmp(
            "historical-price-eod/full", {"symbol": symbol, "from": "2024-01-01",
                                          "to": "2026-12-31"}))
        rows = data if isinstance(data, list) else []
        return [{"date": r.get("date"), "open": r.get("open"), "close": r.get("close"),
                 "volume": r.get("volume")} for r in rows]

    async def prev_close(self, symbol: str, d: str) -> Optional[float]:
        series = await self.eod(symbol)
        prior = [r for r in series if r["date"] and r["date"] < d]
        if not prior:
            return None
        prior.sort(key=lambda r: r["date"])
        return prior[-1]["close"]

    async def current_float(self, symbol: str) -> Optional[float]:
        """CURRENT float only — historical float is unavailable on this plan.
        All rotation metrics derived from this are labeled ESTIMATED_CURRENT."""
        key = f"float:{symbol}"
        data = await self._cached(key, lambda: self._fmp(
            "shares-float", {"symbol": symbol}))
        row = data[0] if isinstance(data, list) and data else None
        try:
            return float(row.get("floatShares")) if row else None
        except (TypeError, ValueError):
            return None

    async def cik_ticker_map(self) -> Dict[str, str]:
        key = "cikmap:v1"

        async def fetch():
            r = await self._get_retry("https://www.sec.gov/files/company_tickers.json",
                                      headers={"User-Agent": self.sec_ua})
            return r.json() if r.status_code == 200 else {}
        data = await self._cached(key, fetch)
        out = {}
        for row in (data or {}).values():
            try:
                out[str(row["cik_str"])] = str(row["ticker"]).upper()
            except (KeyError, TypeError):
                continue
        return out

    async def acceptance_times(self, cik: str) -> Dict[str, str]:
        """accession -> acceptanceDateTime (free EDGAR submissions API)."""
        key = f"accept:{cik}"

        async def fetch():
            try:
                r = await self._get_retry(
                    f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                    headers={"User-Agent": self.sec_ua})
            except Exception:
                return {}
            if r.status_code != 200:
                return {}
            rec = r.json().get("filings", {}).get("recent", {})
            return {"acc": rec.get("accessionNumber", []),
                    "at": rec.get("acceptanceDateTime", []),
                    "form": rec.get("form", [])}
        data = await self._cached(key, fetch)
        out = {}
        accs = (data or {}).get("acc", [])
        ats = (data or {}).get("at", [])
        forms = (data or {}).get("form", [])
        for i, a in enumerate(accs):
            if i < len(ats):
                out[a] = {"at": ats[i], "form": forms[i] if i < len(forms) else ""}
        return out

    async def sym_news(self, symbol: str, frm: str, to: str) -> List[dict]:
        key = f"snews:{symbol}:{frm}:{to}"
        data = await self._cached(key, lambda: self._fmp(
            "news/stock", {"symbols": symbol, "from": frm, "to": to, "limit": 20}))
        out = []
        for row in data if isinstance(data, list) else []:
            try:
                pub = datetime.fromisoformat(row["publishedDate"]).replace(tzinfo=ET)
            except (ValueError, KeyError, TypeError):
                continue
            out.append({"symbol": symbol, "published_et": pub,
                        "title": row.get("title") or "",
                        "text": (row.get("text") or "")[:1200],
                        "site": row.get("site") or "", "url": row.get("url") or ""})
        return out

    async def sec_form_index(self, d: str) -> List[dict]:
        """Free EDGAR daily form index: every filing with form type + CIK."""
        dt = date.fromisoformat(d)
        q = (dt.month - 1) // 3 + 1
        url = (f"https://www.sec.gov/Archives/edgar/daily-index/{dt.year}/QTR{q}/"
               f"form.{dt.strftime('%Y%m%d')}.idx")
        key = f"secidx:{d}"

        async def fetch():
            try:
                r = await self._get_retry(url, headers={"User-Agent": self.sec_ua})
            except Exception:
                return ""
            return r.text if r.status_code == 200 else ""
        text = await self._cached(key, fetch)
        out = []
        for line in (text or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].replace("-", "").replace("/", "").isalnum():
                form = parts[0]
                if form in ("8-K", "8-K/A", "6-K", "424B5", "424B4", "424B3",
                            "S-1", "S-3", "EFFECT"):
                    try:
                        cik = parts[-3]
                        fn = parts[-1]
                        acc = fn.rsplit("/", 1)[-1].replace(".txt", "")
                        out.append({"form": form, "cik": cik.lstrip("0"),
                                    "accession": acc})
                    except IndexError:
                        continue
        return out


def trading_sessions(start: str, end: str) -> List[str]:
    d = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while d <= e:
        if is_trading_day(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def doc_hash(*parts: str) -> str:
    return hashlib.sha256("||".join(parts).encode()).hexdigest()[:32]
