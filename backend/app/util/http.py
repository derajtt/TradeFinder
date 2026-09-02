"""Shared async HTTP plumbing: timeouts, bounded retries with jitter, a global
token-bucket rate limiter per provider, a simple circuit breaker, and DB request logging.
Secrets are stripped from anything that gets logged."""
from __future__ import annotations

import asyncio
import random
import re
import time as _time
from typing import Any, Dict, Optional

import httpx

SECRET_RE = re.compile(r"(apikey=)[^&\s]+", re.I)


def redact(text: str) -> str:
    return SECRET_RE.sub(r"\1***", text or "")


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int):
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = _time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            while True:
                now = _time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at: Optional[float] = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if _time.monotonic() - self.opened_at >= self.cooldown:
            self.opened_at = None
            self.failures = 0
            return True
        return False

    def record(self, ok: bool):
        if ok:
            self.failures = 0
            self.opened_at = None
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = _time.monotonic()


class ProviderClient:
    """One per provider. get_json() applies limiter -> breaker -> retries."""

    def __init__(self, name: str, rate_per_sec: float = 4.0, burst: int = 8,
                 timeout: float = 15.0, headers: Optional[Dict[str, str]] = None):
        self.name = name
        self.bucket = TokenBucket(rate_per_sec, burst)
        self.breaker = CircuitBreaker()
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers or {},
                                        follow_redirects=True)
        self.request_log = []  # in-memory ring; flushed to DB by scheduler

    async def close(self):
        await self.client.aclose()

    def _log(self, endpoint: str, status: int, latency_ms: int, count: int, ok: bool, note: str = ""):
        self.request_log.append({
            "provider": self.name, "endpoint": redact(endpoint)[:128],
            "status_code": status, "latency_ms": latency_ms,
            "record_count": count, "ok": ok, "note": redact(note)[:250],
        })
        if len(self.request_log) > 500:
            del self.request_log[:250]

    async def get_json(self, url: str, params: Optional[Dict[str, Any]] = None,
                       endpoint_name: str = "", retries: int = 3) -> Any:
        name = endpoint_name or url.split("?")[0].rsplit("/", 2)[-1]
        if not self.breaker.allow():
            self._log(name, 0, 0, 0, False, "circuit open")
            raise RuntimeError(f"{self.name} circuit open")
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            await self.bucket.acquire()
            t0 = _time.monotonic()
            try:
                resp = await self.client.get(url, params=params)
                latency = int((_time.monotonic() - t0) * 1000)
                if resp.status_code == 429 or resp.status_code >= 500:
                    self._log(name, resp.status_code, latency, 0, False, "retryable")
                    self.breaker.record(False)
                    await asyncio.sleep(min(8.0, (2 ** attempt) + random.random()))
                    continue
                if resp.status_code >= 400:
                    # A 4xx is about THIS request — a plan that lacks the
                    # endpoint (402/403), a bad symbol (404), a bad key (401).
                    # It says nothing about provider health, so it must not
                    # count toward the breaker: five 402s from an unentitled
                    # endpoint would otherwise open the circuit for every
                    # endpoint that works, blinding the whole platform.
                    self._log(name, resp.status_code, latency, 0, False,
                              "client error (not counted toward circuit)")
                    raise RuntimeError(f"{self.name} {name} HTTP {resp.status_code}")
                data = resp.json()
                count = len(data) if isinstance(data, list) else 1
                self._log(name, resp.status_code, latency, count, True)
                self.breaker.record(True)
                return data
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                self._log(name, 0, int((_time.monotonic() - t0) * 1000), 0, False, type(e).__name__)
                self.breaker.record(False)
                await asyncio.sleep(min(8.0, (2 ** attempt) + random.random()))
        raise RuntimeError(f"{self.name} {name} failed after {retries} attempts: {last_exc}")
