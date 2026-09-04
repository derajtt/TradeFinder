"""Quant Lab harness contract tests: causality, same-bar ambiguity, costs,
split boundaries, composite composition, registry tolerance, stage rules."""
import textwrap
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pytest

from app.lab import backtest as B
from app.lab import registry
from app.lab.base import Signal, StrategyMeta

HOURS = [570, 630, 690, 750, 810, 870, 930]          # 09:30 .. 15:30 ET


def bar(date: str, o: float, h: float, l: float, c: float, v: float = 1000.0) -> dict:
    mod = int(date[11:13]) * 60 + int(date[14:16]) if len(date) > 10 else 960
    return {"o": o, "h": h, "l": l, "c": c, "v": v,
            "time": int(datetime.strptime(date[:16] if len(date) > 10 else date,
                                          "%Y-%m-%d %H:%M" if len(date) > 10 else "%Y-%m-%d").timestamp()),
            "minute_of_day": mod, "date": date}


def make_series(n_days: int, start: str = "2024-01-02", price: float = 100.0,
                drift: float = 0.0, amp: float = 0.5) -> List[dict]:
    """7 hourly bars per weekday; closes drift linearly, highs/lows ±amp."""
    out, d, k = [], datetime.strptime(start, "%Y-%m-%d"), 0
    while len(out) < n_days * 7:
        if d.weekday() < 5:
            for m in HOURS:
                c = price + drift * k
                o = out[-1]["c"] if out else c
                out.append(bar(f"{d:%Y-%m-%d} {m // 60:02d}:{m % 60:02d}:00",
                               o, max(o, c) + amp, min(o, c) - amp, c))
                k += 1
        d += timedelta(days=1)
    return out


def daily_from(bars: List[dict]) -> List[dict]:
    days: Dict[str, dict] = {}
    for b in bars:
        d = b["date"][:10]
        if d not in days:
            days[d] = bar(d, b["o"], b["h"], b["l"], b["c"])
        else:
            days[d]["h"] = max(days[d]["h"], b["h"])
            days[d]["l"] = min(days[d]["l"], b["l"])
            days[d]["c"] = b["c"]
    return [days[k] for k in sorted(days)]


def meta(**kw) -> StrategyMeta:
    base = dict(id="s99_test", name="test", family="momentum", category="t",
                hypothesis="Test strategy used only by the harness tests to exercise the contract end to end.",
                markets=["stocks"], timeframes=["1hour"], hold="intraday", stop_method="atr",
                params={"a": 2, "b": 10}, param_grid={"a": [1, 2, 3], "b": [5, 10, 20]},
                max_hold_bars=5)
    base.update(kw)
    return StrategyMeta(**base)


def long_sig(entry: float, stop: float, t1: float, t2: float, trailing=None) -> Signal:
    return Signal("long", entry, stop, t1, t2, 60.0, ["test reason 1.0"], "closes under stop",
                  5, trailing=trailing, features={"x": 1})


def run(bars, sig_fn, m=None, start="2024-01-01", end="2025-12-31", **kw):
    daily = daily_from(bars)
    return B.replay(m or meta(), sig_fn, {"a": 2, "b": 10}, bars, daily, daily,
                    B.RegimeIndex(daily), "stocks", "TEST", "1hour", start, end, **kw)


# --------------------------------------------------------------- causality ----

def test_causality_spy_never_sees_future_bars():
    bars = make_series(40)
    seen: List[dict] = []

    def spy(ctx, cfg):
        cur = ctx["bars"][-1]
        assert all(b["time"] <= cur["time"] for b in ctx["bars"])
        assert all(d["date"][:10] < cur["date"][:10] for d in ctx["daily"])
        assert all(d["date"][:10] < cur["date"][:10] for d in ctx["spy_daily"])
        assert ctx["session"] in B.SESSIONS and ctx["regime"] in B.REGIMES
        seen.append({"t": cur["time"], "n": len(ctx["bars"])})
        if len(seen) % 40 == 0:
            return long_sig(cur["c"], cur["c"] - 1.0, cur["c"] + 2.0, cur["c"] + 4.0)
        return None

    out = run(bars, spy, lookback=50)
    times = [s["t"] for s in seen]
    assert times == sorted(times) and len(set(times)) == len(times), "each bar seen once, in order"
    assert set(times) <= {b["time"] for b in bars}
    assert max(s["n"] for s in seen) <= 50, "lookback window respected"
    assert out["trades"], "spy emitted trades"
    for t in out["trades"]:
        assert t["entry_idx"] == t["signal_idx"] + 1
        assert t["raw_entry"] == bars[t["entry_idx"]]["o"], "entry is the NEXT bar's open"
        assert t["entry"] > t["raw_entry"], "long fills above the raw open after slippage + half spread"
    # a bar inside an open trade is never asked for a signal
    busy = {j for t in out["trades"] for j in range(t["entry_idx"], t["exit_idx"])}
    assert not (busy & {bars.index(next(b for b in bars if b["time"] == t)) for t in times})


