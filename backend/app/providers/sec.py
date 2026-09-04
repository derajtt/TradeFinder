"""SEC EDGAR adapter. No API key; identifying User-Agent; conservative rate limit
(SEC fair-access ceiling is 10 req/s — we stay far below)."""
from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from ..config import get_config
from ..util.http import ProviderClient

RELEVANT_FORMS = {
    "8-K", "8-K/A", "10-Q", "10-K", "6-K", "20-F", "S-1", "S-1/A", "S-3", "S-3/A",
    "424B1", "424B2", "424B3", "424B4", "424B5", "EFFECT", "DEF 14A", "PRE 14A",
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "3", "4", "5",
    "NT 10-Q", "NT 10-K", "25", "25-NSE",
}

DILUTION_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "424B1", "424B2", "424B3", "424B4", "424B5", "EFFECT"}


class SecProvider:
    def __init__(self):
        cfg = get_config()
        self.http = ProviderClient("sec", rate_per_sec=2.0, burst=4,
                                   headers={"User-Agent": cfg.sec_user_agent,
                                            "Accept-Encoding": "gzip, deflate"})
        self._ticker_map: Dict[str, str] = {}
        self._ticker_map_ts: float = 0.0
        self._submissions_cache: Dict[str, tuple] = {}

    async def close(self):
        await self.http.close()

    async def ticker_to_cik(self, symbol: str) -> Optional[str]:
        now = _time.monotonic()
        if not self._ticker_map or now - self._ticker_map_ts > 12 * 3600:
            data = await self.http.get_json("https://www.sec.gov/files/company_tickers.json",
                                            endpoint_name="company_tickers")
            m = {}
            for row in (data or {}).values():
                try:
                    m[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
                except (KeyError, TypeError):
                    continue
            if m:
                self._ticker_map = m
                self._ticker_map_ts = now
        return self._ticker_map.get(symbol.upper())

    async def recent_filings(self, symbol: str, days: int = 7) -> List[dict]:
        cik = await self.ticker_to_cik(symbol)
        if not cik:
            return []
        hit = self._submissions_cache.get(cik)
        if hit and _time.monotonic() - hit[0] < 900:
            subs = hit[1]
        else:
            subs = await self.http.get_json(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                endpoint_name="submissions")
            self._submissions_cache[cik] = (_time.monotonic(), subs)
        recent = (subs or {}).get("filings", {}).get("recent", {})
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        out = []
        forms = recent.get("form", [])
        for i in range(len(forms)):
            form = forms[i]
            if form not in RELEVANT_FORMS:
                continue
            acc_dt = self._parse_dt(self._at(recent, "acceptanceDateTime", i))
            filed = self._parse_date(self._at(recent, "filingDate", i))
            ref_dt = acc_dt or filed
            if ref_dt is None or ref_dt < cutoff:
                continue
            accession = str(self._at(recent, "accessionNumber", i) or "")
            primary = str(self._at(recent, "primaryDocument", i) or "")
            acc_nodash = accession.replace("-", "")
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary}"
                   if accession and primary else
                   f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}")
            out.append({
                "symbol": symbol.upper(),
                "cik": cik,
                "accession": accession,
                "form_type": form,
                "filed_at": filed,
                "accepted_at": acc_dt,
                "items": str(self._at(recent, "items", i) or ""),
                "title": str(self._at(recent, "primaryDocDescription", i) or ""),
                "primary_doc_url": url,
                "is_dilution_form": form in DILUTION_FORMS,
            })
        out.sort(key=lambda f: f["accepted_at"] or f["filed_at"] or cutoff, reverse=True)
        return out

    @staticmethod
    def _at(recent: dict, key: str, i: int):
        arr = recent.get(key) or []
        return arr[i] if i < len(arr) else None

    @staticmethod
    def _parse_dt(v) -> Optional[datetime]:
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(v) -> Optional[datetime]:
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


# ── live filings feed ────────────────────────────────────────────────────────
import asyncio as _asyncio
import re as _re
import httpx as _httpx

_ATOM_ENTRY = _re.compile(r"<entry>(.*?)</entry>", _re.S)
_ATOM_TITLE = _re.compile(r"<title>\s*([^<]+?)\s*</title>")
_ATOM_LINK = _re.compile(r'<link[^>]*href="([^"]+)"')
_ATOM_UPDATED = _re.compile(r"<updated>([^<]+)</updated>")
_ACCNO = _re.compile(r"(\d{10}-\d{2}-\d{6})")
_TITLE_PARTS = _re.compile(r"^\s*(.+?)\s+-\s+(.*?)\s*\((\d{10})\)")   # "8-K - Acme Corp (0001234567) (Filer)"
_ITEM = _re.compile(r"Item\s+(\d\.\d\d)")


def parse_getcurrent_atom(xml: str) -> List[dict]:
    """Parse EDGAR's getcurrent Atom feed. Pure regex — the feed is small and
    regular, and this avoids a parser dependency in the hot loop."""
    out = []
    for m in _ATOM_ENTRY.finditer(xml or ""):
        body = m.group(1)
        t = _ATOM_TITLE.search(body); l = _ATOM_LINK.search(body); u = _ATOM_UPDATED.search(body)
        acc = _ACCNO.search(body)
        if not (t and l and acc):
            continue
        parts = _TITLE_PARTS.match(t.group(1).replace("&amp;", "&"))
        if not parts:
            continue
        form, name, cik = parts.group(1), parts.group(2), parts.group(3)
        try:
            accepted = datetime.fromisoformat(u.group(1).replace("Z", "+00:00")) if u else None
        except ValueError:
            accepted = None
        out.append({"form_type": form, "company": name, "cik": cik.lstrip("0") or "0",
                    "accession": acc.group(1), "index_url": l.group(1), "accepted_at": accepted})
    return out


class LiveFilings:
    """Market-wide newest filings from EDGAR (updates about once a minute at
    the source). Paced well under SEC's 10 req/s; one call per poll plus one
    small index fetch per NEW 8-K to read its Item codes."""

    def __init__(self, ua: str):
        self.ua = ua
        self.client = _httpx.AsyncClient(timeout=20, headers={"User-Agent": ua,
                                                                 "Accept-Encoding": "gzip, deflate"})
        self.seen: set = set()
        self.last_ok_at: float = 0.0
        self.last_error: str = ""

    async def close(self):
        await self.client.aclose()

    async def latest(self, form_type: str = "8-K", count: int = 100) -> List[dict]:
        url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
               f"&type={form_type}&company=&dateb=&owner=include&count={count}&output=atom")
        try:
            r = await self.client.get(url)
            if r.status_code != 200:
                self.last_error = f"HTTP {r.status_code}"
                return []
            self.last_ok_at = _time.monotonic(); self.last_error = ""
            return parse_getcurrent_atom(r.text)
        except _httpx.HTTPError as e:
            self.last_error = type(e).__name__
            return []

    async def items_for(self, index_url: str) -> str:
        """Item codes from the filing index page, e.g. '2.02,9.01'."""
        try:
            await _asyncio.sleep(0.15)
            r = await self.client.get(index_url)
            if r.status_code != 200:
                return ""
            found = sorted(set(_ITEM.findall(r.text)))
            return ",".join(found)
        except _httpx.HTTPError:
            return ""
