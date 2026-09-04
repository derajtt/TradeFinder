"""Contracts shared by every lab strategy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

# Families group correlated ideas so an ensemble cannot count four flavours of
# "momentum" as four independent confirmations.
FAMILIES = ["momentum", "mean_reversion", "trend", "breakout", "volatility",
            "volume", "structure", "relative_strength", "statistical", "event",
            "session", "adaptive"]

STAGES = ["RESEARCH", "BACKTESTING", "VALIDATION", "PAPER_TRADING",
          "PROMISING", "PRODUCTION_CANDIDATE", "FAILED"]


@dataclass
class StrategyMeta:
    id: str
    name: str
    family: str
    category: str
    hypothesis: str                      # WHY there should be an edge
    markets: List[str]                   # stocks | etf | crypto | index | options
    timeframes: List[str]                # 5min | 15min | 30min | 1hour | 4hour | 1day
    hold: str                            # scalp | intraday | swing
    stop_method: str                     # atr | swing_low | structure | vwap | stdev | trailing
    params: Dict[str, Any] = field(default_factory=dict)
    param_grid: Dict[str, List[Any]] = field(default_factory=dict)
    regimes_on: Optional[List[str]] = None   # None = all; else the regimes it trades
    max_hold_bars: int = 78
    version: str = "1.0.0"


@dataclass
class Signal:
    """What a strategy emits. Levels are absolute prices; `reasons` is the
    plain-English basis; `confidence` is 0-100 and strategy-specific."""
    direction: str
    entry: float
    stop: float
    target1: float
    target2: float
    confidence: float
    reasons: List[str]
    invalidation: str
    expected_bars: int
    trailing: Optional[Dict[str, Any]] = None
    features: Dict[str, Any] = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr1(self) -> float:
        return abs(self.target1 - self.entry) / self.risk if self.risk else 0.0


# ctx keys: bars (list of {o,h,l,c,v,time,minute_of_day}) for the working
# timeframe up to and including the current bar; daily (prior sessions);
# spy_daily; regime (str); session (str); market (str); symbol (str)
SignalFn = Callable[[Dict[str, Any], Dict[str, Any]], Optional[Signal]]
