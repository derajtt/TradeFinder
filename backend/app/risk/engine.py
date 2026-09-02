"""Platform-wide risk layer.

This sits ON TOP of the strategy layer. It never changes when a strategy fires
or what it considers a valid setup — it converts a raw signal into a position
the account can actually carry, and refuses the trade when the plan does not
justify the risk.

The central rule: position size is derived FROM the stop distance. The stop
comes from market structure; size adapts to it. Never the reverse — choosing a
tight stop to justify a big position is how accounts die.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

RISK_DEFAULTS: Dict[str, Any] = {
    "account_equity": 10000.0,
    "default_risk_pct": 1.0,
    "max_risk_pct": 2.0,
    "max_total_open_risk_pct": 3.0,
    "max_sector_risk_pct": 2.0,
    "max_correlated_risk_pct": 2.0,
    "daily_loss_limit_pct": 3.0,
    "weekly_loss_limit_pct": 6.0,
    "max_position_pct": 100.0,      # of equity in any one position (cash cap)
    "min_rr": 1.5,
    "preferred_rr": 2.0,
    "slippage_pct": 0.05,
    "commission_pct": 0.02,
    "allow_shorts": True,
    "allow_fractional": False,
    "consecutive_loss_trigger": 3,
    "consecutive_loss_pause": 5,
    "drawdown_reduce_25_pct": 5.0,
    "drawdown_reduce_50_pct": 8.0,
    "drawdown_pause_pct": 10.0,
    "leverage": 1.0,
    "reset_timezone": "America/New_York",
    "partial_plan": [0.35, 0.35, 0.30],
}

# Strategies that keep their own tested execution model. The risk layer still
# renders entry/stop/targets/size for them, but does not override their rules.
EXEMPT_STRATEGIES = {"premarket_scalper"}
EXEMPT_RISK_MODELS = {"scalper", "WINGED", "winged"}


def is_exempt(model_id: str, model_meta: Optional[dict] = None) -> bool:
    if model_id in EXEMPT_STRATEGIES:
        return True
    rm = (model_meta or {}).get("risk_model")
    return rm in EXEMPT_RISK_MODELS


# --------------------------------------------------------- dynamic risk ----

MODIFIER_TABLE = [
    ("high_volatility", 0.75, "Volatility is unusually high for this instrument"),
    ("wide_spread", 0.50, "Spread is wide relative to the planned risk"),
    ("low_liquidity", 0.50, "Traded volume is thin"),
    ("event_risk", 0.50, "A scheduled event sits inside the expected hold"),
    ("strategy_conflict", 0.50, "Another strategy is calling the opposite direction"),
    ("weak_signal", 0.50, "Setup quality is below the strategy's strong band"),
    ("correlated_exposure", 0.60, "Highly correlated with existing open risk"),
    ("loss_streak", 0.50, "Consecutive losses on this strategy"),
    ("drawdown_25", 0.75, "Account is in drawdown"),
    ("drawdown_50", 0.50, "Account drawdown is significant"),
]
MODIFIERS = {k: (m, why) for k, m, why in MODIFIER_TABLE}


def dynamic_risk(base_pct: float, flags: List[str],
                 max_pct: float) -> Dict[str, Any]:
    """Multiply down for unfavourable conditions. Never multiplies up past the
    user's configured maximum — risk only ever shrinks from the baseline."""
    mult, reasons = 1.0, []
    for f in flags:
        if f in MODIFIERS:
            m, why = MODIFIERS[f]
            mult *= m
            reasons.append(why)
    adj = min(base_pct * mult, max_pct)
    return {"base_pct": round(base_pct, 4), "adjusted_pct": round(adj, 4),
            "multiplier": round(mult, 3), "reasons": reasons,
            "reduced": adj < base_pct - 1e-9}


# ------------------------------------------------------ position sizing ----

