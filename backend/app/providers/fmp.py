"""Financial Modeling Prep adapter. All FMP knowledge lives here so the provider
can be replaced without touching scoring or UI code. Responses are treated as
untrusted input and normalized before use."""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..config import get_config
from ..util.http import ProviderClient

ET = ZoneInfo("America/New_York")

EXCLUDED_SUFFIXES = ("W", "WS", "R", "U")  # warrants, rights, units (heuristic, checked with name)
EXCLUDED_NAME_TOKENS = ("etf", "warrant", " right", "preferred", "acquisition corp",
                        "acquisition corporation", "spac", " unit", "trust etf", "fund")
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "NYSEAMERICAN"}


def _f(v, default=None):
    """Safe float from untrusted payload."""
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _parse_ts(v) -> Optional[datetime]:
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            if v > 10_000_000_000:  # ms epoch
                v = v / 1000.0
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        s = str(v).strip()
        # FMP bar timestamps ("YYYY-MM-DD HH:MM:SS") are US/Eastern
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def looks_common_stock(symbol: str, name: str) -> bool:
    s = (symbol or "").upper()
    n = (name or "").lower()
    if not s or not s.isalnum() or len(s) > 5:
        return False
    if any(tok in n for tok in EXCLUDED_NAME_TOKENS):
        return False
    if len(s) == 5 and s.endswith(EXCLUDED_SUFFIXES) and s[-1] in "WRU":
        return False
    return True


class EntitlementError(RuntimeError):
    """Endpoint not included in the current FMP plan (HTTP 402/403)."""


