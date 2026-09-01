"""Fleet backtester: chronological daily-resolution replay of every engine that
can be honestly tested on daily bars. Intraday-only engines are labeled
FORWARD_ONLY rather than faked. Same conservative execution: next-bar-open
entries + slippage, stop/T1-partial/T2/time exits, AMBIGUOUS on both-touch."""
from typing import Any, Dict, List, Optional

from ..strategy.engines import ENGINES
from ..strategy.registry import ETF_UNIVERSE, MODELS, PAIRS
from .tournament import metrics, wilson_lb

DAILY_TESTABLE = {   # engine -> can run on daily bars alone
    "trend": True, "resmom": True, "factor": True, "gaussian": True,
    "vacuum": False,     # needs 5m confirmation — forward only
    "chartpat": False,   # engine needs m5 for px; daily variant below
    "pairs": True, "earnings": True, "insider": False, "confluence": False,
    "meanrev": False, "orb": False, "opendrive": False, "rsreclaim": False,
}
ENTRY_SLIP_PCT = 0.25          # liquid ETFs/large caps
SELL_SLIP_PCT = 0.25
MAX_HOLD_BARS = 20


def _simulate_exit(bars_fwd: List[dict], entry: float, stop: float,
                   t1: float, t2: float) -> Dict[str, Any]:
    """Daily-bar walk: stop / T1 partial(50%)+breakeven / T2 / 20-bar time exit.
    Both-touch in one bar = AMBIGUOUS (never assume the favorable order)."""
    risk = max(1e-9, entry - stop)
    realized_r = 0.0
    frac = 1.0
    cur_stop = stop
    for n, b in enumerate(bars_fwd[:MAX_HOLD_BARS]):
        hit_stop = b["low"] <= cur_stop
        hit_t1 = frac == 1.0 and t1 and b["high"] >= t1
        hit_t2 = t2 and b["high"] >= t2
        if hit_stop and (hit_t1 or hit_t2):
            return {"outcome": "ambiguous", "pct": None, "r": None,
                    "reason": "stop_and_target_same_bar", "bars_held": n + 1}
        if hit_stop:
            px = cur_stop * (1 - SELL_SLIP_PCT / 100)
            r = realized_r + frac * (px - entry) / risk
            return {"outcome": "win" if r > 0.05 else "loss" if r < -0.05 else "neutral",
                    "pct": round((realized_r * risk / entry * 100)
                                 + frac * (px - entry) / entry * 100, 3),
                    "r": round(r, 3), "reason": "stop", "bars_held": n + 1}
        if hit_t1:
            px = t1 * (1 - SELL_SLIP_PCT / 100)
            realized_r += 0.5 * (px - entry) / risk
            frac = 0.5
            cur_stop = entry            # breakeven after partial
        if hit_t2 and frac < 1.0:
            px = t2 * (1 - SELL_SLIP_PCT / 100)
            r = realized_r + frac * (px - entry) / risk
            return {"outcome": "win" if r > 0.05 else "neutral",
                    "pct": round((r * risk) / entry * 100, 3),
                    "r": round(r, 3), "reason": "target2", "bars_held": n + 1}
    last = bars_fwd[min(MAX_HOLD_BARS, len(bars_fwd)) - 1] if bars_fwd else None
    if last is None:
        return {"outcome": "neutral", "pct": None, "r": None,
                "reason": "no_forward_bars", "bars_held": 0}
    px = last["close"] * (1 - SELL_SLIP_PCT / 100)
    r = realized_r + frac * (px - entry) / risk
    return {"outcome": "win" if r > 0.05 else "loss" if r < -0.05 else "neutral",
            "pct": round((realized_r * risk / entry * 100)
                         + frac * (px - entry) / entry * 100, 3),
            "r": round(r, 3), "reason": "time_exit_20bars",
            "bars_held": min(MAX_HOLD_BARS, len(bars_fwd))}


