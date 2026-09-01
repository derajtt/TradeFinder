import pytest

from app.scoring.engine import DEFAULT_SETTINGS
from app.settings_service import get_settings, update_settings

pytestmark = pytest.mark.asyncio


async def test_defaults(db):
    s = await get_settings(db)
    assert s["price_min"] == 0.0
    assert s["price_max"] == 5.0
    assert s["min_pm_dollar_volume"] == 100_000


async def test_update_and_blank_means_no_limit(db):
    s = await update_settings(db, {"market_cap_max": "", "float_max": None,
                                   "price_max": "10"})
    assert s["market_cap_max"] is None    # blank => unbounded
    assert s["float_max"] is None
    assert s["price_max"] == 10.0


async def test_micro_cap_targeting(db):
    s = await update_settings(db, {"market_cap_min": 0, "market_cap_max": 50_000_000})
    assert s["market_cap_max"] == 50_000_000


async def test_unknown_keys_ignored(db):
    s = await update_settings(db, {"evil_key": "x", "min_score_for_buy": 80})
    assert "evil_key" not in s
    assert s["min_score_for_buy"] == 80


async def test_bool_coercion(db):
    s = await update_settings(db, {"momentum_only_mode": "true", "paused": "false"})
    assert s["momentum_only_mode"] is True
    assert s["paused"] is False


async def test_api_key_guard_logic():
    """hmac comparison used by the auth middleware."""
    from app.main import secrets_compare
    assert secrets_compare("abc", "abc") is True
    assert secrets_compare("abc", "abd") is False
    assert secrets_compare("", "expected") is False