def test_signal_on_last_bar_has_no_entry():
    bars = make_series(3)
    sig = long_sig(100, 99, 102, 104)
    assert B.simulate_trade(bars, len(bars) - 1, sig) is None


def test_pair_bars_are_causal_and_absent_without_a_partner():
    bars = make_series(12)
    partner = make_series(12, price=50.0)
    seen: List[int] = []

    def spy(ctx, cfg):
        pb = ctx["pair_bars"]
        assert ctx["pair_symbol"] == "SPY" and pb
        assert all(b["time"] <= ctx["bars"][-1]["time"] for b in pb), "partner never runs ahead"
        assert pb[-1]["time"] == ctx["bars"][-1]["time"]
        seen.append(len(pb))
        return None

    daily = daily_from(bars)
    B.replay(meta(), spy, {}, bars, daily, daily, B.RegimeIndex(daily), "etf", "QQQ", "1hour",
             "2024-01-01", "2025-12-31", lookback=20, pair_bars=partner, pair_symbol="SPY")
    assert seen and max(seen) <= 20, "pair window bounded by lookback"

    def alone(ctx, cfg):
        assert "pair_bars" not in ctx and "pair_symbol" not in ctx
        return None

    out = B.replay(meta(), alone, {}, bars, daily, daily, B.RegimeIndex(daily), "etf", "SPY", "1hour",
                   "2024-01-01", "2025-12-31")
    assert out["counts"]["bars_evaluated"] == len(bars) and not out["errors"]
    assert B.PAIRS["QQQ"] == "SPY" and "SPY" not in B.PAIRS, "the base leg is never its own primary"
    assert B._pair_series("NOT_A_PAIR", "1hour", False) == (None, [])


# --------------------------------------------------------- ambiguity / exits ----

def test_same_bar_stop_and_target_is_scored_at_stop():
    bars = [bar("2024-03-04 09:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 10:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 11:30:00", 100, 103, 98, 101)]       # touches 102 AND 99
    t = B.simulate_trade(bars, 1, long_sig(100, 99, 102, 104))
    assert t["exit_reason"] == "AMBIGUOUS" and t["result"] == "LOSS"
    assert t["legs"][0][1] == 99 and t["r_multiple"] < -1.0  # stop fill plus costs


def test_scale_out_then_breakeven_and_time_stop():
    bars = [bar("2024-03-04 09:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 10:30:00", 100, 100.5, 99.5, 100),   # entry bar
            bar("2024-03-04 11:30:00", 100, 102.5, 99.6, 102),   # target1 only
            bar("2024-03-04 12:30:00", 102, 102.9, 99.9, 100.5)] # dips to breakeven stop
    t = B.simulate_trade(bars, 0, long_sig(100, 99, 102, 104), max_hold=10)
    assert [leg[2] for leg in t["legs"]] == ["TARGET1", "BREAKEVEN"]
    assert t["t1_hit"] and t["result"] == "WIN" and 0 < t["r_multiple"] < 1.0
    m = meta(max_hold_bars=2)
    bars2 = make_series(3)
    t2 = B.simulate_trade(bars2, 0, long_sig(100, 90, 150, 160), max_hold=m.max_hold_bars)
    assert t2["exit_reason"] == "TIME" and t2["bars_held"] == 2 and t2["exit_idx"] == 2


def test_trailing_pct_ratchets_and_exits_above_initial_stop():
    bars = [bar("2024-03-04 09:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 10:30:00", 100, 101, 99.5, 100.5),
            bar("2024-03-04 11:30:00", 100.5, 110, 100.4, 109),   # high-water 110
            bar("2024-03-04 12:30:00", 109, 109.5, 105, 106)]     # 110*(1-0.03)=106.7 is hit
    t = B.simulate_trade(bars, 0, long_sig(100, 95, 200, 300, trailing={"type": "pct", "pct": 3.0}))
    assert t["exit_reason"] == "TRAIL" and t["result"] == "WIN"
    assert t["legs"][0][1] == pytest.approx(106.7)


