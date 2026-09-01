"""Persisted settings with prospective-only application + strategy version registry."""
from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AppSetting, StrategyVersion
from .scoring.engine import DEFAULT_SETTINGS, NULLABLE_KEYS, STRATEGY_VERSION


STRING_KEYS = {"buy_confirm_after_et"}


def _coerce(key: str, value):
    if key in STRING_KEYS:
        v = str(value or "").strip()
        if v and not __import__("re").match(r"^([01]?\d|2[0-3]):[0-5]\d$", v):
            return DEFAULT_SETTINGS.get(key)
        return v
    if value is None or value == "" or value == "null":
        return None if key in NULLABLE_KEYS else DEFAULT_SETTINGS.get(key)
    default = DEFAULT_SETTINGS.get(key)
    try:
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
        if isinstance(default, int) and not isinstance(default, bool):
            return int(float(value))
        if isinstance(default, float) or key in NULLABLE_KEYS:
            return float(value)
    except (TypeError, ValueError):
        return default
    return value


async def get_settings(session: AsyncSession) -> Dict[str, Any]:
    row = (await session.execute(select(AppSetting).limit(1))).scalar_one_or_none()
    merged = dict(DEFAULT_SETTINGS)
    if row and isinstance(row.data, dict):
        for k, v in row.data.items():
            if k in DEFAULT_SETTINGS:
                merged[k] = v
    return merged


async def update_settings(session: AsyncSession, patch: Dict[str, Any]) -> Dict[str, Any]:
    row = (await session.execute(select(AppSetting).limit(1))).scalar_one_or_none()
    if row is None:
        row = AppSetting(data={})
        session.add(row)
        await session.flush()
    data = dict(row.data or {})
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            data[k] = _coerce(k, v)
    row.data = data
    await session.commit()
    return await get_settings(session)


async def ensure_strategy_version(session: AsyncSession, thresholds: Dict[str, Any]) -> None:
    row = (await session.execute(
        select(StrategyVersion).where(StrategyVersion.version == STRATEGY_VERSION)
    )).scalar_one_or_none()
    if row is None:
        session.add(StrategyVersion(version=STRATEGY_VERSION, thresholds=thresholds))
        await session.commit()
