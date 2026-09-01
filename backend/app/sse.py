"""In-process SSE broadcaster for live dashboard updates."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List


class Broadcaster:
    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue):
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    async def publish(self, event: str, data: Dict[str, Any]):
        payload = json.dumps(data, default=str)
        async with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait((event, payload))
                except asyncio.QueueFull:
                    pass


broadcaster = Broadcaster()