def test_gap_past_stop_is_skipped_not_traded():
    bars = [bar("2024-03-04 09:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 10:30:00", 97, 98, 96, 97)]
    t = B.simulate_trade(bars, 0, long_sig(100, 99, 102, 104))
    assert t["result"] == "SKIPPED"


# ------------------------------------------------------------------- costs ----

def test_costs_applied_on_both_sides():
    bars = [bar("2024-03-04 09:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 10:30:00", 100, 100.5, 99.5, 100),
            bar("2024-03-04 11:30:00", 100, 102.5, 99.5, 102)]
    t = B.simulate_trade(bars, 0, long_sig(100, 99, 102, 104), exit_model="t1_full")
    entry = 100 * (1 + 0.0005 + 0.0005)              # slippage + half of 0.1% spread
    fill = 102 * (1 - 0.0005 - 0.0005)
    net = fill - entry - 0.0002 * (entry + fill)     # commission both sides
    assert t["entry"] == pytest.approx(entry)
    assert t["exit_price"] == pytest.approx(fill)
    assert t["r_multiple"] == pytest.approx(net / (entry - 99))
    assert t["r_multiple"] < t["planned_rr"] == pytest.approx(2.0)
    assert t["cost_pct"] > 0
    low = B.DEFAULT_COSTS.per_side(4.0)
    assert low == (0.004, 0.005, 0.0002), "sub-$5 names pay 0.4% slippage and a 1% spread"
    stressed = B.pnl_under(t, B.DEFAULT_COSTS.scaled(2.0))
    assert stressed["r_multiple"] < t["r_multiple"]


# ------------------------------------------------------------------ splits ----

def test_split_boundaries():
    assert B.split_of("2024-01-01") == "train" and B.split_of("2024-12-31 15:30:00") == "train"
    assert B.split_of("2025-01-01") == "validation" and B.split_of("2025-04-30") == "validation"
    assert B.split_of("2025-05-01") == "oos" and B.split_of("2025-08-29") == "oos"
    assert B.split_of("2026-09-01") == "forward" and B.split_of("2026-09-03") == "forward"
    assert B.split_of("2025-09-15") is None and B.split_of("2023-12-29") is None


def test_replay_only_signals_inside_range_and_labels_split():
    bars = make_series(20, start="2024-12-16")               # spans the train/validation boundary
    calls: List[str] = []

    def every_bar(ctx, cfg):
        calls.append(ctx["bars"][-1]["date"])
        c = ctx["bars"][-1]["c"]
        return long_sig(c, c - 1, c + 1, c + 2)

    out = run(bars, every_bar, meta(max_hold_bars=1), start="2025-01-01", end="2025-01-07")
    assert min(calls)[:10] >= "2025-01-01" and max(calls)[:10] <= "2025-01-07"
    assert out["trades"] and all(t["split"] == "validation" for t in out["trades"])
    assert all("2025-01-01" <= t["signal_date"][:10] <= "2025-01-07" for t in out["trades"])


# --------------------------------------------------------------- composite ----

def _row(**kw):
    base = {"expectancy_r": 0.2, "profit_factor": 1.5, "max_drawdown_r": 4.0, "sortino": 0.8,
            "consistency": 0.6, "wilson_lb": 0.4, "win_rate": 0.5}
    base.update(kw)
    return base


def test_composite_excludes_win_rate():
    assert "win_rate" not in B.COMPOSITE_COMPONENTS
    a, b = _row(win_rate=0.9), _row(win_rate=0.1)
    sa, sb = B.composite_scores([a, b])
    assert sa == pytest.approx(sb), "identical components, different win rate -> identical score"
    better = _row(expectancy_r=0.5, profit_factor=2.0, max_drawdown_r=2.0, win_rate=0.2)
    worse = _row(win_rate=0.95)
    sc = B.composite_scores([better, worse])
    assert sc[0] > sc[1]
    assert all(0.0 < s < 1.0 for s in sc)
    # a missing component ranks last, never crashes
    assert len(B.composite_scores([_row(sortino=None), _row()])) == 2


# ----------------------------------------------------------------- metrics ----

