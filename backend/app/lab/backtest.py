"""Shared backtest harness for the Quant Lab.

Every strategy is replayed the same way, so results are comparable:

  * bar-by-bar: signal(ctx, cfg) sees ctx["bars"] = series[:i+1] (bounded to a
    trailing lookback window) and never a bar after i; daily / spy_daily hold
    prior sessions only.
  * entry at the NEXT bar's open, with slippage + half the spread proxy against
    us on every fill and commission on both sides.
  * stops / targets are evaluated on subsequent bar highs and lows; a bar that
    touches both stop and target is scored at the stop — never a win.
  * exit model "scale_out": half the position leaves at target1 and the stop
    moves to breakeven for the remainder (applied from the next bar), which then
    exits at target2, the (trailing) stop, or the time stop. "t1_full" exits
    everything at target1.
  * chronological splits by signal date; the parameter grid is swept on TRAIN
    only, the configuration is chosen on VALIDATION, OOS is run once with the
    chosen configuration, and the Sep-2026 movers set is a separate FORWARD
    split.

Nothing here touches the network: bars come from the on-disk rev_cache.
"""
from __future__ import annotations

import bisect
import functools
import itertools
import json
import logging
import math
import pathlib
import random
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ..strategy.indicators import atr_series, percentile_rank, pivots
from .base import STAGES, Signal, StrategyMeta

log = logging.getLogger("lab.backtest")

CACHE_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "rev_cache"

STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "JPM", "XOM",
          "UNH", "V", "WMT", "ROKU", "DKNG", "PINS", "PLTR", "SOFI", "RIVN",
          "MARA", "RIOT", "PLUG", "LCID"]
ETFS = ["SPY", "QQQ", "IWM", "XLE", "XLF", "SMH"]
CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD"]
UNIVERSE: Dict[str, List[str]] = {"stocks": STOCKS, "etf": ETFS, "crypto": CRYPTO}
QUICK_SYMBOLS = ["AAPL", "NVDA", "SPY", "QQQ", "BTCUSD", "ETHUSD"]

# Spread partners for the pairs strategies: primary -> partner. When the partner
# series is cached for the same timeframe it is handed to signal() as
# ctx["pair_bars"] (cut at the current bar, never beyond) with ctx["pair_symbol"];
# a primary without a cached partner simply gets no pair keys, so a pairs
# strategy returns None there instead of guessing. XOP / GDX / GLD are listed
# for completeness but are not on the cache today, so XLE has no partner yet.
PAIRS: Dict[str, str] = {"QQQ": "SPY", "SMH": "QQQ", "IWM": "SPY", "XLE": "XOP", "GDX": "GLD",
                         "ETHUSD": "BTCUSD", "SOLUSD": "ETHUSD"}

TIMEFRAMES = ["5min", "15min", "30min", "1hour", "4hour", "1day"]
TF_MINUTES = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}

SPLITS: Dict[str, Tuple[str, str]] = {
    "train": ("2024-01-01", "2024-12-31"),
    "validation": ("2025-01-01", "2025-04-30"),
    "oos": ("2025-05-01", "2025-08-29"),
    "forward": ("2026-09-01", "2026-09-03"),
}
SWEEP_RANGE = (SPLITS["train"][0], SPLITS["validation"][1])
REGIMES = ["trend_up", "trend_down", "range", "high_vol", "low_vol", "bear"]
SESSIONS = ["premarket", "open", "midday", "power_hour", "afterhours", "crypto", "daily"]
RESOLVED = ("WIN", "LOSS", "BREAKEVEN")


# ------------------------------------------------------------------ costs ----

@dataclass(frozen=True)
class CostModel:
    """Per-side frictions as percentages of price. The spread proxy is a full
    round-trip spread; half of it is charged on each fill."""
    slippage_pct: float = 0.05
    slippage_pct_low: float = 0.4
    commission_pct: float = 0.02
    spread_pct: float = 0.1
    spread_pct_low: float = 1.0
    low_price: float = 5.0
    slippage_mult: float = 1.0

    def per_side(self, price: float) -> Tuple[float, float, float]:
        """(slippage, half_spread, commission) as fractions for one fill at `price`."""
        low = price < self.low_price
        slip = (self.slippage_pct_low if low else self.slippage_pct) * self.slippage_mult / 100.0
        spread = (self.spread_pct_low if low else self.spread_pct) / 100.0
        return slip, spread / 2.0, self.commission_pct / 100.0

    def scaled(self, slippage_mult: float) -> "CostModel":
        return replace(self, slippage_mult=slippage_mult)

    def as_dict(self) -> Dict[str, Any]:
        return {"slippage_pct_per_side": self.slippage_pct,
                "slippage_pct_per_side_below_5": self.slippage_pct_low,
                "commission_pct_per_side": self.commission_pct,
                "spread_proxy_pct_round_trip": self.spread_pct,
                "spread_proxy_pct_round_trip_below_5": self.spread_pct_low,
                "slippage_mult": self.slippage_mult}


DEFAULT_COSTS = CostModel()


# ------------------------------------------------------------------- data ----