def size_position(equity: float, risk_pct: float, entry: float, stop: float,
                  direction: str, *, buying_power: Optional[float] = None,
                  max_position_pct: float = 100.0, allow_fractional: bool = False,
                  leverage: float = 1.0, slippage_pct: float = 0.05,
                  commission_pct: float = 0.02,
                  lot: float = 1.0) -> Dict[str, Any]:
    """Size from the stop distance, then clamp to what the account can carry."""
    if entry <= 0 or equity <= 0:
        return {"valid": False, "reason": "account equity or entry price is not usable"}
    risk_per_unit = (entry - stop) if direction == "long" else (stop - entry)
    if risk_per_unit <= 0:
        return {"valid": False,
                "reason": "stop is on the wrong side of entry — trade plan rejected"}

    risk_dollars = equity * risk_pct / 100.0
    raw_qty = risk_dollars / risk_per_unit

    bp = buying_power if buying_power is not None else equity * leverage
    cap_by_bp = bp / entry if entry > 0 else 0
    cap_by_pos = (equity * max_position_pct / 100.0 * leverage) / entry

    qty = min(raw_qty, cap_by_bp, cap_by_pos)
    if allow_fractional:
        qty = math.floor(qty * 1e6) / 1e6
    else:
        qty = math.floor(qty / lot) * lot
    if qty <= 0:
        return {"valid": False,
                "reason": "position rounds to zero — the stop is too wide for this "
                          "account at the configured risk"}

    binding = "risk"
    if abs(qty - cap_by_bp) < 1e-9 and cap_by_bp < raw_qty:
        binding = "buying_power"
    elif abs(qty - cap_by_pos) < 1e-9 and cap_by_pos < raw_qty:
        binding = "max_position_size"

    notional = qty * entry
    planned_loss = qty * risk_per_unit
    slip = notional * slippage_pct / 100.0
    comm = notional * commission_pct / 100.0 * 2      # both sides
    worst = planned_loss + slip + comm

    return {
        "valid": True,
        "quantity": round(qty, 8),
        "risk_per_unit": round(risk_per_unit, 6),
        "risk_dollars": round(risk_dollars, 2),
        "planned_loss": round(planned_loss, 2),
        "position_notional": round(notional, 2),
        "capital_required": round(notional / max(leverage, 1e-9), 2),
        "leverage": leverage,
        "estimated_costs": round(slip + comm, 2),
        "worst_case_with_costs": round(worst, 2),
        "pct_of_equity": round(notional / equity * 100, 2),
        "binding_constraint": binding,
        "uncapped_quantity": round(raw_qty, 6),
    }


# -------------------------------------------------------- portfolio risk ----

CORRELATION_GROUPS = {
    "semis": {"NVDA", "AMD", "SMH", "MU", "AVGO", "INTC", "TSM", "QCOM"},
    "megacap_tech": {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "QQQ"},
    "broad_market": {"SPY", "QQQ", "IWM", "DIA", "VTI"},
    "energy": {"XLE", "XOP", "XOM", "CVX", "OXY"},
    "financials": {"XLF", "JPM", "BAC", "GS", "WFC", "C"},
    "crypto_proxy": {"BTCUSD", "ETHUSD", "SOLUSD", "MARA", "RIOT", "COIN", "MSTR"},
    "ev": {"TSLA", "RIVN", "LCID", "NIO"},
}


def correlation_group(symbol: str) -> Optional[str]:
    s = (symbol or "").upper()
    for g, members in CORRELATION_GROUPS.items():
        if s in members:
            return g
    return None