def test_metrics_and_monte_carlo_shapes():
    bars = make_series(60, drift=0.05)

    def periodic(ctx, cfg):
        c = ctx["bars"][-1]["c"]
        return long_sig(c, c - 1.5, c + 1.0, c + 2.0) if len(ctx["bars"]) % 9 == 0 else None

    out = run(bars, periodic, meta(max_hold_bars=6))
    m = B.metrics(out["trades"])
    assert m["n"] >= 20 and m["wins"] + m["losses"] + m["breakeven"] == m["n"]
    assert 0 <= m["win_rate"] <= 1 and m["wilson_lb"] <= m["win_rate"]
    assert m["max_drawdown_r"] >= 0 and m["avg_rr"] == pytest.approx(1 / 1.5, rel=1e-6)
    assert set(B.breakdown(out["trades"], "session")) <= set(B.SESSIONS)
    mc = B.monte_carlo(out["trades"], n_iter=200)
    assert set(mc["drawdown_r"]) == {"p5", "p50", "p95"} and mc["drawdown_r"]["p5"] <= mc["drawdown_r"]["p95"]
    s = mc["stress"]
    assert s["base"]["expectancy_r"] >= s["slip_x1.5"]["expectancy_r"] >= s["slip_x2"]["expectancy_r"]
    assert s["slip_x1.5"]["expectancy_p5"] <= s["slip_x1.5"]["expectancy_p50"]
    assert mc["winrate_minus_10pp"]["expectancy_r"] <= s["base"]["expectancy_r"]


def test_grid_selection_and_robustness():
    m = meta()
    cfgs = B.grid_configs(m)
    assert len(cfgs) == 9 and {"a": 2, "b": 10} in cfgs
    assert len(B.quick_configs(m)) == 5
    good = {"n": 40, "expectancy_r": 0.3}
    bad = {"n": 40, "expectancy_r": -0.2}
    train = {B.cfg_key(c): (good if c["a"] >= 2 else bad) for c in cfgs}
    val = {B.cfg_key(c): {"n": 15, "expectancy_r": c["b"] / 100.0} for c in cfgs}
    chosen = B.select_params(cfgs, train, val)
    assert chosen["a"] >= 2 and chosen["b"] == 20, "positive-train candidates, best validation wins"
    assert B.robustness({"a": 2, "b": 10}, m, train) == pytest.approx(3 / 4)   # a=1 neighbour is negative
    assert B.robustness({"a": 3, "b": 20}, m, train) == pytest.approx(1.0)


def test_stage_rules():
    ok_mc = {"stress": {"slip_x1.5": {"expectancy_p5": 0.05}}}
    assert B.decide_stage({"n": 10, "expectancy_r": 0.1}, {}, {}, None, None)[0] == "BACKTESTING"
    assert B.decide_stage({"n": 30, "expectancy_r": 0.1}, {}, {}, None, None)[0] == "VALIDATION"
    assert B.decide_stage({"n": 60, "expectancy_r": 0.2}, {"n": 25, "expectancy_r": 0.1},
                          {"n": 25, "expectancy_r": 0.1}, 0.75, ok_mc)[0] == "PAPER_TRADING"
    assert B.decide_stage({"n": 60, "expectancy_r": 0.2}, {"n": 25, "expectancy_r": 0.1},
                          {"n": 25, "expectancy_r": 0.1}, 0.5, ok_mc)[0] == "VALIDATION"
    assert B.decide_stage({"n": 60, "expectancy_r": 0.2}, {"n": 25, "expectancy_r": 0.1},
                          {"n": 35, "expectancy_r": -0.2}, 0.9, ok_mc)[0] == "FAILED"
    assert B.decide_stage({}, {}, {}, None, None, has_run=False)[0] == "RESEARCH"
    assert B.stage_from_paper(50, 0.1) == "PROMISING" and B.stage_from_paper(100, 0.1) == "PRODUCTION_CANDIDATE"
    assert B.stage_from_paper(120, -0.1) is None and B.stage_from_paper(10, 0.5) is None
    assert B.best_stage(["FAILED", "VALIDATION", "BACKTESTING"]) == "VALIDATION"
    assert B.best_stage(["FAILED"]) == "FAILED"


