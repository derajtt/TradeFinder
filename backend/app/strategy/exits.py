"""Exit-policy library. Every policy is a pure function over the SAME frozen
inputs: entry fill, stop, targets, and the post-entry bar path. A policy can never
change the entry. Sell fills subtract the execution model's slippage.

Path bars: {t_min (minutes after entry), o, h, l, c, v, vwap?, minute_of_day}.
Historical resolution is 5-minute bars — policies finer than the bar interval are
flagged unavailable historically and only run in live-forward mode.
"""
from typing import Any, Callable, Dict, List, Optional

AMBIGUOUS = "ambiguous"

EXEC_MODELS = {
    "optimistic": {"sell_slip_pct": 0.10, "stop_slip_pct": 0.20},
    "baseline": {"sell_slip_pct": 0.40, "stop_slip_pct": 0.60},
    "pessimistic": {"sell_slip_pct": 0.90, "stop_slip_pct": 1.40},
}


def _sell(price: float, model: Dict[str, float], stop_side=False) -> float:
    slip = model["stop_slip_pct" if stop_side else "sell_slip_pct"] / 100.0
    return price * (1 - slip)


def _result(exit_price, entry, stop, reason, t_min, outcome=None,
            frac: float = 1.0, realized_prior: float = 0.0):
    if exit_price is None:
        return {"exit_price": None, "pct": None, "r": None, "reason": reason,
                "t_min": t_min, "outcome": outcome or "no_fill"}
    risk = max(1e-9, entry - stop) if stop else entry * 0.05
    pct_piece = (exit_price - entry) / entry * 100.0 * frac
    r_piece = (exit_price - entry) / risk * frac
    pct = pct_piece + realized_prior * (risk / entry * 100.0)
    r = r_piece + realized_prior
    out = outcome or ("win" if r > 0.05 else "loss" if r < -0.05 else "neutral")
    return {"exit_price": round(exit_price, 5), "pct": round(pct, 3),
            "r": round(r, 3), "reason": reason, "t_min": t_min, "outcome": out}


def _stop_target_scan(path, entry, stop, target, model,
                      until_min: Optional[float] = None):
    """Walk bars; return (exit_dict or None, index stopped at). AMBIGUOUS when one
    bar touches both stop and target (tick order unknown at bar resolution)."""
    for i, b in enumerate(path):
        if until_min is not None and b["t_min"] > until_min:
            return None, i
        hit_stop = stop is not None and b["l"] <= stop
        hit_tgt = target is not None and b["h"] >= target
        if hit_stop and hit_tgt:
            return {"exit_price": None, "pct": None, "r": None,
                    "reason": "stop_and_target_same_bar", "t_min": b["t_min"],
                    "outcome": AMBIGUOUS}, i
        if hit_stop:
            return _result(_sell(stop, model, stop_side=True), entry, stop,
                           "stop", b["t_min"]), i
        if hit_tgt:
            return _result(_sell(target, model), entry, stop, "target",
                           b["t_min"]), i
    return None, len(path)


def _mark_at(path, minute):
    for b in path:
        if b["t_min"] >= minute:
            return b, b["t_min"]
    return (path[-1], path[-1]["t_min"]) if path else (None, None)


# ── policy implementations ────────────────────────────────────────────────
def time_exit(minutes: float) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        hit, _ = _stop_target_scan(path, entry, stop, None, model, until_min=minutes)
        if hit:
            return hit
        b, t = _mark_at(path, minutes)
        if b is None:
            return _result(None, entry, stop, "no_path_data", None)
        return _result(_sell(b["c"], model), entry, stop, f"time_{minutes:g}m", t)
    return run


def r_target_exit(r_mult: float) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        if not stop:
            return _result(None, entry, stop, "no_stop_defined", None)
        target = entry + r_mult * (entry - stop)
        hit, _ = _stop_target_scan(path, entry, stop, target, model)
        if hit:
            return hit
        if not path:
            return _result(None, entry, stop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, stop, "eod_no_trigger",
                       b["t_min"])
    return run


def pct_exit(tp_pct: float, sl_pct: float) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        target = entry * (1 + tp_pct / 100.0)
        pstop = entry * (1 - sl_pct / 100.0)
        hit, _ = _stop_target_scan(path, entry, pstop, target, model)
        if hit:
            return hit
        if not path:
            return _result(None, entry, pstop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, pstop, "eod_no_trigger", b["t_min"])
    return run


def vwap_loss_exit() -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        for b in path:
            if stop is not None and b["l"] <= stop:
                return _result(_sell(stop, model, True), entry, stop, "stop", b["t_min"])
            vw = b.get("vwap")
            if vw and b["c"] < vw:
                return _result(_sell(b["c"], model), entry, stop, "vwap_lost", b["t_min"])
        if not path:
            return _result(None, entry, stop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, stop, "eod_hold", b["t_min"])
    return run


def trail_bar_lows(lookback: int = 1) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        trail = stop
        for i, b in enumerate(path):
            if trail is not None and b["l"] <= trail:
                return _result(_sell(trail, model, True), entry, stop,
                               f"trail_bar_low", b["t_min"])
            lo = min(x["l"] for x in path[max(0, i - lookback + 1): i + 1])
            trail = max(trail or 0, lo)
        if not path:
            return _result(None, entry, stop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, stop, "eod_hold", b["t_min"])
    return run