def portfolio_risk(open_positions: List[dict], equity: float) -> Dict[str, Any]:
    """Open risk is what is still exposed if every stop fills at its stop."""
    total = 0.0
    by_group: Dict[str, float] = {}
    by_sector: Dict[str, float] = {}
    by_direction: Dict[str, float] = {"long": 0.0, "short": 0.0}
    for p in open_positions:
        r = float(p.get("open_risk_dollars") or 0)
        total += r
        g = correlation_group(p.get("symbol", "")) or "ungrouped"
        by_group[g] = by_group.get(g, 0) + r
        sec = p.get("sector") or "unknown"
        by_sector[sec] = by_sector.get(sec, 0) + r
        by_direction[p.get("direction", "long")] = \
            by_direction.get(p.get("direction", "long"), 0) + r
    pct = (total / equity * 100) if equity > 0 else 0
    return {
        "open_positions": len(open_positions),
        "total_open_risk": round(total, 2),
        "total_open_risk_pct": round(pct, 3),
        "by_correlation_group": {k: round(v, 2) for k, v in by_group.items()},
        "by_correlation_group_pct": {k: round(v / equity * 100, 3)
                                     for k, v in by_group.items()} if equity else {},
        "by_sector": {k: round(v, 2) for k, v in by_sector.items()},
        "by_direction": {k: round(v, 2) for k, v in by_direction.items()},
    }


def circuit_breaker_state(settings: Dict[str, Any], *, daily_pnl_pct: float = 0.0,
                          weekly_pnl_pct: float = 0.0,
                          drawdown_pct: float = 0.0,
                          consecutive_losses: int = 0) -> Dict[str, Any]:
    """Paper signals ALWAYS keep being recorded — only new trade
    recommendations pause. Silence in the data would destroy the research."""
    blocks, warnings, mult = [], [], 1.0
    dl = float(settings.get("daily_loss_limit_pct", 3.0))
    wl = float(settings.get("weekly_loss_limit_pct", 6.0))
    if daily_pnl_pct <= -dl:
        blocks.append(f"Daily loss limit reached ({daily_pnl_pct:.2f}% of {-dl:.2f}%). "
                      f"New trade entries are paused until the next session.")
    elif daily_pnl_pct <= -dl * 0.7:
        warnings.append(f"Approaching the daily loss limit ({daily_pnl_pct:.2f}% "
                        f"of {-dl:.2f}%).")
    if weekly_pnl_pct <= -wl:
        blocks.append(f"Weekly loss limit reached ({weekly_pnl_pct:.2f}% of {-wl:.2f}%).")

    if drawdown_pct >= float(settings.get("drawdown_pause_pct", 10.0)):
        blocks.append(f"Account drawdown {drawdown_pct:.1f}% — live recommendations "
                      f"paused. Paper testing continues.")
    elif drawdown_pct >= float(settings.get("drawdown_reduce_50_pct", 8.0)):
        mult *= 0.5
        warnings.append(f"Drawdown {drawdown_pct:.1f}% — risk halved.")
    elif drawdown_pct >= float(settings.get("drawdown_reduce_25_pct", 5.0)):
        mult *= 0.75
        warnings.append(f"Drawdown {drawdown_pct:.1f}% — risk reduced 25%.")

    pause_at = int(settings.get("consecutive_loss_pause", 5))
    trig = int(settings.get("consecutive_loss_trigger", 3))
    if consecutive_losses >= pause_at:
        blocks.append(f"{consecutive_losses} consecutive losses — strategy flagged "
                      f"for review. Paper signals continue to be recorded.")
    elif consecutive_losses >= trig:
        mult *= 0.5
        warnings.append(f"{consecutive_losses} consecutive losses — risk halved.")

    return {"paused": bool(blocks), "blocks": blocks, "warnings": warnings,
            "risk_multiplier": round(mult, 3),
            "paper_recording": True}


# ------------------------------------------------------ the full plan ------

