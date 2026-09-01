from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_config


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    url = get_config().database_url
    if url.startswith("sqlite+aiosqlite:///./"):
        # Anchor relative sqlite paths to the backend directory so cwd doesn't matter.
        rel = url.replace("sqlite+aiosqlite:///./", "")
        base = Path(__file__).resolve().parents[1]
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{p}"
    return url


engine = create_async_engine(_database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session