def drawdown_from_high_exit(dd_pct: float) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        hi = entry
        for b in path:
            if stop is not None and b["l"] <= stop:
                return _result(_sell(stop, model, True), entry, stop, "stop", b["t_min"])
            hi = max(hi, b["h"])
            trig = hi * (1 - dd_pct / 100.0)
            if b["l"] <= trig and hi > entry:
                return _result(_sell(trig, model), entry, stop,
                               f"dd_{dd_pct:g}pct_from_high", b["t_min"])
        if not path:
            return _result(None, entry, stop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, stop, "eod_hold", b["t_min"])
    return run


def open_time_exit(minute_of_day: int, label: str) -> Callable:
    def run(entry, stop, t1, t2, path, model, ctx):
        for b in path:
            if stop is not None and b["l"] <= stop and b["minute_of_day"] < minute_of_day:
                return _result(_sell(stop, model, True), entry, stop, "stop", b["t_min"])
            if b["minute_of_day"] >= minute_of_day:
                px = b["o"] if b["minute_of_day"] == minute_of_day else b["c"]
                return _result(_sell(px, model), entry, stop, label, b["t_min"])
        if not path:
            return _result(None, entry, stop, "no_path_data", None)
        b = path[-1]
        return _result(_sell(b["c"], model), entry, stop, "eod_hold", b["t_min"])
    return run


def hybrid_partial_1r(hold_frac: float, then: Callable) -> Callable:
    """Take (1-hold_frac) at +1R; run `then` on the remainder."""
    def run(entry, stop, t1, t2, path, model, ctx):
        if not stop:
            return _result(None, entry, stop, "no_stop_defined", None)
        target1 = entry + (entry - stop)
        hit, idx = _stop_target_scan(path, entry, stop, target1, model)
        if hit is None or hit["outcome"] == AMBIGUOUS:
            return hit or then(entry, stop, t1, t2, path, model, ctx)
        if hit["reason"] == "stop":
            return hit
        realized = (1 - hold_frac) * 1.0  # (1R on the sold fraction)
        rest = then(entry, stop, t1, t2, path[idx:], model, ctx)
        if rest["exit_price"] is None:
            return dict(hit, reason=f"partial_1R_then_{rest['reason']}")
        risk = entry - stop
        r_total = realized + hold_frac * (rest["exit_price"] - entry) / risk
        pct_total = ((1 - hold_frac) * (target1 * (1 - EXEC_MODELS['baseline']['sell_slip_pct']/100) - entry)
                     + hold_frac * (rest["exit_price"] - entry)) / entry * 100.0
        return {"exit_price": rest["exit_price"], "pct": round(pct_total, 3),
                "r": round(r_total, 3),
                "reason": f"partial_1R_then_{rest['reason']}",
                "t_min": rest["t_min"],
                "outcome": "win" if r_total > 0.05 else "loss" if r_total < -0.05 else "neutral"}
    return run


# ── the tournament roster ────────────────────────────────────────────────
def build_policies() -> Dict[str, Dict[str, Any]]:
    P: Dict[str, Dict[str, Any]] = {}
    # time scalps (sub-5m only measurable live at ~1min polling)
    for m in (2, 3):
        P[f"scalp_{m}m"] = {"fn": time_exit(m), "family": "time",
                            "hist_ok": False,
                            "note": "finer than 5-min bars: forward/live only"}
    for m in (5, 7, 10, 15, 20, 30, 60):
        P[f"scalp_{m}m"] = {"fn": time_exit(m), "family": "time",
                            "hist_ok": m >= 5 and m % 5 == 0 or m in (7,),
                            "note": "" if m % 5 == 0 else "7m approximated to bar grid"}
    for r in (0.5, 1.0, 1.5, 2.0, 3.0):
        P[f"target_{r:g}R"] = {"fn": r_target_exit(r), "family": "r", "hist_ok": True}
    for tp, sl in ((5, 4), (8, 5), (12, 7), (20, 10)):
        P[f"pct_tp{tp}_sl{sl}"] = {"fn": pct_exit(tp, sl), "family": "pct", "hist_ok": True}
    P["vwap_loss"] = {"fn": vwap_loss_exit(), "family": "structure", "hist_ok": True}
    P["trail_bar_lows"] = {"fn": trail_bar_lows(1), "family": "structure", "hist_ok": True}
    P["dd8_from_high"] = {"fn": drawdown_from_high_exit(8), "family": "structure", "hist_ok": True}
    P["exit_pre_open_929"] = {"fn": open_time_exit(569, "pre_open"), "family": "open", "hist_ok": True}
    P["exit_open_930"] = {"fn": open_time_exit(570, "opening_print"), "family": "open", "hist_ok": True}
    P["exit_935"] = {"fn": open_time_exit(575, "at_935"), "family": "open", "hist_ok": True}
    P["exit_945"] = {"fn": open_time_exit(585, "at_945"), "family": "open", "hist_ok": True}
    P["exit_1000"] = {"fn": open_time_exit(600, "at_1000"), "family": "open", "hist_ok": True}
    P["half_1R_hold_open"] = {"fn": hybrid_partial_1r(0.5, open_time_exit(575, "at_935")),
                              "family": "hybrid", "hist_ok": True}
    P["quarter_run_trail"] = {"fn": hybrid_partial_1r(0.25, trail_bar_lows(1)),
                              "family": "hybrid", "hist_ok": True}
    P["partial_5m_hold_open"] = {"fn": hybrid_partial_1r(0.5, time_exit(5)),
                                 "family": "hybrid", "hist_ok": True}
    return P

POLICIES = build_policies()