def _to_engine_bars(rows: List[dict]) -> List[dict]:
    return [{"o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"],
             "v": r["volume"] or 0, "date": r["date"]} for r in rows
            if r.get("close") and r.get("high") and r.get("low")]


def run_fleet_backtest(series: Dict[str, List[dict]],
                       earnings_by_date: Dict[str, Dict[str, dict]],
                       start_idx: int = 140) -> Dict[str, Any]:
    """series: symbol -> chronological EOD rows. Walks day by day; at index i an
    engine sees bars[:i+1] only; entry at bar i+1 open."""
    eng_bars = {s: _to_engine_bars(rows) for s, rows in series.items()}
    spy = eng_bars.get("SPY") or []
    dates = [b["date"] for b in spy]
    trades: List[dict] = []
    open_until: Dict[tuple, int] = {}   # (model, sym) -> idx when tradeable again
    for i in range(start_idx, len(dates) - 1):
        d = dates[i]
        ctx = {"bars_daily": {s: b[:i + 1] for s, b in eng_bars.items()
                              if len(b) > i}, "bars_5m": {},
               "spy_daily": spy[:i + 1],
               "earnings": earnings_by_date.get(d, {}),
               "insider_clusters": {}, "fundamentals": {}}
        for mid, meta in MODELS.items():
            eng_name = meta["engine"]
            if not DAILY_TESTABLE.get(eng_name):
                continue
            fn = ENGINES[eng_name]
            symbols = ([f"{a}|{b}" for a, b in PAIRS] if eng_name == "pairs"
                       else list(eng_bars.keys()))
            for sym in symbols:
                base = sym.split("|")[0]
                if open_until.get((mid, base), -1) >= i:
                    continue
                bars_b = eng_bars.get(base)
                if not bars_b or len(bars_b) <= i + 1:
                    continue
                try:
                    v = fn(ctx, sym, {})
                except Exception:
                    continue
                if not v or v["action"] != "buy":
                    continue
                nxt = series[base][i + 1]
                if not nxt.get("open"):
                    continue
                entry = nxt["open"] * (1 + ENTRY_SLIP_PCT / 100)
                if entry <= v["stop"]:
                    continue
                res = _simulate_exit(series[base][i + 1:], entry, v["stop"],
                                     v["target1"], v["target2"])
                open_until[(mid, base)] = i + res["bars_held"]
                trades.append({"model": mid, "symbol": base, "date": d,
                               "signal_time": d, "entry": entry,
                               "setup": v["setup"], "score": v["score"],
                               "t_min": res["bars_held"] * 390,
                               **{k: res[k] for k in
                                  ("outcome", "pct", "r", "reason")}})
    by_model: Dict[str, Any] = {}
    for mid in {t["model"] for t in trades}:
        rows = [t for t in trades if t["model"] == mid]
        m = metrics(rows)
        halves = (rows[: len(rows) // 2], rows[len(rows) // 2:])
        m["first_half_exp"] = metrics(halves[0])["expectancy_pct"] if halves[0] else None
        m["second_half_exp"] = metrics(halves[1])["expectancy_pct"] if halves[1] else None
        by_model[mid] = m
    forward_only = [mid for mid, meta in MODELS.items()
                    if not DAILY_TESTABLE.get(meta["engine"])]
    return {"cohort": "replay_daily_baseline",
            "resolution": "daily bars, next-open entries, conservative slippage "
                          f"{ENTRY_SLIP_PCT}%/{SELL_SLIP_PCT}%, AMBIGUOUS on both-touch",
            "sessions": len(dates) - start_idx - 1,
            "date_range": [dates[start_idx] if dates else None,
                           dates[-1] if dates else None],
            "universe": sorted(series.keys()),
            "trades_total": len(trades), "by_model": by_model,
            "sample_trades": trades[-120:],
            "forward_only_models": forward_only,
            "note": "Intraday engines cannot be honestly tested on daily bars — "
                    "they are labeled FORWARD_ONLY and judged by live paper."}