def build_trade_plan(signal: Dict[str, Any], settings: Dict[str, Any], *,
                     open_positions: Optional[List[dict]] = None,
                     strategy_stats: Optional[Dict[str, Any]] = None,
                     risk_flags: Optional[List[str]] = None,
                     breaker: Optional[Dict[str, Any]] = None,
                     exempt: bool = False) -> Dict[str, Any]:
    """Convert a strategy signal into a complete, checkable trade plan.

    `signal` needs: symbol, direction, entry, stop, targets[{name,price,r}],
    and optionally score/atr/spread_pct. Returns a plan or a NO_TRADE with the
    specific reason, which is itself shown to the user — a refusal teaches.
    """
    st = {**RISK_DEFAULTS, **(settings or {})}
    sym = signal.get("symbol", "")
    d = signal.get("direction", "long")
    entry, stop = signal.get("entry"), signal.get("stop")
    targets = list(signal.get("targets") or [])
    equity = float(st["account_equity"])
    flags = list(risk_flags or [])

    if entry is None or stop is None:
        return {"actionable": False, "status": "TRADE_PLAN_UNAVAILABLE",
                "reason": "signal detected but entry or stop could not be "
                          "determined from current data"}
    if not targets:
        return {"actionable": False, "status": "TRADE_PLAN_UNAVAILABLE",
                "reason": "signal detected but no technically valid profit "
                          "target could be derived"}
    if d == "short" and not st.get("allow_shorts", True):
        return {"actionable": False, "status": "SHORTS_DISABLED",
                "reason": "This is a bearish signal and short selling is "
                          "disabled in your risk settings.",
                "display_as": "EXIT / BEARISH SIGNAL"}

    risk_unit = (entry - stop) if d == "long" else (stop - entry)
    if risk_unit <= 0:
        return {"actionable": False, "status": "BLOCKED_QC",
                "reason": "stop is not on the loss side of entry"}

    # --- reward/risk gate ------------------------------------------------
    for t in targets:
        t["r"] = round(abs(t["price"] - entry) / risk_unit, 2)
    best_r = max(t["r"] for t in targets)
    min_rr = float(st["min_rr"])
    if best_r < min_rr:
        return {"actionable": False, "status": "NO_TRADE_REWARD_TOO_LOW",
                "reason": f"Potential reward does not justify the planned risk.",
                "required_r": min_rr, "available_r": best_r,
                "detail": f"Required: {min_rr}R · Available: {best_r}R",
                "note": "Targets come from market structure. They are never moved "
                        "outward just to manufacture an acceptable ratio."}

    # --- score-driven and condition-driven risk modifiers ----------------
    score = signal.get("score")
    if score is not None and score < 80:
        flags.append("weak_signal")
    if signal.get("spread_pct") is not None and signal["spread_pct"] > 1.0:
        flags.append("wide_spread")

    positions = open_positions or []
    grp = correlation_group(sym)
    if grp:
        same = [p for p in positions
                if correlation_group(p.get("symbol", "")) == grp
                and p.get("direction") == d]
        if len(same) >= 2:
            flags.append("correlated_exposure")

    base = float(signal.get("risk_pct_override") or st["default_risk_pct"])
    dyn = dynamic_risk(base, flags, float(st["max_risk_pct"]))
    risk_pct = dyn["adjusted_pct"] * float((breaker or {}).get("risk_multiplier", 1.0))

    # --- portfolio ceilings ----------------------------------------------
    pf = portfolio_risk(positions, equity)
    headroom = float(st["max_total_open_risk_pct"]) - pf["total_open_risk_pct"]
    portfolio_notes = []
    if headroom <= 0:
        return {"actionable": False, "status": "NO_TRADE_PORTFOLIO_RISK",
                "reason": f"Total open risk is already "
                          f"{pf['total_open_risk_pct']:.2f}% of equity, at the "
                          f"{st['max_total_open_risk_pct']:.1f}% ceiling.",
                "portfolio": pf}
    if risk_pct > headroom:
        portfolio_notes.append(
            f"Risk trimmed to {headroom:.2f}% to stay inside the "
            f"{st['max_total_open_risk_pct']:.1f}% total open-risk ceiling.")
        risk_pct = headroom
    if grp:
        gp = pf["by_correlation_group_pct"].get(grp, 0.0)
        cap = float(st["max_correlated_risk_pct"])
        if gp + risk_pct > cap:
            allowed = max(0.0, cap - gp)
            if allowed <= 0:
                return {"actionable": False, "status": "NO_TRADE_CORRELATION",
                        "reason": f"Correlated exposure in {grp.replace('_',' ')} is "
                                  f"already {gp:.2f}%, at the {cap:.1f}% ceiling.",
                        "portfolio": pf}
            portfolio_notes.append(
                f"Risk trimmed to {allowed:.2f}% — {grp.replace('_',' ')} exposure "
                f"is already {gp:.2f}% of the {cap:.1f}% correlated ceiling.")
            risk_pct = allowed

    sizing = size_position(
        equity, risk_pct, entry, stop, d,
        buying_power=st.get("buying_power"),
        max_position_pct=float(st["max_position_pct"]),
        allow_fractional=bool(st["allow_fractional"]) or sym.endswith("USD"),
        leverage=float(st.get("leverage", 1.0)),
        slippage_pct=float(st["slippage_pct"]),
        commission_pct=float(st["commission_pct"]),
    )
    if not sizing.get("valid"):
        return {"actionable": False, "status": "TRADE_PLAN_UNAVAILABLE",
                "reason": sizing.get("reason")}

    # partial-exit plan mapped onto however many targets exist
    plan = list(st.get("partial_plan") or [0.35, 0.35, 0.30])
    n = len(targets)
    if n == 1:
        alloc = [1.0]
    elif n == 2:
        alloc = [plan[0] + plan[1] / 2, 1 - (plan[0] + plan[1] / 2)]
    else:
        alloc = plan[:n]
        alloc[-1] = round(1 - sum(alloc[:-1]), 4)
    for t, a in zip(targets, alloc):
        t["allocation_pct"] = round(a * 100)
        t["qty"] = round(sizing["quantity"] * a, 8 if st["allow_fractional"] else 0)
        t["profit_at_target"] = round(abs(t["price"] - entry) * sizing["quantity"] * a, 2)

    expected = None
    if strategy_stats and strategy_stats.get("trades", 0) >= 30:
        wr = (strategy_stats.get("win_rate") or 0) / 100.0
        aw, al = strategy_stats.get("avg_win_pct"), strategy_stats.get("avg_loss_pct")
        if aw is not None and al is not None:
            expected = {"basis": strategy_stats.get("basis", "backtest"),
                        "sample": strategy_stats.get("trades"),
                        "sample_label": strategy_stats.get("sample"),
                        "observed_win_rate_pct": strategy_stats.get("win_rate"),
                        "expectancy_pct": round(wr * aw - (1 - wr) * al, 4),
                        "note": "Observed historical frequency on this sample. "
                                "Not a probability that this trade wins."}

    return {
        "actionable": True, "status": "READY",
        "symbol": sym, "direction": d,
        "entry": round(entry, 6), "stop": round(stop, 6),
        "stop_basis": signal.get("stop_basis"),
        "targets": targets,
        "risk": {
            "account_equity": equity,
            "configured_risk_pct": base,
            "applied_risk_pct": round(risk_pct, 4),
            "max_risk_pct": float(st["max_risk_pct"]),
            "risk_reduced": round(risk_pct, 4) < base - 1e-9,
            "reduction_reasons": dyn["reasons"] + portfolio_notes
                                 + list((breaker or {}).get("warnings", [])),
            **{k: v for k, v in sizing.items() if k != "valid"},
        },
        "rr": {"primary": targets[0]["r"], "best": best_r,
               "min_required": min_rr, "preferred": float(st["preferred_rr"]),
               "meets_preferred": best_r >= float(st["preferred_rr"])},
        "portfolio": pf,
        "portfolio_after": {
            "total_open_risk_pct": round(pf["total_open_risk_pct"] + risk_pct, 3),
            "ceiling_pct": float(st["max_total_open_risk_pct"]),
        },
        "expected_value": expected,
        "exempt_strategy": exempt,
        "entry_zone": signal.get("entry_zone"),
        "score": signal.get("score"),
        "invalidation": signal.get("invalidation"),
        "expires_at": signal.get("expires_at"),
    }