def test_regime_session_and_resample():
    up = daily_from(make_series(80, drift=0.2))
    assert B.regime_labels(up)[-1] in ("trend_up", "high_vol", "low_vol")
    down = daily_from(make_series(80, price=300, drift=-0.4))
    assert B.regime_labels(down)[-1] in ("bear", "trend_down", "high_vol")
    idx = B.RegimeIndex(up)
    assert idx.at("2000-01-01") == "range", "no prior SPY bar -> neutral"
    assert idx.at(up[-1]["date"]) == B.regime_labels(up)[-2], "uses the bar BEFORE the session"
    assert [B.session_of(m, "stocks") for m in (300, 570, 629, 630, 839, 840, 959, 960)] == \
        ["premarket", "open", "open", "midday", "midday", "power_hour", "power_hour", "afterhours"]
    assert B.session_of(600, "crypto") == "crypto" and B.session_of(960, "stocks", "1day") == "daily"
    five = [bar(f"2026-09-01 09:{mm:02d}:00", 10 + k, 11 + k, 9 + k, 10.5 + k, 100)
            for k, mm in enumerate((30, 35, 40, 45, 50, 55))]
    fifteen = B.resample(five, 15)
    assert len(fifteen) == 2 and fifteen[0]["o"] == 10 and fifteen[0]["h"] == 13
    assert fifteen[0]["l"] == 9 and fifteen[0]["c"] == 12.5 and fifteen[0]["v"] == 300
    assert fifteen[1]["date"] == "2026-09-01 09:45:00"
    assert B.market_of("BTCUSD") == "crypto" and B.market_of("SPY") == "etf" and B.market_of("AAPL") == "stocks"


def test_short_rejected_for_stocks_and_bad_levels_invalid():
    ok = long_sig(100, 99, 102, 104)
    assert B.validate_signal(ok, "stocks") is None
    short = Signal("short", 100, 101, 98, 96, 50, ["r"], "inv", 3)
    assert B.validate_signal(short, "stocks") == "short_rejected"
    assert B.validate_signal(short, "crypto") is None
    assert B.validate_signal(long_sig(100, 101, 102, 104), "stocks") == "invalid"   # stop above entry
    assert B.validate_signal(long_sig(100, 99, 104, 102), "stocks") == "invalid"    # t2 not beyond t1


# ---------------------------------------------------------------- registry ----

def test_registry_skips_broken_modules(tmp_path, monkeypatch):
    pkg = tmp_path / "labtmp_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    good = textwrap.dedent('''
        from app.lab.base import StrategyMeta, Signal
        META = StrategyMeta(id="s01_good", name="Good", family="trend", category="c",
            hypothesis="Buyers who missed a breakout chase the first pullback because benchmark tracking forces them in; fails if pullbacks keep extending.",
            markets=["stocks"], timeframes=["1hour"], hold="intraday", stop_method="atr",
            params={"n": 10}, param_grid={"n": [5, 10, 20]})
        def signal(ctx, cfg):
            return None
    ''')
    (pkg / "s01_good.py").write_text(good)
    (pkg / "s02_broken.py").write_text("import definitely_not_a_module\nMETA = None\n")
    (pkg / "s03_mismatch.py").write_text(good.replace('id="s01_good"', 'id="s03_other"'))
    (pkg / "_helper.py").write_text("raise RuntimeError('never imported')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(registry, "STRATEGIES_DIR", str(pkg))
    monkeypatch.setattr(registry, "PACKAGE", "labtmp_pkg")
    loaded, skipped = registry.load_report()
    assert [s.id for s in loaded] == ["s01_good"]
    assert "import failed" in skipped["s02_broken"]
    assert "filename stem" in skipped["s03_mismatch"]
    assert "_helper" not in skipped, "non-strategy helpers are never imported"
    s = registry.load("s01_good")
    assert s is not None and s.meta.family == "trend" and s.signal({}, {}) is None
    assert registry.load("s09_missing") is None
    with pytest.raises(Exception):
        registry.load_all(strict=True)


def test_strategy_exception_is_recorded_not_raised():
    bars = make_series(5)

    def boom(ctx, cfg):
        raise ValueError("bad indicator")

    out = run(bars, boom, max_errors=3)
    assert out["trades"] == [] and len(out["errors"]) == 3 and out["counts"]["aborted"] == 1


def test_zero_signals_on_regular_hours_only_data_is_not_called_a_gate_bug():
    """s04 and s17 define their entry from the premarket high, and the cached
    2024-2025 intraday history starts at 09:30, so they cannot fire there.
    Reporting that as "audit the gates" points at a bug that does not exist."""
    import inspect
    from app.lab import backtest as B
    src = inspect.getsource(B)
    i = src.index('if loud:')
    block = src[i:i + 1400]
    assert 'premarket_bars' in block
    assert 'not evidence about the strategy' in block
    assert 'audit ' in block          # the real gate warning still exists
