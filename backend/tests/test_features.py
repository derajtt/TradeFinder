from app.scanner import features as F


def bars(vols, price=2.0):
    return [{"high": price * 1.01, "low": price * 0.99, "close": price, "volume": v}
            for v in vols]


def test_gap_pct():
    assert F.gap_pct(11, 10) == 10.0
    assert F.gap_pct(9, 10) == -10.0
    assert F.gap_pct(None, 10) is None
    assert F.gap_pct(10, 0) is None
    assert F.gap_pct(10, -5) is None


def test_spread_pct():
    assert abs(F.spread_pct(1.00, 1.02) - 1.9801) < 0.01
    assert F.spread_pct(None, 1.02) is None
    assert F.spread_pct(1.02, 1.00) is None   # crossed quote rejected
    assert F.spread_pct(0, 1.0) is None


def test_vwap_basic():
    v = F.vwap([{"high": 2, "low": 1, "close": 1.5, "volume": 100},
                {"high": 3, "low": 2, "close": 2.5, "volume": 300}])
    assert 1.5 < v < 2.5
    assert F.vwap([]) is None
    assert F.vwap([{"high": 2, "low": 1, "close": 1.5, "volume": 0}]) is None


def test_vwap_ignores_bad_bars():
    v = F.vwap([{"high": 2, "low": 1, "close": 1.5, "volume": 100},
                {"high": "x", "low": 1, "close": 1, "volume": 50}])
    assert v is not None


def test_premarket_rvol_normal():
    r = F.premarket_rvol(300_000, [100_000] * 10)
    assert r["rvol"] == 3.0
    assert r["coverage"] == 10
    assert r["confidence"] == 1.0


def test_premarket_rvol_insufficient_coverage_never_fabricates():
    r = F.premarket_rvol(300_000, [100_000] * 3)   # < 5 sessions
    assert r["rvol"] is None
    assert r["confidence"] == 0.0


def test_premarket_rvol_rejects_zero_baselines():
    r = F.premarket_rvol(300_000, [0, 0, 0, 0, 0, 0])
    assert r["rvol"] is None


def test_volume_acceleration():
    assert F.volume_acceleration(300, [100, 100, 100]) == 3.0
    assert F.volume_acceleration(300, []) is None
    assert F.volume_acceleration(0, [100]) is None


def test_structure_features():
    bs = bars([100] * 12, price=2.0)
    for i, b in enumerate(bs):
        b["high"] = 2.0 + i * 0.1
        b["low"] = 1.9 + i * 0.1
        b["close"] = 1.95 + i * 0.1
    s = F.structure_features(bs)
    assert s["pm_high"] == max(b["high"] for b in bs)
    assert s["hh_hl"] == 1.0
    assert s["dist_from_high_pct"] is not None


def test_dollar_volume():
    assert F.dollar_volume([{"close": 2.0, "volume": 1000}]) == 2000.0


def test_estimated_rvol_and_curve():
    from app.scanner.bars import estimated_rvol, expected_pm_fraction
    assert expected_pm_fraction(240) == 0.0
    assert expected_pm_fraction(600) == 0.065          # after 9:30 clamps to last point
    assert estimated_rvol(0, 1_000_000, 480) is None   # no volume -> no estimate
    assert estimated_rvol(60_000, None, 480) is None   # unknown avg volume -> None


def test_stale_trade_uses_live_mid_and_blocks_freshness():
    """A stale trade print with a live book must display the indicative mid and
    keep quote_fresh False (no BUY at a stale price)."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.scanner.funnel import compute_market_features
    from app.scoring.engine import DEFAULT_SETTINGS

    now = datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York")).replace(hour=5, minute=0)
    quote = {"price": 0.90, "previous_close": 0.66,
             "provider_ts": now - timedelta(hours=10)}          # yesterday's print
    amq = {"bid": 0.78, "ask": 0.80, "provider_ts": now}         # live book
    bars = [{"ts_utc": now, "minute_of_day": 250 + i, "open": 0.8, "high": 0.81,
             "low": 0.79, "close": 0.80, "volume": 100} for i in range(5)]
    f = compute_market_features(quote, bars, [], amq, dict(DEFAULT_SETTINGS), et)
    assert f["price_indicative"] is True
    assert abs(f["price"] - 0.79) < 1e-9          # mid of 0.78/0.80
    assert f["quote_fresh"] is False              # stale trade -> BUY impossible
    # gap now computed from the live mid, not the stale print
    assert abs(f["gap_pct"] - ((0.79 - 0.66) / 0.66 * 100)) < 0.01


def test_fresh_trade_price_wins_over_mid():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.scanner.funnel import compute_market_features
    from app.scoring.engine import DEFAULT_SETTINGS

    now = datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York")).replace(hour=5, minute=0)
    quote = {"price": 0.85, "previous_close": 0.66, "provider_ts": now}
    amq = {"bid": 0.78, "ask": 0.80, "provider_ts": now}
    f = compute_market_features(quote, [], [], amq, dict(DEFAULT_SETTINGS), et)
    assert f["price"] == 0.85
    assert f["price_indicative"] is False
    assert f["quote_fresh"] is True


def test_wide_book_mid_rejected():
    """A wide 4 AM book must NOT produce an indicative mid — keep the stale print."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from app.scanner.funnel import compute_market_features
    from app.scoring.engine import DEFAULT_SETTINGS

    now = datetime.now(timezone.utc)
    et = now.astimezone(ZoneInfo("America/New_York")).replace(hour=5, minute=0)
    quote = {"price": 0.876, "previous_close": 0.60,
             "provider_ts": now - timedelta(hours=10)}
    amq = {"bid": 0.90, "ask": 2.07, "provider_ts": now}   # ~79% spread book
    f = compute_market_features(quote, [], [], amq, dict(DEFAULT_SETTINGS), et)
    assert f["price"] == 0.876            # keeps last real print
    assert f["price_indicative"] is False
    assert f["quote_fresh"] is False
