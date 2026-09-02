"""Backtest + walk-forward engine for EXTREME_BB_RSI.

Rules enforced structurally, not by convention:
  * Signals come from strategy.reversion.scan(), the same function the live
    scanner calls. There is no separate "backtest logic" that could drift.
  * A signal confirms on a CLOSED bar; simulated entry is the NEXT bar's open.
    The engine never reads a price that was unknowable at decision time.
  * Same-bar stop-and-target is AMBIGUOUS and is never scored as a win.
  * Costs are non-zero by default (slippage + commission both sides).
  * Splits are chronological. Candles are never shuffled.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..strategy import reversion as R
from ..strategy.indicators import atr_series, ema_series, rsi_series

INTERVALS = ["5min", "15min", "30min", "1hour", "4hour", "1day"]
# 1min is not entitled on the current data plan — never silently substituted.
UNAVAILABLE_INTERVALS = ["1min", "3min"]

BARS_PER_DAY = {"5min": 78, "15min": 26, "30min": 13, "1hour": 7,
                "4hour": 2, "1day": 1}


def htf_of(interval: str) -> Optional[str]:
    return {"5min": "1hour", "15min": "1hour", "30min": "4hour",
            "1hour": "4hour", "4hour": "1day", "1day": None}.get(interval)


def htf_trend_series(htf_bars: Sequence[dict]) -> List[Tuple[Any, str]]:
    """(time, trend) from the higher timeframe, EMA200 based, causal."""
    closes = [float(b["c"]) for b in htf_bars]
    e200 = ema_series(closes, 200)
    e50 = ema_series(closes, 50)
    out = []
    for i, b in enumerate(htf_bars):
        if i < 200 or e200[i] is None:
            out.append((b.get("time"), "unknown"))
            continue
        c = closes[i]
        if c > e200[i] and (e50[i] or 0) > e200[i]:
            out.append((b.get("time"), "up"))
        elif c < e200[i] and (e50[i] or 0) < e200[i]:
            out.append((b.get("time"), "down"))
        else:
            out.append((b.get("time"), "neutral"))
    return out


def trend_at(htf: List[Tuple[Any, str]], t: Any) -> str:
    """Most recent higher-timeframe bar at or before t. Never looks forward."""
    if not htf or t is None:
        return "unknown"
    lo, hi, best = 0, len(htf) - 1, "unknown"
    while lo <= hi:
        mid = (lo + hi) // 2
        if htf[mid][0] is not None and htf[mid][0] <= t:
            best = htf[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# ------------------------------------------------------------ simulation ----

def simulate(bars: Sequence[dict], signals: List[dict], p: Dict[str, Any],
             max_hold_bars: Optional[int] = None) -> List[Dict[str, Any]]:
    """Turn confirmed signals into resolved trades. Bar-by-bar, forward only."""
    closes = [float(b["c"]) for b in bars]
    rsis = rsi_series(closes, int(p["rsi_length"]))
    atrs = atr_series(bars, 14)
    slip = float(p.get("slippage_pct", 0.05)) / 100.0
    comm = float(p.get("commission_pct", 0.02)) / 100.0
    hold = max_hold_bars or int(p.get("max_hold_bars") or 78)
    trades: List[Dict[str, Any]] = []

    for s in signals:
        if s.get("status") != "CONFIRMED" or not s.get("levels"):
            continue
        k = s["confirm_idx"]
        if k + 1 >= len(bars):
            continue
        d = s["direction"]
        L = s["levels"]
        nxt = bars[k + 1]
        raw_entry = float(nxt["o"])
        atr = atrs[k] or (raw_entry * 0.01)

        # No chasing: if the next open gapped past the plan, the trade is MISSED.
        dev = (raw_entry - L["entry"]) if d == "long" else (L["entry"] - raw_entry)
        if dev > float(p["max_entry_dev_atr"]) * atr:
            trades.append({"result": "MISSED", "direction": d, "score": s["score"],
                           "confirm_idx": k, "signal_price": L["entry"],
                           "next_open": raw_entry, "reason": "gapped beyond no-chase level",
                           "time": s.get("confirm_time")})
            continue

        entry = raw_entry * (1 + slip) if d == "long" else raw_entry * (1 - slip)
        stop = L["stop"]
        risk_unit = abs(entry - stop)
        if risk_unit <= 0:
            continue
        tps = [t["price"] for t in L["targets"]]
        tp1 = tps[0]

        mfe = mae = 0.0
        exit_price = exit_reason = None
        exit_idx = None
        for j in range(k + 1, min(len(bars), k + 1 + hold)):
            b = bars[j]
            hi, lo = float(b["h"]), float(b["l"])
            if d == "long":
                mfe = max(mfe, (hi - entry) / risk_unit)
                mae = min(mae, (lo - entry) / risk_unit)
                hit_stop, hit_tp = lo <= stop, hi >= tp1
            else:
                mfe = max(mfe, (entry - lo) / risk_unit)
                mae = min(mae, (entry - hi) / risk_unit)
                hit_stop, hit_tp = hi >= stop, lo <= tp1

            if hit_stop and hit_tp:
                exit_price, exit_reason, exit_idx = stop, "AMBIGUOUS", j
                break
            if hit_stop:
                exit_price, exit_reason, exit_idx = stop, "STOP", j
                break
            if hit_tp:
                exit_price, exit_reason, exit_idx = tp1, "TARGET", j
                break
            if p["exit_model"] == "rsi_norm" and rsis[j] is not None:
                lvl = float(p["exit_param"])
                if (d == "long" and rsis[j] >= lvl) or (d == "short" and rsis[j] <= lvl):
                    exit_price, exit_reason, exit_idx = float(b["c"]), "RSI_NORMALISED", j
                    break
        if exit_price is None:
            j = min(len(bars) - 1, k + hold)
            exit_price, exit_reason, exit_idx = float(bars[j]["c"]), "TIME_STOP", j

        fill = exit_price * (1 - slip) if d == "long" else exit_price * (1 + slip)
        gross = (fill - entry) if d == "long" else (entry - fill)
        cost = (entry + fill) * comm
        net = gross - cost
        r_mult = net / risk_unit

        if exit_reason == "AMBIGUOUS":
            wl = "AMBIGUOUS"                      # never counted as a win
        elif net > 0:
            wl = "WIN"
        elif net < 0:
            wl = "LOSS"
        else:
            wl = "BREAKEVEN"

        trades.append({
            "result": wl, "direction": d, "score": s["score"],
            "score_band": s["score_band"], "regime": s["snapshot"].get("regime"),
            "adx": s["snapshot"].get("adx"), "rvol": s["snapshot"].get("rvol"),
            "htf_trend": s["snapshot"].get("htf_trend"),
            "divergence": bool(s.get("divergence")),
            "confirm_idx": k, "exit_idx": exit_idx,
            "bars_held": (exit_idx - k) if exit_idx else None,
            "entry": round(entry, 6), "stop": round(stop, 6),
            "target": round(tp1, 6), "exit": round(fill, 6),
            "exit_reason": exit_reason,
            "gross_pct": round(gross / entry * 100, 4),
            "net_pct": round(net / entry * 100, 4),
            "cost_pct": round(cost / entry * 100, 4),
            "r_multiple": round(r_mult, 4),
            "mfe_r": round(mfe, 3), "mae_r": round(mae, 3),
            "planned_rr": L["rr_primary"],
            "time": s.get("confirm_time"),
        })
    return trades


# --------------------------------------------------------------- metrics ----

def wilson_lb(wins: int, n: int, z: float = 1.96) -> Optional[float]:
    if n == 0:
        return None
    ph = wins / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    m = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return max(0.0, (c - m) / d)


def sample_label(n: int) -> str:
    if n < 30:
        return "INSUFFICIENT DATA"
    if n < 100:
        return "EARLY"
    if n < 500:
        return "MODERATE SAMPLE"
    return "STRONGER SAMPLE"


def metrics(trades: List[dict]) -> Dict[str, Any]:
    resolved = [t for t in trades if t["result"] in ("WIN", "LOSS", "BREAKEVEN", "AMBIGUOUS")]
    n = len(resolved)
    if n == 0:
        return {"trades": 0, "missed": sum(1 for t in trades if t["result"] == "MISSED"),
                "sample": "INSUFFICIENT DATA", "note": "no resolved trades"}
    wins = [t for t in resolved if t["result"] == "WIN"]
    losses = [t for t in resolved if t["result"] == "LOSS"]
    amb = [t for t in resolved if t["result"] == "AMBIGUOUS"]
    rs = [t["r_multiple"] for t in resolved]
    aw = statistics.mean([t["net_pct"] for t in wins]) if wins else 0.0
    al = abs(statistics.mean([t["net_pct"] for t in losses])) if losses else 0.0
    wr = len(wins) / n
    gross_win = sum(t["net_pct"] for t in wins)
    # Ambiguous trades resolved at the stop are economically losses. Excluding
    # them from the denominator would flatter the profit factor.
    gross_loss = abs(sum(t["net_pct"] for t in losses)) + \
        abs(sum(min(0.0, t["net_pct"]) for t in amb))

    # equity curve in R for drawdown
    eq, peak, mdd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)

    streak_w = streak_l = best_w = best_l = 0
    for t in resolved:
        if t["result"] == "WIN":
            streak_w += 1; streak_l = 0
        elif t["result"] == "LOSS":
            streak_l += 1; streak_w = 0
        else:
            streak_w = streak_l = 0
        best_w, best_l = max(best_w, streak_w), max(best_l, streak_l)

    held = [t["bars_held"] for t in resolved if t.get("bars_held")]
    return {
        "trades": n,
        "wins": len(wins), "losses": len(losses),
        "ambiguous": len(amb),
        "breakeven": sum(1 for t in resolved if t["result"] == "BREAKEVEN"),
        "missed": sum(1 for t in trades if t["result"] == "MISSED"),
        "win_rate": round(wr * 100, 2),
        "win_rate_wilson_lb": round((wilson_lb(len(wins), n) or 0) * 100, 2),
        "avg_win_pct": round(aw, 3), "avg_loss_pct": round(al, 3),
        "expectancy_pct": round(wr * aw - (1 - wr) * al, 4),
        "expectancy_r": round(statistics.mean(rs), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "net_return_pct": round(sum(t["net_pct"] for t in resolved), 3),
        "total_r": round(sum(rs), 3),
        "max_drawdown_r": round(mdd, 3),
        "avg_r": round(statistics.mean(rs), 4),
        "median_r": round(statistics.median(rs), 4),
        "stdev_r": round(statistics.pstdev(rs), 4) if n > 1 else 0.0,
        "sharpe_like": round(statistics.mean(rs) / statistics.pstdev(rs), 3)
                       if n > 1 and statistics.pstdev(rs) > 0 else None,
        "sortino_like": _sortino(rs),
        "target_hit_rate": round(100 * sum(1 for t in resolved if t["exit_reason"] == "TARGET") / n, 2),
        "stop_hit_rate": round(100 * sum(1 for t in resolved if t["exit_reason"] == "STOP") / n, 2),
        "avg_mfe_r": round(statistics.mean([t["mfe_r"] for t in resolved]), 3),
        "avg_mae_r": round(statistics.mean([t["mae_r"] for t in resolved]), 3),
        "avg_hold_bars": round(statistics.mean(held), 1) if held else None,
        "median_hold_bars": round(statistics.median(held), 1) if held else None,
        "longest_win_streak": best_w, "longest_loss_streak": best_l,
        "avg_cost_pct": round(statistics.mean([t["cost_pct"] for t in resolved]), 4),
        "sample": sample_label(n),
    }


def _sortino(rs: List[float]) -> Optional[float]:
    downs = [r for r in rs if r < 0]
    if not downs or len(rs) < 2:
        return None
    dd = statistics.pstdev(downs) if len(downs) > 1 else abs(downs[0])
    return round(statistics.mean(rs) / dd, 3) if dd > 0 else None


def breakdown(trades: List[dict], key: str) -> Dict[str, Any]:
    """Metrics grouped by any trade field — regime, band, hour, direction."""
    groups: Dict[Any, List[dict]] = {}
    for t in trades:
        if t["result"] == "MISSED":
            continue
        groups.setdefault(t.get(key), []).append(t)
    return {str(k): metrics(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def chronological_split(bars: Sequence[dict], dev=0.6, val=0.2
                        ) -> Dict[str, Tuple[int, int]]:
    """60/20/20 by time. Never shuffled — that would leak the future."""
    n = len(bars)
    a, b = int(n * dev), int(n * (dev + val))
    return {"train": (0, a), "validation": (a, b), "test": (b, n)}
