"""Premarket seeding: provider 5-minute bars fill gaps in self-accumulated
1-minute history without overwriting minutes we actually observed."""
from datetime import datetime, timezone

from app.scanner.bars import merge_pm_bars
from app.scanner.features import structure_features, vwap


def _b(minute, px, vol, src=None):
    d = {"ts_utc": datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc), "minute_of_day": minute,
         "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": vol}
    if src:
        d["source"] = src
    return d


def test_merge_prefers_observed_minutes_and_fills_gaps():
    accumulated = [_b(520, 1.00, 100), _b(521, 1.01, 120)]          # we saw 08:40-08:41
    seeded = [_b(240, 0.90, 5000, "provider_5m"), _b(515, 0.98, 800, "provider_5m"),
              _b(520, 9.99, 1, "provider_5m"), _b(525, 1.02, 900, "provider_5m")]
    merged = merge_pm_bars(accumulated, seeded)
    minutes = [b["minute_of_day"] for b in merged]
    assert minutes == sorted(minutes)
    # the provider bar covering 08:40-08:44 is dropped because we observed 08:40 ourselves
    assert not any(b.get("source") == "provider_5m" and b["minute_of_day"] == 520 for b in merged)
    assert any(b["minute_of_day"] == 240 for b in merged) and any(b["minute_of_day"] == 525 for b in merged)
    # participation now reflects the whole session, not the two minutes we saw
    assert sum(1 for b in merged if b["volume"] > 0) == 5


def test_seeded_bars_drive_structure_and_vwap():
    seeded = [_b(240 + 5 * i, 1.0 + 0.01 * i, 1000 + 10 * i, "provider_5m") for i in range(66)]
    st = structure_features(seeded)
    assert st["pm_high"] is not None and st["pm_low"] is not None and st["pm_high"] > st["pm_low"]
    assert vwap(seeded) is not None