def market_of(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USD"):
        return "crypto"
    if s in ETFS:
        return "etf"
    return "stocks"


def split_of(date_str: str) -> Optional[str]:
    d = date_str[:10]
    for name, (a, b) in SPLITS.items():
        if a <= d <= b:
            return name
    return None


def session_of(minute_of_day: int, market: str, timeframe: str = "5min") -> str:
    if market == "crypto":
        return "crypto"
    if timeframe == "1day":
        return "daily"           # a daily bar spans every session bucket
    m = minute_of_day
    if m < 570:
        return "premarket"
    if m < 630:
        return "open"
    if m < 840:
        return "midday"
    if m < 960:
        return "power_hour"
    return "afterhours"


def to_engine_bars(rows: Sequence[dict]) -> List[dict]:
    """rev_cache rows -> engine bars {o,h,l,c,v,time,minute_of_day,date}.
    minute_of_day is ET wall-clock from the provider's date string; a daily
    bar is stamped 960 (complete at the 16:00 close)."""
    out = []
    for r in rows:
        ds = str(r["date"])
        mod = int(ds[11:13]) * 60 + int(ds[14:16]) if len(ds) > 10 else 960
        out.append({"o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]),
                    "c": float(r["c"]), "v": float(r.get("v") or 0.0),
                    "time": int(r["time"]), "minute_of_day": mod, "date": ds})
    return out


def read_cache(symbol: str, interval: str, extended: bool = False,
               start: Optional[str] = None, end: Optional[str] = None) -> List[dict]:
    """Ascending raw rows from the on-disk cache, or [] when the file is absent.
    Never fetches."""
    key = f"{interval}_ext" if (extended and interval != "1day") else interval
    p = CACHE_DIR / f"{symbol.upper()}_{key}.json"
    if not p.exists():
        return []
    try:
        blob = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    rows = sorted(blob.get("bars", {}).values(), key=lambda b: b["time"])
    if start:
        rows = [r for r in rows if r["date"][:10] >= start]
    if end:
        rows = [r for r in rows if r["date"][:10] <= end]
    return rows


def has_cache(symbol: str, interval: str, extended: bool = False) -> bool:
    key = f"{interval}_ext" if (extended and interval != "1day") else interval
    return (CACHE_DIR / f"{symbol.upper()}_{key}.json").exists()


def available_symbols(market: str, timeframe: str,
                      only: Optional[Sequence[str]] = None) -> List[str]:
    syms = UNIVERSE.get(market, [])
    if only is not None:
        syms = [s for s in syms if s in set(only)]
    return [s for s in syms if has_cache(s, timeframe)]


def forward_symbols() -> List[str]:
    """Movers captured at 5min extended-hours for the forward split."""
    return sorted(p.name[:-len("_5min_ext.json")] for p in CACHE_DIR.glob("*_5min_ext.json"))


def resample(bars: Sequence[dict], minutes: int) -> List[dict]:
    """Aggregate intraday engine bars into `minutes` buckets aligned to midnight
    ET within each session date. A bucket is emitted once all its source bars
    have passed, so nothing inside it is known early."""
    out: List[dict] = []
    cur: Optional[dict] = None
    cur_key: Optional[Tuple[str, int]] = None
    for b in bars:
        key = (b["date"][:10], (b["minute_of_day"] // minutes) * minutes)
        if key != cur_key:
            if cur is not None:
                out.append(cur)
            hh, mm = divmod(key[1], 60)
            cur = {"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"],
                   "time": b["time"], "minute_of_day": key[1],
                   "date": f"{key[0]} {hh:02d}:{mm:02d}:00"}
            cur_key = key
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b["v"]
    if cur is not None:
        out.append(cur)
    return out


# ----------------------------------------------------------------- regime ----

def regime_labels(spy_daily: Sequence[dict]) -> List[str]:
    """Regime at each SPY daily bar using only bars[:i+1].
    bear: SMA20 < SMA50, close < SMA50 and >8% under the 60-session high;
    high_vol / low_vol: ATR14/close in the top / bottom quintile of the trailing
    year; trend_up: close > SMA20 > SMA50; trend_down: the mirror; else range.
    Precedence: bear > high_vol > trend_up > trend_down > low_vol > range."""
    closes = [float(b["c"]) for b in spy_daily]
    atrs = atr_series(spy_daily, 14)
    atr_pct = [(a / c) if (a and c) else None for a, c in zip(atrs, closes)]
    labels: List[str] = []
    for i, c in enumerate(closes):
        if i < 49:
            labels.append("range")
            continue
        sma20 = sum(closes[i - 19:i + 1]) / 20.0
        sma50 = sum(closes[i - 49:i + 1]) / 50.0
        hi60 = max(closes[max(0, i - 59):i + 1])
        window = [x for x in atr_pct[max(0, i - 251):i + 1] if x is not None]
        pct = percentile_rank(window, atr_pct[i]) if atr_pct[i] is not None else None
        if c < sma50 and sma20 < sma50 and c < hi60 * 0.92:
            labels.append("bear")
        elif pct is not None and pct >= 80:
            labels.append("high_vol")
        elif c > sma20 > sma50:
            labels.append("trend_up")
        elif c < sma20 < sma50:
            labels.append("trend_down")
        elif pct is not None and pct <= 20:
            labels.append("low_vol")
        else:
            labels.append("range")
    return labels


class RegimeIndex:
    """Regime for any session date from the last SPY daily bar BEFORE it."""

    def __init__(self, spy_daily: Sequence[dict]):
        self.dates = [b["date"][:10] for b in spy_daily]
        self.labels = regime_labels(spy_daily)

    def at(self, session_date: str) -> str:
        k = bisect.bisect_left(self.dates, session_date[:10])
        return self.labels[k - 1] if k > 0 else "range"


# --------------------------------------------------------------- simulate ----

def validate_signal(sig: Any, market: str) -> Optional[str]:
    """None when the signal is well-formed for this market, else the reason."""
    if not isinstance(sig, Signal):
        return "invalid"
    if sig.direction not in ("long", "short"):
        return "invalid"
    if sig.direction == "short" and market not in ("crypto", "etf"):
        return "short_rejected"
    try:
        levels = [float(sig.entry), float(sig.stop), float(sig.target1), float(sig.target2)]
    except (TypeError, ValueError):
        return "invalid"
    if any((not math.isfinite(x)) or x <= 0 for x in levels):
        return "invalid"
    side = 1.0 if sig.direction == "long" else -1.0
    entry, stop, t1, t2 = levels
    if side * (entry - stop) <= 0 or side * (t1 - entry) <= 0 or side * (t2 - t1) <= 0:
        return "invalid"
    return None


def _trail_level(trailing: Dict[str, Any], bars: Sequence[dict], j: int, long: bool,
                 hi_water: float, lo_water: float, atr14: Optional[Sequence[Optional[float]]]
                 ) -> Optional[float]:
    kind = str(trailing.get("type", "")).lower()
    if kind == "atr":
        a = atr14[j] if (atr14 is not None and j < len(atr14)) else None
        if not a:
            return None
        mult = float(trailing.get("mult", 2.0))
        return hi_water - mult * a if long else lo_water + mult * a
    if kind == "pct":
        p = float(trailing.get("pct", 3.0)) / 100.0
        return hi_water * (1 - p) if long else lo_water * (1 + p)
    if kind == "swing":
        highs, lows = pivots(bars[max(0, j - 60):j + 1], 3, 3)
        if long:
            return lows[-1][1] if lows else None
        return highs[-1][1] if highs else None
    return None


def simulate_trade(bars: Sequence[dict], i: int, sig: Signal, costs: CostModel = DEFAULT_COSTS,
                   max_hold: int = 78, exit_model: str = "scale_out",
                   atr14: Optional[Sequence[Optional[float]]] = None) -> Optional[dict]:
    """Resolve a signal confirmed at bar i. Entry is bar i+1's open. Returns
    None when no next bar exists; result SKIPPED when the open gapped past the
    stop or target1; OPEN when data ends before the trade resolves."""
    n = len(bars)
    e = i + 1
    if e >= n:
        return None
    long = sig.direction == "long"
    side = 1.0 if long else -1.0
    raw_open = float(bars[e]["o"])
    slip, half_spread, comm = costs.per_side(raw_open)
    entry = raw_open * (1 + side * (slip + half_spread))
    stop0 = float(sig.stop)
    t1, t2 = float(sig.target1), float(sig.target2)
    risk = side * (entry - stop0)
    base = {"direction": sig.direction, "signal_idx": i, "entry_idx": e,
            "signal_time": int(bars[i]["time"]), "entry_time": int(bars[e]["time"]),
            "raw_entry": raw_open, "entry": entry, "stop": stop0, "target1": t1,
            "target2": t2, "planned_rr": sig.rr1, "expected_bars": sig.expected_bars,
            "trailing": sig.trailing}
    if risk <= 0 or side * (t1 - entry) <= 0:
        return {**base, "result": "SKIPPED", "exit_reason": "GAP_PAST_LEVEL"}

    legs: List[List[Any]] = []            # [fraction, raw_exit, reason]
    remaining, stop, t1_done = 1.0, stop0, False
    mfe = mae = 0.0
    hi_water = lo_water = raw_open
    exit_idx: Optional[int] = None
    last = min(n - 1, e + max_hold - 1)
    for j in range(e, last + 1):
        b = bars[j]
        hi, lo = float(b["h"]), float(b["l"])
        mfe = max(mfe, side * ((hi if long else lo) - entry) / risk)
        mae = min(mae, side * ((lo if long else hi) - entry) / risk)
        hit_stop = lo <= stop if long else hi >= stop
        if not t1_done:
            hit_t1 = hi >= t1 if long else lo <= t1
            if hit_stop and hit_t1:
                legs.append([remaining, stop, "AMBIGUOUS"]); exit_idx = j; break
            if hit_stop:
                legs.append([remaining, stop, "TRAIL" if stop != stop0 else "STOP"]); exit_idx = j; break
            if hit_t1:
                if exit_model == "t1_full":
                    legs.append([remaining, t1, "TARGET1"]); exit_idx = j; break
                legs.append([remaining / 2.0, t1, "TARGET1"])
                remaining /= 2.0
                t1_done = True                      # breakeven stop applies from next bar
        else:
            hit_t2 = hi >= t2 if long else lo <= t2
            be = abs(stop - entry) < 1e-12
            reason = "AMBIGUOUS" if (hit_stop and hit_t2) else (
                "BREAKEVEN" if be else ("TRAIL" if stop != stop0 else "STOP"))
            if hit_stop:
                legs.append([remaining, stop, reason]); exit_idx = j; break
            if hit_t2:
                legs.append([remaining, t2, "TARGET2"]); exit_idx = j; break
        # end of bar: ratchet the stop for the NEXT bar (never within this one)
        hi_water, lo_water = max(hi_water, hi), min(lo_water, lo)
        if t1_done and exit_model != "t1_full":
            stop = max(stop, entry) if long else min(stop, entry)
        if sig.trailing:
            tl = _trail_level(sig.trailing, bars, j, long, hi_water, lo_water, atr14)
            close = float(b["c"])
            if tl is not None and (tl < close if long else tl > close):
                stop = max(stop, tl) if long else min(stop, tl)
    if exit_idx is None:
        if last == e + max_hold - 1:
            legs.append([remaining, float(bars[last]["c"]), "TIME"]); exit_idx = last
        else:
            return {**base, "result": "OPEN", "exit_reason": "", "exit_idx": None,
                    "mfe_r": mfe, "mae_r": mae, "legs": legs}

    trade = {**base, "legs": legs, "exit_idx": exit_idx,
             "exit_time": int(bars[exit_idx]["time"]), "exit_reason": legs[-1][2],
             "t1_hit": t1_done or legs[-1][2] == "TARGET1",
             "bars_held": exit_idx - e + 1, "mfe_r": mfe, "mae_r": mae}
    trade.update(pnl_under(trade, costs))
    return trade


def pnl_under(trade: dict, costs: CostModel) -> Dict[str, Any]:
    """Re-price a resolved trade's path under a cost model. The path (which
    levels were touched) never depends on costs, so stress tests reuse it."""
    long = trade["direction"] == "long"
    side = 1.0 if long else -1.0
    raw_open = trade["raw_entry"]
    slip, half_spread, comm = costs.per_side(raw_open)
    entry = raw_open * (1 + side * (slip + half_spread))
    risk = side * (entry - trade["stop"])
    net = gross = fills = 0.0
    for frac, raw_exit, _ in trade["legs"]:
        s2, hs2, c2 = costs.per_side(raw_exit)
        fill = raw_exit * (1 - side * (s2 + hs2))
        fills += frac * fill
        gross += frac * side * (raw_exit - raw_open)
        net += frac * (side * (fill - entry) - comm * entry - c2 * fill)
    r = net / risk if risk > 0 else 0.0
    result = "WIN" if net > 1e-12 else ("LOSS" if net < -1e-12 else "BREAKEVEN")
    return {"entry": entry, "exit_price": fills, "r_multiple": r,
            "return_pct": net / raw_open * 100.0, "cost_pct": (gross - net) / raw_open * 100.0,
            "risk": risk, "result": result}


# ----------------------------------------------------------------- replay ----

def replay(meta: StrategyMeta, signal_fn: Callable, cfg: Dict[str, Any], bars: Sequence[dict],
           daily: Sequence[dict], spy_daily: Sequence[dict], regimes: Optional[RegimeIndex],
           market: str, symbol: str, timeframe: str, start: str, end: str,
           costs: CostModel = DEFAULT_COSTS, lookback: int = 800, daily_lookback: int = 400,
           exit_model: str = "scale_out", atr14: Optional[Sequence[Optional[float]]] = None,
           max_errors: int = 20, pair_bars: Optional[Sequence[dict]] = None,
           pair_symbol: Optional[str] = None) -> Dict[str, Any]:
    """Bar-by-bar replay of one series. Signals are taken only on bars whose
    session date lies in [start, end]; earlier bars are history. One position
    at a time per series; a bar in a trade is never asked for a new signal.
    `pair_bars` (a spread partner's series on the same timeframe) is exposed as
    ctx["pair_bars"] cut at the current bar's timestamp, bounded by lookback."""
    counts: Counter = Counter()
    errors: List[str] = []
    trades: List[dict] = []
    if not bars:
        return {"trades": trades, "counts": dict(counts), "errors": errors, "bars": 0}
    sdates = [b["date"][:10] for b in bars]
    d_dates = [b["date"][:10] for b in daily]
    s_dates = [b["date"][:10] for b in spy_daily]
    p_times = [int(b["time"]) for b in pair_bars] if pair_bars else None
    i0, i1 = bisect.bisect_left(sdates, start), bisect.bisect_right(sdates, end)
    regimes_on = set(meta.regimes_on) if meta.regimes_on else None
    max_hold = max(1, int(meta.max_hold_bars or 78))
    i = i0
    while i < i1:
        sd = sdates[i]
        regime = regimes.at(sd) if regimes is not None else "range"
        if regimes_on and regime not in regimes_on:
            counts["regime_filtered"] += 1
            i += 1
            continue
        if timeframe == "1day":
            dprior = bars[max(0, i - daily_lookback):i]
        else:
            k = bisect.bisect_left(d_dates, sd)
            dprior = daily[max(0, k - daily_lookback):k]
        ks = bisect.bisect_left(s_dates, sd)
        ctx = {"bars": bars[max(0, i + 1 - lookback):i + 1], "daily": dprior,
               "spy_daily": spy_daily[max(0, ks - daily_lookback):ks], "regime": regime,
               "session": session_of(bars[i]["minute_of_day"], market, timeframe),
               "market": market, "symbol": symbol, "timeframe": timeframe}
        if p_times is not None:                       # partner bars at or before this bar only
            kp = bisect.bisect_right(p_times, int(bars[i]["time"]))
            ctx["pair_bars"] = pair_bars[max(0, kp - lookback):kp]
            ctx["pair_symbol"] = pair_symbol
        counts["bars_evaluated"] += 1
        try:
            sig = signal_fn(ctx, cfg)
        except Exception as exc:  # a strategy bug must not kill the run
            errors.append(f"{symbol} {timeframe} {bars[i]['date']}: {type(exc).__name__}: {exc}")
            if len(errors) >= max_errors:
                counts["aborted"] += 1
                break
            i += 1
            continue
        if sig is None:
            i += 1
            continue
        counts["signals"] += 1
        problem = validate_signal(sig, market)
        if problem:
            counts[problem] += 1
            i += 1
            continue
        if sig.trailing and str(sig.trailing.get("type", "")).lower() == "atr" and atr14 is None:
            atr14 = atr_series(bars, 14)
        tr = simulate_trade(bars, i, sig, costs, max_hold, exit_model, atr14)
        if tr is None:
            counts["no_next_bar"] += 1
            break
        if tr["result"] == "SKIPPED":
            counts["gap_skipped"] += 1
            i += 1
            continue
        tr.update({"strategy_id": meta.id, "symbol": symbol, "market": market,
                   "timeframe": timeframe, "regime": regime, "session": ctx["session"],
                   "signal_date": bars[i]["date"], "split": split_of(sd),
                   "confidence": float(sig.confidence), "reasons": list(sig.reasons or []),
                   "invalidation": sig.invalidation, "features": dict(sig.features or {}),
                   "params": dict(cfg)})
        trades.append(tr)
        if tr["result"] == "OPEN":
            counts["open_at_end"] += 1
            break
        i = tr["exit_idx"]                # flat at that bar's close; may signal again there
    return {"trades": trades, "counts": dict(counts), "errors": errors, "bars": i1 - i0}


# ---------------------------------------------------------------- metrics ----

def wilson_lb(wins: int, n: int, z: float = 1.96) -> Optional[float]:
    if n <= 0:
        return None
    ph = wins / n
    denom = 1 + z * z / n
    centre = ph + z * z / (2 * n)
    margin = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def max_drawdown_r(rs: Sequence[float]) -> float:
    """Largest peak-to-trough fall of the cumulative-R curve, as a positive magnitude."""
    eq = peak = mdd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def _sortino(rs: Sequence[float]) -> Optional[float]:
    if len(rs) < 2:
        return None
    dd = math.sqrt(sum(min(0.0, r) ** 2 for r in rs) / len(rs))
    return statistics.mean(rs) / dd if dd > 0 else None


def resolved(trades: Iterable[dict]) -> List[dict]:
    return sorted((t for t in trades if t.get("result") in RESOLVED),
                  key=lambda t: (t["signal_time"], t.get("symbol", "")))


def monthly_returns(trades: Sequence[dict]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for t in trades:
        m = out.setdefault(str(t["signal_date"])[:7], {"n": 0, "r": 0.0, "pct": 0.0})
        m["n"] += 1
        m["r"] += t["r_multiple"]
        m["pct"] += t["return_pct"]
    return {k: {"n": v["n"], "r": round(v["r"], 4), "pct": round(v["pct"], 4)}
            for k, v in sorted(out.items())}


def equity_curve(trades: Sequence[dict]) -> List[List[float]]:
    out, cum_r, cum_pct = [], 0.0, 0.0
    for t in trades:
        cum_r += t["r_multiple"]
        cum_pct += t["return_pct"]
        out.append([int(t["exit_time"]), round(cum_r, 4), round(cum_pct, 4)])
    return out


def metrics(trades: Iterable[dict]) -> Dict[str, Any]:
    """Per-trade statistics on RESOLVED trades, chronological by signal time."""
    res = resolved(trades)
    n = len(res)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "breakeven": 0, "ambiguous": 0,
                "win_rate": None, "avg_win_pct": None, "avg_loss_pct": None,
                "profit_factor": None, "expectancy_r": None, "expectancy_pct": None,
                "total_return_pct": 0.0, "total_r": 0.0, "max_drawdown_r": 0.0,
                "sharpe": None, "sortino": None, "avg_rr": None, "avg_hold_bars": None,
                "streaks": {"max_win": 0, "max_loss": 0}, "wilson_lb": None,
                "consistency": None, "months": 0, "avg_mfe_r": None, "avg_mae_r": None,
                "exit_reasons": {}, "first": None, "last": None}
    rs = [t["r_multiple"] for t in res]
    pcts = [t["return_pct"] for t in res]
    wins = [t for t in res if t["result"] == "WIN"]
    losses = [t for t in res if t["result"] == "LOSS"]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = -sum(r for r in rs if r < 0)
    if gross_loss > 0:
        pf: Optional[float] = min(gross_win / gross_loss, 100.0)
    else:
        pf = 100.0 if gross_win > 0 else None
    sw = sl = best_w = best_l = 0
    for t in res:
        if t["result"] == "WIN":
            sw, sl = sw + 1, 0
        elif t["result"] == "LOSS":
            sw, sl = 0, sl + 1
        else:
            sw = sl = 0
        best_w, best_l = max(best_w, sw), max(best_l, sl)
    sd = statistics.stdev(rs) if n > 1 else 0.0
    monthly = monthly_returns(res)
    pos_months = sum(1 for m in monthly.values() if m["r"] > 0)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "breakeven": n - len(wins) - len(losses),
        "ambiguous": sum(1 for t in res if t["exit_reason"] == "AMBIGUOUS"),
        "win_rate": len(wins) / n,
        "avg_win_pct": statistics.mean(t["return_pct"] for t in wins) if wins else 0.0,
        "avg_loss_pct": statistics.mean(t["return_pct"] for t in losses) if losses else 0.0,
        "profit_factor": pf,
        "expectancy_r": statistics.mean(rs),
        "expectancy_pct": statistics.mean(pcts),
        "total_return_pct": sum(pcts), "total_r": sum(rs),
        "max_drawdown_r": max_drawdown_r(rs),
        "sharpe": (statistics.mean(rs) / sd) if sd > 0 else None,
        "sortino": _sortino(rs),
        "avg_rr": statistics.mean(t["planned_rr"] for t in res),
        "avg_hold_bars": statistics.mean(t["bars_held"] for t in res),
        "streaks": {"max_win": best_w, "max_loss": best_l},
        "wilson_lb": wilson_lb(len(wins), n),
        "consistency": pos_months / len(monthly) if monthly else None,
        "months": len(monthly),
        "avg_mfe_r": statistics.mean(t["mfe_r"] for t in res),
        "avg_mae_r": statistics.mean(t["mae_r"] for t in res),
        "avg_cost_pct": statistics.mean(t["cost_pct"] for t in res),
        "t1_hit_rate": sum(1 for t in res if t.get("t1_hit")) / n,
        "exit_reasons": dict(Counter(t["exit_reason"] for t in res)),
        "first": res[0]["signal_date"], "last": res[-1]["signal_date"],
    }


COMPACT_KEYS = ("n", "wins", "losses", "win_rate", "expectancy_r", "expectancy_pct",
                "profit_factor", "total_r", "max_drawdown_r", "wilson_lb", "avg_hold_bars")


def compact(m: Dict[str, Any]) -> Dict[str, Any]:
    return {k: m.get(k) for k in COMPACT_KEYS}


def breakdown(trades: Iterable[dict], key: str) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[dict]] = {}
    for t in resolved(trades):
        groups.setdefault(str(t.get(key)), []).append(t)
    return {k: compact(metrics(v)) for k, v in sorted(groups.items())}