class FmpProvider:
    def __init__(self):
        cfg = get_config()
        self.base = cfg.fmp_rest_base.rstrip("/")
        self.key = cfg.fmp_api_key
        self.http = ProviderClient("fmp", rate_per_sec=2.2, burst=4)
        # 4/s (240/min) drew sustained 429s, and each burst of five tripped
        # the circuit breaker, blinding every endpoint. 2.2/s (~130/min)
        # with a smaller burst keeps headroom under the plan limit.
        self._cache: Dict[str, tuple] = {}
        self.entitlements: Dict[str, dict] = {}  # endpoint -> {ok, status, checked_at}

    def _mark_entitlement(self, name: str, ok: bool, status: int):
        self.entitlements[name] = {"ok": ok, "status": status,
                                   "checked_at": _time.time()}

    def _entitled(self, name: str) -> bool:
        e = self.entitlements.get(name)
        if e is None:
            return True  # unknown until first probe
        if e["ok"]:
            return True
        return _time.time() - e["checked_at"] > 6 * 3600  # re-probe blocked endpoints every 6h

    async def close(self):
        await self.http.close()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
                   cache_ttl: float = 0, endpoint_name: str = "") -> Any:
        params = dict(params or {})
        cache_key = f"{path}|{sorted(params.items())}"
        if cache_ttl > 0:
            hit = self._cache.get(cache_key)
            if hit and _time.monotonic() - hit[0] < cache_ttl:
                return hit[1]
        name = endpoint_name or path
        if not self._entitled(name):
            raise EntitlementError(f"fmp {name} not in plan")
        params["apikey"] = self.key
        try:
            data = await self.http.get_json(f"{self.base}/{path.lstrip('/')}", params=params,
                                            endpoint_name=name)
        except RuntimeError as e:
            msg = str(e)
            if "HTTP 402" in msg or "HTTP 403" in msg:
                code = 402 if "402" in msg else 403
                self._mark_entitlement(name, False, code)
                raise EntitlementError(f"fmp {name} not in plan (HTTP {code})")
            raise
        self._mark_entitlement(name, True, 200)
        # An empty response is not knowledge; caching it for the full TTL
        # made a transient miss look like "no data" for up to a day.
        if cache_ttl > 0 and data not in (None, [], {}):
            self._cache[cache_key] = (_time.monotonic(), data)
        return data

    # ---- discovery ----
    async def biggest_gainers(self) -> List[dict]:
        data = await self._get("biggest-gainers", cache_ttl=45)
        return self._norm_movers(data)

    async def most_actives(self) -> List[dict]:
        data = await self._get("most-actives", cache_ttl=45)
        return self._norm_movers(data)

    def _norm_movers(self, data) -> List[dict]:
        out = []
        for row in data if isinstance(data, list) else []:
            sym = str(row.get("symbol", "")).upper()
            out.append({
                "symbol": sym,
                "name": row.get("name") or "",
                "exchange": str(row.get("exchange") or "").upper(),
                "price": _f(row.get("price")),
                "change": _f(row.get("change")),
                "change_pct": _f(row.get("changesPercentage")),
            })
        return out

    async def exchange_quotes(self, exchange: str) -> List[dict]:
        data = await self._get("batch-exchange-quote", {"exchange": exchange, "short": "false"},
                               cache_ttl=120, endpoint_name=f"batch-exchange-quote:{exchange}")
        out = []
        for row in data if isinstance(data, list) else []:
            out.append(self._norm_quote(row))
        return out

    def _norm_quote(self, row: dict) -> dict:
        return {
            "symbol": str(row.get("symbol", "")).upper(),
            "name": row.get("name") or "",
            "exchange": str(row.get("exchange") or "").upper(),
            "price": _f(row.get("price")),
            "previous_close": _f(row.get("previousClose")),
            "change_pct": _f(row.get("changesPercentage") or row.get("changePercentage")),
            "volume": _f(row.get("volume")),
            "avg_volume": _f(row.get("avgVolume")),
            "market_cap": _f(row.get("marketCap")),
            "day_high": _f(row.get("dayHigh")),
            "day_low": _f(row.get("dayLow")),
            "open": _f(row.get("open")),
            "provider_ts": _parse_ts(row.get("timestamp")),
        }

    async def quote_one(self, symbol: str) -> Optional[dict]:
        """stable /quote accepts a single symbol per call on this plan."""
        data = await self._get("quote", {"symbol": symbol}, cache_ttl=15,
                               endpoint_name="quote")
        if isinstance(data, dict):
            data = [data]
        for r in data if isinstance(data, list) else []:
            if isinstance(r, dict) and str(r.get("symbol", "")).upper() == symbol.upper():
                return self._norm_quote(r)
        return None

    async def quotes(self, symbols: List[str]) -> List[dict]:
        out: List[dict] = []
        for sym in symbols:
            try:
                q = await self.quote_one(sym)
            except Exception:
                q = None
            if q:
                out.append(q)
        return out

    async def aftermarket_quote(self, symbol: str) -> Optional[dict]:
        """Extended-hours bid/ask if entitled; returns None on failure."""
        try:
            data = await self._get("aftermarket-quote", {"symbol": symbol},
                                   cache_ttl=45, endpoint_name="aftermarket-quote")
            row = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else None
            if not row:
                return None
            return {
                "symbol": symbol,
                "bid": _f(row.get("bidPrice")),
                "ask": _f(row.get("askPrice")),
                "bid_size": _f(row.get("bidSize")),
                "ask_size": _f(row.get("askSize")),
                "volume": _f(row.get("volume")),  # extended-session cumulative counter
                "provider_ts": _parse_ts(row.get("timestamp")),
            }
        except Exception:
            return None

    async def aftermarket_trade(self, symbol: str) -> Optional[dict]:
        """Latest extended-hours TRADE print — the real live premarket price."""
        try:
            data = await self._get("aftermarket-trade", {"symbol": symbol},
                                   cache_ttl=20, endpoint_name="aftermarket-trade")
        except EntitlementError:
            return None
        row = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else None
        if not row:
            return None
        return {"symbol": symbol, "price": _f(row.get("price")),
                "size": _f(row.get("tradeSize")),
                "provider_ts": _parse_ts(row.get("timestamp"))}

    # ---- enrichment ----
    async def profile(self, symbol: str) -> Optional[dict]:
        data = await self._get("profile", {"symbol": symbol}, cache_ttl=6 * 3600,
                               endpoint_name="profile")
        row = data[0] if isinstance(data, list) and data else None
        if not row:
            return None
        return {
            "symbol": symbol,
            "name": row.get("companyName") or "",
            "exchange": str(row.get("exchange") or "").upper(),
            "market_cap": _f(row.get("marketCap") or row.get("mktCap")),
            "avg_volume": _f(row.get("averageVolume") or row.get("volAvg")),
            "sector": row.get("sector") or "",
            "industry": row.get("industry") or "",
            "country": row.get("country") or "",
            "is_etf": bool(row.get("isEtf")),
            "is_fund": bool(row.get("isFund")),
            "is_actively_trading": row.get("isActivelyTrading", True),
            "cik": str(row.get("cik") or ""),
            "description": (row.get("description") or "")[:2000],
            "website": row.get("website") or "",
            "raw": {k: row.get(k) for k in ("beta", "lastDividend", "range", "ipoDate")},
        }

    async def shares_float(self, symbol: str) -> Optional[dict]:
        data = await self._get("shares-float", {"symbol": symbol}, cache_ttl=6 * 3600,
                               endpoint_name="shares-float")
        row = data[0] if isinstance(data, list) and data else None
        if not row:
            return None
        return {
            "symbol": symbol,
            "float_shares": _f(row.get("floatShares")),
            "shares_outstanding": _f(row.get("outstandingShares")),
            "free_float_pct": _f(row.get("freeFloat")),
        }

    async def minute_bars(self, symbol: str, date_from: str, date_to: str) -> List[dict]:
        """1-minute bars, ET timestamps normalized to UTC. Includes premarket when entitled."""
        data = await self._get("historical-chart/1min",
                               {"symbol": symbol, "from": date_from, "to": date_to,
                                "extended": "true", "nonadjusted": "false"},
                               cache_ttl=240, endpoint_name="historical-chart-1min")
        out = []
        for row in data if isinstance(data, list) else []:
            ts = _parse_ts(row.get("date"))
            if ts is None:
                continue
            out.append({
                "ts_utc": ts,
                "open": _f(row.get("open")), "high": _f(row.get("high")),
                "low": _f(row.get("low")), "close": _f(row.get("close")),
                "volume": _f(row.get("volume"), 0.0),
            })
        out.sort(key=lambda b: b["ts_utc"])
        return out

    async def splits(self, symbol: str) -> List[dict]:
        try:
            data = await self._get("splits", {"symbol": symbol}, cache_ttl=24 * 3600,
                                   endpoint_name="splits")
        except EntitlementError:
            return []
        out = []
        for row in data if isinstance(data, list) else []:
            out.append({"date": str(row.get("date") or ""),
                        "numerator": _f(row.get("numerator")),
                        "denominator": _f(row.get("denominator"))})
        return out

    async def screener_universe(self, price_min=None, price_max=None,
                                mcap_min=None, mcap_max=None) -> List[dict]:
        """All active common stocks on NASDAQ/NYSE/AMEX inside a widened price band.
        Screener prices are prior-session, so the band is widened to catch gappers."""
        out: List[dict] = []
        for exch in ("NASDAQ", "NYSE", "AMEX"):
            params = {"exchange": exch, "isActivelyTrading": "true",
                      "isEtf": "false", "isFund": "false", "limit": 5000}
            if price_max is not None:
                params["priceLowerThan"] = price_max * 1.6   # widened for overnight gaps
            if price_min:
                params["priceMoreThan"] = max(0.0, price_min * 0.5)
            if mcap_min is not None:
                params["marketCapMoreThan"] = mcap_min
            if mcap_max is not None:
                params["marketCapLowerThan"] = mcap_max
            data = await self._get("company-screener", params, cache_ttl=12 * 3600,
                                   endpoint_name=f"company-screener:{exch}")
            for row in data if isinstance(data, list) else []:
                sym = str(row.get("symbol", "")).upper()
                out.append({"symbol": sym, "name": row.get("companyName") or "",
                            "exchange": exch, "price": _f(row.get("price")),
                            "market_cap": _f(row.get("marketCap")),
                            "volume": _f(row.get("volume"))})
        return out

    # ---- news ----
    async def stock_news_for(self, symbol: str, limit: int = 25) -> List[dict]:
        """Per-symbol news — catches catalysts that rolled out of the global feed."""
        try:
            data = await self._get("news/stock", {"symbols": symbol, "limit": limit},
                                   cache_ttl=600, endpoint_name="news-stock")
        except EntitlementError:
            return []
        return self._norm_news(data, "news")

    async def latest_stock_news(self, limit: int = 100) -> List[dict]:
        data = await self._get("news/stock-latest", {"limit": limit}, cache_ttl=180,
                               endpoint_name="news-stock-latest")
        return self._norm_news(data, "news")

    async def latest_press_releases(self, limit: int = 100) -> List[dict]:
        data = await self._get("news/press-releases-latest", {"limit": limit}, cache_ttl=180,
                               endpoint_name="news-press-releases-latest")
        return self._norm_news(data, "press_release")

    def _norm_news(self, data, kind: str) -> List[dict]:
        out = []
        for row in data if isinstance(data, list) else []:
            out.append({
                "kind": kind,
                "symbol": str(row.get("symbol") or "").upper(),
                "published_at": _parse_ts(row.get("publishedDate") or row.get("date")),
                "source": row.get("publisher") or row.get("site") or "",
                "url": row.get("url") or "",
                "headline": row.get("title") or "",
                "excerpt": (row.get("text") or "")[:1500],
            })
        return out
