"""Executable pricing: simulated fills from the live book, never the last print.
Buys fill at the ask plus configured slippage; exits at the bid minus slippage."""
from typing import Any, Dict, Optional

DEFAULTS = {
    "slippage_pct": 0.4,        # conservative marketable-limit assumption
    "commission_usd": 0.0,      # configurable transaction cost per fill
    "min_quote_size_usd": 1000, # tier can raise this
}


def spread_metrics(bid: Optional[float], ask: Optional[float]):
    if not bid or not ask or ask < bid or bid <= 0:
        return None, None
    mid = (bid + ask) / 2.0
    return round(ask - bid, 4), round((ask - bid) / mid * 100.0, 3)


def simulate_buy_fill(bid, ask, bid_size, ask_size, price_tier,
                      cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    c = dict(DEFAULTS)
    c.update(cfg or {})
    out: Dict[str, Any] = {"filled": False, "fill_price": None, "no_fill_reason": None}
    sp_usd, sp_pct = spread_metrics(bid, ask)
    out["spread_usd"], out["spread_pct"] = sp_usd, sp_pct
    if sp_usd is None:
        out["no_fill_reason"] = "no_valid_two_sided_quote"
        return out
    ask_usd = (ask or 0) * (ask_size or 0)
    out["quote_size_usd"] = round(ask_usd, 2)
    min_size = (price_tier or {}).get("min_quote_size_usd", c["min_quote_size_usd"])
    if ask_usd < min_size:
        out["no_fill_reason"] = f"insufficient_ask_liquidity(${ask_usd:.0f}<${min_size})"
        return out
    max_sp = (price_tier or {}).get("max_spread_pct", 5.0)
    if sp_pct is not None and sp_pct > max_sp:
        out["no_fill_reason"] = f"spread_{sp_pct:.1f}pct_above_tier_max_{max_sp}pct"
        return out
    out["filled"] = True
    out["slippage_pct"] = c["slippage_pct"]
    out["fill_price"] = round(ask * (1 + c["slippage_pct"] / 100.0), 5)
    return out


def simulate_sell_price(bid: Optional[float], cfg: Optional[Dict[str, Any]] = None):
    if not bid or bid <= 0:
        return None
    c = dict(DEFAULTS)
    c.update(cfg or {})
    return round(bid * (1 - c["slippage_pct"] / 100.0), 5)