def full_report(trades: Iterable[dict]) -> Dict[str, Any]:
    res = resolved(trades)
    return {"metrics": metrics(res), "by_regime": breakdown(res, "regime"),
            "by_session": breakdown(res, "session"), "by_symbol": breakdown(res, "symbol"),
            "monthly": monthly_returns(res), "equity_curve": equity_curve(res)}


# --------------------------------------------------------- sweep / select ----

def cfg_key(cfg: Dict[str, Any]) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


def grid_configs(meta: StrategyMeta) -> List[Dict[str, Any]]:
    """Every point of META.param_grid merged over META.params defaults."""
    grid = meta.param_grid or {}
    if not grid:
        return [dict(meta.params)]
    names = sorted(grid)
    return [{**meta.params, **dict(zip(names, vals))}
            for vals in itertools.product(*(grid[k] for k in names))]


def neighbours(cfg: Dict[str, Any], meta: StrategyMeta) -> List[Dict[str, Any]]:
    """Grid points one step away in exactly one parameter."""
    out = []
    for name, values in (meta.param_grid or {}).items():
        if cfg.get(name) not in values:
            continue
        idx = values.index(cfg[name])
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(values):
                out.append({**cfg, name: values[j]})
    return out


def quick_configs(meta: StrategyMeta) -> List[Dict[str, Any]]:
    """Default point plus its one-step neighbourhood — the --quick sweep."""
    default = dict(meta.params)
    for name, values in (meta.param_grid or {}).items():
        if default.get(name) not in values:
            default[name] = values[len(values) // 2]
    seen, out = set(), []
    for c in [default] + neighbours(default, meta):
        k = cfg_key(c)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def robustness(cfg: Dict[str, Any], meta: StrategyMeta,
               train_by_key: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """Fraction of grid neighbours whose TRAIN expectancy is positive."""
    nbrs = neighbours(cfg, meta)
    if not nbrs:
        return None
    good = 0
    for nb in nbrs:
        m = train_by_key.get(cfg_key(nb)) or {}
        if (m.get("n") or 0) > 0 and (m.get("expectancy_r") or 0) > 0:
            good += 1
    return good / len(nbrs)


def select_params(cfgs: Sequence[Dict[str, Any]], train_by_key: Dict[str, Dict[str, Any]],
                  val_by_key: Dict[str, Dict[str, Any]], min_n: int = 10) -> Dict[str, Any]:
    """Candidates must have a positive TRAIN expectancy on >= min_n trades (else
    every config with trades; else the first config). Among candidates, the
    highest VALIDATION expectancy wins, preferring configs with >= min_n
    validation trades. Train never decides the winner directly."""
    def tm(c: Dict[str, Any]) -> Dict[str, Any]:
        return train_by_key.get(cfg_key(c)) or {}

    def vm(c: Dict[str, Any]) -> Dict[str, Any]:
        return val_by_key.get(cfg_key(c)) or {}

    cands = [c for c in cfgs if (tm(c).get("n") or 0) >= min_n and (tm(c).get("expectancy_r") or 0) > 0]
    if not cands:
        cands = [c for c in cfgs if (tm(c).get("n") or 0) > 0]
    if not cands:
        return dict(cfgs[0]) if cfgs else {}
    return dict(max(cands, key=lambda c: ((vm(c).get("n") or 0) >= min_n,
                                          vm(c).get("expectancy_r") if (vm(c).get("n") or 0) > 0
                                          else -math.inf,
                                          tm(c).get("expectancy_r") or 0.0)))


# ----------------------------------------------------------- monte carlo ----

def _pcts(vals: Sequence[float], qs: Sequence[float] = (5, 50, 95)) -> Dict[str, float]:
    s = sorted(vals)
    out = {}
    for q in qs:
        pos = (len(s) - 1) * q / 100.0
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        out[f"p{q}"] = s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return out


def monte_carlo(trades: Iterable[dict], costs: CostModel = DEFAULT_COSTS, n_iter: int = 1000,
                seed: int = 7) -> Dict[str, Any]:
    """Order shuffles for the drawdown distribution, bootstrap resamples for the
    expectancy distribution under base / x1.5 / x2 slippage, and a win-rate
    -10pp haircut (random winners re-priced as average losers)."""
    res = resolved(trades)
    n = len(res)
    if n < 2:
        return {"n": n, "note": "insufficient trades for Monte Carlo"}
    rng = random.Random(seed)
    rs = [t["r_multiple"] for t in res]
    order = list(rs)
    dds = []
    for _ in range(n_iter):
        rng.shuffle(order)
        dds.append(max_drawdown_r(order))
    stress: Dict[str, Any] = {}
    for label, mult in (("base", 1.0), ("slip_x1.5", 1.5), ("slip_x2", 2.0)):
        cm = costs.scaled(mult)
        rs_m = [pnl_under(t, cm)["r_multiple"] for t in res]
        boot = [statistics.mean(rng.choices(rs_m, k=n)) for _ in range(n_iter)]
        p = _pcts(boot)
        gl = -sum(r for r in rs_m if r < 0)
        stress[label] = {"expectancy_r": statistics.mean(rs_m),
                         "expectancy_p5": p["p5"], "expectancy_p50": p["p50"],
                         "expectancy_p95": p["p95"],
                         "win_rate": sum(1 for r in rs_m if r > 0) / n,
                         "profit_factor": (sum(r for r in rs_m if r > 0) / gl) if gl > 0 else None}
    win_idx = [k for k, r in enumerate(rs) if r > 0]
    loss_rs = [r for r in rs if r < 0]
    avg_loss = statistics.mean(loss_rs) if loss_rs else -1.0
    flips = min(len(win_idx), int(round(0.10 * n)))
    hair = []
    for _ in range(200):
        chosen = set(rng.sample(win_idx, flips)) if flips else set()
        hair.append(statistics.mean(avg_loss if k in chosen else r for k, r in enumerate(rs)))
    return {"n": n, "iterations": n_iter, "drawdown_r": _pcts(dds), "stress": stress,
            "winrate_minus_10pp": {"flipped": flips, "expectancy_r": statistics.mean(hair),
                                   **_pcts(hair)}}


# ------------------------------------------------------------- composite ----

COMPOSITE_COMPONENTS = ("expectancy_r", "profit_factor", "neg_max_drawdown_r",
                        "sortino", "consistency", "wilson_lb")


def composite_components(m: Dict[str, Any]) -> Dict[str, Optional[float]]:
    mdd = m.get("max_drawdown_r")
    return {"expectancy_r": m.get("expectancy_r"), "profit_factor": m.get("profit_factor"),
            "neg_max_drawdown_r": (-mdd) if mdd is not None else None,
            "sortino": m.get("sortino"), "consistency": m.get("consistency"),
            "wilson_lb": m.get("wilson_lb")}


def composite_scores(rows: Sequence[Dict[str, Any]]) -> List[float]:
    """Rank-average (0..1, higher is better) across COMPOSITE_COMPONENTS. Ties
    share the mean rank; a missing component ranks last. Win rate itself is
    deliberately not a component — only its Wilson lower bound is."""
    n = len(rows)
    if n == 0:
        return []
    total = [0.0] * n
    for key in COMPOSITE_COMPONENTS:
        vals = [composite_components(r).get(key) for r in rows]
        sort_vals = [(-math.inf if v is None else float(v)) for v in vals]
        order = sorted(range(n), key=lambda k: sort_vals[k])
        pos = 0
        while pos < n:
            end = pos
            while end + 1 < n and sort_vals[order[end + 1]] == sort_vals[order[pos]]:
                end += 1
            mean_rank = (pos + end) / 2.0 + 1.0          # 1-based average rank
            for k in order[pos:end + 1]:
                total[k] += (mean_rank - 0.5) / n
            pos = end + 1
    return [t / len(COMPOSITE_COMPONENTS) for t in total]


# ----------------------------------------------------------------- stages ----

PAPER_STAGES = ("PROMISING", "PRODUCTION_CANDIDATE")


def decide_stage(train: Dict[str, Any], validation: Dict[str, Any], oos: Dict[str, Any],
                 robust: Optional[float], mc: Optional[Dict[str, Any]],
                 has_run: bool = True) -> Tuple[str, str]:
    """Backtest-derived stage for one (market, timeframe). PROMISING and
    PRODUCTION_CANDIDATE are reachable only through stage_from_paper()."""
    def n_e(m: Dict[str, Any]) -> Tuple[int, float]:
        return int(m.get("n") or 0), float(m.get("expectancy_r") or 0.0)

    tn, te = n_e(train or {})
    vn, ve = n_e(validation or {})
    on, oe = n_e(oos or {})
    if on >= 30 and oe < -0.1:
        return "FAILED", f"oos n={on} expectancy_r={oe:.3f} < -0.1"
    p5 = ((mc or {}).get("stress", {}).get("slip_x1.5", {}) or {}).get("expectancy_p5")
    if (vn >= 20 and ve > 0 and on >= 20 and oe > 0 and (robust or 0.0) >= 0.6
            and p5 is not None and p5 > 0):
        return "PAPER_TRADING", (f"validation n={vn} e={ve:.3f}; oos n={on} e={oe:.3f}; "
                                 f"robustness={robust:.2f}; MC p5 @1.5x slip={p5:.3f}")
    if tn >= 30 and te > 0:
        why = []
        if vn < 20 or ve <= 0:
            why.append(f"validation n={vn} e={ve:.3f}")
        if on < 20 or oe <= 0:
            why.append(f"oos n={on} e={oe:.3f}")
        if (robust or 0.0) < 0.6:
            why.append(f"robustness={robust if robust is not None else 'n/a'}")
        if p5 is None or p5 <= 0:
            why.append(f"MC p5 @1.5x slip={p5 if p5 is not None else 'n/a'}")
        return "VALIDATION", f"train n={tn} e={te:.3f}; not paper because " + "; ".join(why)
    if has_run:
        return "BACKTESTING", f"train n={tn} e={te:.3f} (needs n>=30 and e>0 for VALIDATION)"
    return "RESEARCH", "no backtest run yet"


def stage_from_paper(paper_n: int, paper_expectancy_r: Optional[float]) -> Optional[str]:
    """Hook for the paper cohort: >=100 trades with positive expectancy ->
    PRODUCTION_CANDIDATE, >=50 -> PROMISING, otherwise no promotion."""
    if paper_expectancy_r is None or paper_expectancy_r <= 0:
        return None
    if paper_n >= 100:
        return "PRODUCTION_CANDIDATE"
    if paper_n >= 50:
        return "PROMISING"
    return None


def best_stage(stages: Iterable[str]) -> str:
    """Highest stage reached by any combo; FAILED only when nothing else exists."""
    stages = list(stages)
    live = [s for s in stages if s != "FAILED"]
    if live:
        return max(live, key=STAGES.index)
    return "FAILED" if stages else "RESEARCH"


# ------------------------------------------------------------ worker jobs ----

@functools.lru_cache(maxsize=None)
def _strategy(strategy_id: str) -> Tuple[StrategyMeta, Callable]:
    from .registry import load
    loaded = load(strategy_id)
    if loaded is None:
        raise KeyError(f"strategy {strategy_id!r} not discoverable")
    return loaded.meta, loaded.signal


@functools.lru_cache(maxsize=None)
def _series(symbol: str, timeframe: str, forward: bool) -> Tuple[List[dict], List[dict]]:
    """(engine bars, daily bars) for one symbol. Forward series come from the
    5min extended-hours capture, resampled when the timeframe is coarser."""
    if forward:
        raw = to_engine_bars(read_cache(symbol, "5min", extended=True))
        bars = raw if timeframe == "5min" else resample(raw, TF_MINUTES[timeframe])
    else:
        bars = to_engine_bars(read_cache(symbol, timeframe))
    daily = [] if timeframe == "1day" else to_engine_bars(read_cache(symbol, "1day"))
    return bars, daily


@functools.lru_cache(maxsize=None)
def _pair_series(symbol: str, timeframe: str, forward: bool) -> Tuple[Optional[str], List[dict]]:
    """(partner symbol, partner engine bars) for a PAIRS primary whose partner is
    cached on the same timeframe (and source); (None, []) otherwise."""
    partner = PAIRS.get(symbol.upper())
    if not partner:
        return None, []
    bars, _ = _series(partner, timeframe, forward)
    return (partner, bars) if bars else (None, [])


@functools.lru_cache(maxsize=1)
def spy_context() -> Tuple[List[dict], RegimeIndex]:
    spy = to_engine_bars(read_cache("SPY", "1day", start="2023-01-01"))
    return spy, RegimeIndex(spy)


def replay_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Process-pool entry point: one (strategy, symbol, timeframe) series
    replayed for every cfg in job["cfgs"] over [job["start"], job["end"]]."""
    sid, symbol, tf = job["strategy_id"], job["symbol"], job["timeframe"]
    forward = bool(job.get("forward"))
    out: Dict[str, Any] = {"strategy_id": sid, "symbol": symbol, "timeframe": tf,
                           "market": market_of(symbol), "forward": forward, "results": {},
                           "coverage": {}, "error": None}
    try:
        meta, fn = _strategy(sid)
        bars, daily = _series(symbol, tf, forward)
        pair_symbol, pair_bars = _pair_series(symbol, tf, forward)
        spy, regimes = spy_context()
        out["coverage"] = {"bars": len(bars), "first": bars[0]["date"] if bars else None,
                           "last": bars[-1]["date"] if bars else None,
                           "daily_bars": len(daily) if tf != "1day" else len(bars),
                           "source": "5min_ext_resampled" if (forward and tf != "5min") else "cache",
                           "pair": pair_symbol, "pair_bars": len(pair_bars),
                           "premarket_bars": sum(1 for b in bars if b["minute_of_day"] < 570)
                           if tf != "1day" else 0}
        costs = CostModel(**job.get("costs", {})) if job.get("costs") else DEFAULT_COSTS
        atr14 = None
        for cfg in job["cfgs"]:
            r = replay(meta, fn, cfg, bars, daily, spy, regimes, out["market"], symbol, tf,
                       job["start"], job["end"], costs=costs, lookback=int(job.get("lookback", 800)),
                       exit_model=job.get("exit_model", "scale_out"), atr14=atr14,
                       pair_bars=pair_bars or None, pair_symbol=pair_symbol)
            out["results"][cfg_key(cfg)] = r
    except Exception as exc:  # reported, never raised: one bad series must not stop the run
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# ------------------------------------------------------- strategy driver ----

def run_strategy(meta: StrategyMeta, markets: Sequence[str], timeframes: Sequence[str],
                 map_fn: Callable[[Callable, Sequence[dict]], Iterable[dict]],
                 symbols_only: Optional[Sequence[str]] = None, quick: bool = False,
                 costs: CostModel = DEFAULT_COSTS, lookback: int = 800,
                 exit_model: str = "scale_out", forward_syms: Optional[Sequence[str]] = None,
                 logger: Callable[[str], None] = log.info) -> Dict[str, Any]:
    """Sweep, select, report OOS once, then run the forward set — for every
    (market, timeframe) the strategy declares that has cached data."""
    cfgs = quick_configs(meta) if quick else grid_configs(meta)
    combos = [(m, tf) for m in meta.markets if m in markets for tf in meta.timeframes if tf in timeframes]
    result: Dict[str, Any] = {"strategy_id": meta.id, "name": meta.name, "family": meta.family,
                              "category": meta.category, "hypothesis": meta.hypothesis,
                              "markets": meta.markets, "timeframes": meta.timeframes,
                              "hold": meta.hold, "stop_method": meta.stop_method,
                              "version": meta.version, "grid_size": len(cfgs),
                              "costs": costs.as_dict(), "exit_model": exit_model,
                              "combos": {}, "errors": []}
    if not combos:
        result["stage"], result["stage_reason"] = "RESEARCH", "no declared market/timeframe has cached data"
        return result

    # phase A: sweep on train + validation (one replay covers both; split by signal
    # date). One job per (series, config) so a long crypto series cannot starve the pool.
    jobs, combo_syms = [], {}
    for m, tf in list(combos):
        syms = available_symbols(m, tf, symbols_only)
        if not syms:                       # e.g. "index" — nothing cached, nothing to claim
            result.setdefault("no_data", []).append(f"{m}/{tf}")
            combos.remove((m, tf))
            continue
        combo_syms[(m, tf)] = syms
        for s in syms:
            for cfg in cfgs:
                jobs.append({"strategy_id": meta.id, "symbol": s, "timeframe": tf, "cfgs": [cfg],
                             "start": SWEEP_RANGE[0], "end": SWEEP_RANGE[1], "lookback": lookback,
                             "exit_model": exit_model, "costs": costs.__dict__})
    n_series = sum(len(v) for v in combo_syms.values())
    logger(f"{meta.id}: sweep {len(cfgs)} configs x {n_series} series ({len(jobs)} jobs)")
    sweep: Dict[Tuple[str, str], Dict[str, List[Dict[str, Any]]]] = {}
    coverage: Dict[Tuple[str, str], Dict[str, dict]] = {}
    for r in map_fn(replay_job, jobs):
        if r["error"]:
            result["errors"].append(f"{r['symbol']} {r['timeframe']}: {r['error']}")
            continue
        key = (r["market"], r["timeframe"])
        coverage.setdefault(key, {})[r["symbol"]] = {"symbol": r["symbol"], **r["coverage"]}
        for ck, rr in r["results"].items():
            sweep.setdefault(key, {}).setdefault(ck, []).append(rr)
            result["errors"].extend(rr["errors"][:3])

    # phase B: choose per combo, then OOS once + forward with the chosen config
    chosen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    jobs = []
    fwd_all = list(forward_syms) if forward_syms is not None else forward_symbols()
    for m, tf in combos:
        per_cfg = sweep.get((m, tf), {})
        train_by, val_by = {}, {}
        for ck, rs in per_cfg.items():
            train_by[ck] = metrics(t for r in rs for t in r["trades"] if t["split"] == "train")
            val_by[ck] = metrics(t for r in rs for t in r["trades"] if t["split"] == "validation")
        cfg = select_params(cfgs, train_by, val_by)
        chosen[(m, tf)] = cfg
        result["combos"][f"{m}/{tf}"] = {
            "market": m, "timeframe": tf, "symbols": combo_syms[(m, tf)],
            "selected_params": cfg, "robustness": robustness(cfg, meta, train_by),
            "grid": [{"params": c, "train": compact(train_by.get(cfg_key(c), metrics([]))),
                      "validation": compact(val_by.get(cfg_key(c), metrics([])))} for c in cfgs],
            "counts": dict(sum((Counter(r["counts"]) for r in per_cfg.get(cfg_key(cfg), [])), Counter())),
            "coverage": list(coverage.get((m, tf), {}).values()),
            "_sweep_trades": [t for r in per_cfg.get(cfg_key(cfg), []) for t in r["trades"]],
        }
        for s in combo_syms[(m, tf)]:
            jobs.append({"strategy_id": meta.id, "symbol": s, "timeframe": tf, "cfgs": [cfg],
                         "start": SPLITS["oos"][0], "end": SPLITS["oos"][1], "lookback": lookback,
                         "exit_model": exit_model, "costs": costs.__dict__})
        if m in ("stocks", "etf") and tf in ("5min", "15min", "30min", "1hour"):
            for s in fwd_all:
                if market_of(s) == m:
                    jobs.append({"strategy_id": meta.id, "symbol": s, "timeframe": tf, "cfgs": [cfg],
                                 "start": SPLITS["forward"][0], "end": SPLITS["forward"][1],
                                 "lookback": lookback, "exit_model": exit_model, "forward": True,
                                 "costs": costs.__dict__})
    logger(f"{meta.id}: oos + forward on {len(jobs)} series")
    later: Dict[Tuple[str, str, str], List[dict]] = {}
    for r in map_fn(replay_job, jobs):
        if r["error"]:
            result["errors"].append(f"{r['symbol']} {r['timeframe']}: {r['error']}")
            continue
        split = "forward" if r["forward"] else "oos"
        key = (r["market"], r["timeframe"], split)
        for rr in r["results"].values():
            later.setdefault(key, []).extend(rr["trades"])
            result["errors"].extend(rr["errors"][:3])
        if r["forward"]:
            result["combos"][f"{r['market']}/{r['timeframe']}"].setdefault("forward_coverage", []).append(
                {"symbol": r["symbol"], **r["coverage"]})

    stages = []
    for m, tf in combos:
        c = result["combos"][f"{m}/{tf}"]
        sweep_tr = c.pop("_sweep_trades")
        oos_tr = [t for t in later.get((m, tf, "oos"), []) if t["split"] == "oos"]
        fwd_tr = [t for t in later.get((m, tf, "forward"), []) if t["split"] == "forward"]
        c["splits"] = {"train": full_report(t for t in sweep_tr if t["split"] == "train"),
                       "validation": full_report(t for t in sweep_tr if t["split"] == "validation"),
                       "oos": full_report(oos_tr), "forward": full_report(fwd_tr)}
        c["monte_carlo"] = monte_carlo(oos_tr, costs)
        c["open_trades"] = {"oos": sum(1 for t in oos_tr if t["result"] == "OPEN"),
                            "forward": sum(1 for t in fwd_tr if t["result"] == "OPEN")}
        c["stage"], c["stage_reason"] = decide_stage(
            c["splits"]["train"]["metrics"], c["splits"]["validation"]["metrics"],
            c["splits"]["oos"]["metrics"], c["robustness"], c["monte_carlo"])
        c["trades"] = {"oos": resolved(oos_tr), "forward": resolved(fwd_tr)}
        stages.append(c["stage"])
        logger(f"{meta.id} {m}/{tf}: train n={c['splits']['train']['metrics']['n']} "
               f"val n={c['splits']['validation']['metrics']['n']} "
               f"oos n={c['splits']['oos']['metrics']['n']} -> {c['stage']}")

    result["stage"] = best_stage(stages) if stages else "RESEARCH"
    result.update(_best_of(result["combos"], result["stage"]))
    result["errors"] = result["errors"][:50]
    return result


def summarize(combos: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Strategy-level stage + best-of fields from finished combos (pure, so a
    leaderboard rebuild can recompute them without replaying anything)."""
    stage = best_stage(c["stage"] for c in combos.values()) if combos else "RESEARCH"
    return {"stage": stage, **_best_of(combos, stage)}


def _best_of(combos: Dict[str, Dict[str, Any]], stage: Optional[str] = None,
             min_n: int = 15) -> Dict[str, Any]:
    """Best market/timeframe = highest OOS expectancy with n >= min_n; best and
    worst regime from pooled OOS trades. 'insufficient data' otherwise, with the
    combo that earned the strategy's stage named so the stage is explained."""
    best_key, best_e = None, -math.inf
    pooled: List[dict] = []
    for key, c in combos.items():
        m = c["splits"]["oos"]["metrics"]
        pooled.extend(c["trades"]["oos"])
        if (m.get("n") or 0) >= min_n and m["expectancy_r"] > best_e:
            best_key, best_e = key, m["expectancy_r"]
    by_reg = {k: v for k, v in breakdown(pooled, "regime").items() if (v.get("n") or 0) >= min_n}
    out = {"best_market": "insufficient data", "best_timeframe": "insufficient data",
           "best_regime": "insufficient data", "worst_regime": "insufficient data",
           "stage_reason": ""}
    if best_key:
        out["best_market"], out["best_timeframe"] = best_key.split("/")
        out["stage_reason"] = f"{best_key}: {combos[best_key]['stage_reason']}"
    elif combos:
        loud = [k for k, c in combos.items() if (c.get("counts", {}).get("signals", 0) == 0)]
        out["stage_reason"] = f"no combo reached {min_n} OOS trades"
        via = next((k for k, c in combos.items() if stage and c.get("stage") == stage), None)
        if via:
            out["stage_reason"] = f"{stage} via {via}: {combos[via]['stage_reason']}; " + out["stage_reason"]
        if loud:
            # A strategy that needs bars the cached history does not contain
            # cannot fire there, and telling the reader to audit the gates sends
            # them after a bug that is not in the code.  Regular-hours-only
            # history is the common case: 2024-2025 intraday bars start at 09:30.
            no_pm = [k for k in loud
                     if not any(c.get("premarket_bars")
                                for c in combos[k].get("coverage") or [])]
            if no_pm:
                out["stage_reason"] += (
                    f"; zero signals on {no_pm} — the cached history for those "
                    "carries no premarket bars, so a premarket-dependent setup "
                    "cannot occur; not evidence about the strategy")
            rest = [k for k in loud if k not in no_pm]
            if rest:
                out["stage_reason"] += (f"; zero signals ever on {rest} — audit "
                                        "the gates, not the thresholds")
            rth_only = all(not k.endswith("/1day") and all(cv.get("premarket_bars", 0) == 0 for cv in combos[k]["coverage"])
                           for k in loud)
            if rth_only:
                out["stage_reason"] += ("; note the historical intraday cache is RTH-only (0 premarket bars), so "
                                        "premarket/extended-hours gates cannot pass before the forward split")
    if by_reg:
        out["best_regime"] = max(by_reg, key=lambda k: by_reg[k]["expectancy_r"])
        out["worst_regime"] = min(by_reg, key=lambda k: by_reg[k]["expectancy_r"])
    return out
