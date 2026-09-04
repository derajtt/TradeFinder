"""Catalyst reaction, live-only.

Hypothesis: a fresh material filing (8-K) or headline creates a burst of
informed and imitative demand the market cannot absorb instantly: market
makers widen and back away, momentum accounts and news-scanning algos buy the
print, and shorts caught by the headline cover. While relative volume is still
several times normal and price holds above session VWAP, the average buyer
since the news is in profit and keeps adding, so the reaction extends; the
stop sits under VWAP because a return below it means the post-news buyers are
under water and the flow reverses. The catalyst arrives in ctx["catalyst"]
(form, items, age_min, optionally grade) from the live SEC/news feed. There is
no catalyst history in the data plan, so backtests will report "insufficient
data" until one accumulates; this strategy is forward-tested live only.
Falsified if fresh-catalyst entries with RVOL >= 3 above VWAP show no excess
return over the next 30-90 minutes versus matched non-catalyst volume spikes.
"""
from typing import Any, Dict, List, Optional, Sequence

from ..base import Signal, StrategyMeta
from ...strategy.indicators import atr, session_vwap

META = StrategyMeta(
    id="s30_catalyst_reaction_live_only",
    name="Catalyst Reaction (live only)",
    family="event",
    category="catalyst_reaction",
    hypothesis=(__doc__ or "").strip(),
    markets=["stocks", "etf"],
    timeframes=["5min", "15min"],
    hold="intraday",
    stop_method="vwap",
    params={"max_age_min": 30, "rvol_min": 3.0, "vwap_buffer_atr": 0.25},
    param_grid={"max_age_min": [15, 30, 45], "rvol_min": [2.0, 3.0, 4.0],
                "vwap_buffer_atr": [0.1, 0.25, 0.5]},
    regimes_on=None,
    max_hold_bars=18,
    version="1.0.0",
)

# 8-K item codes -> materiality points; negatives are dilution/termination items.
ITEM_POINTS = {"1.01": 20, "2.01": 20, "2.02": 18, "8.01": 12, "7.01": 8, "5.02": 6,
               "5.03": 4, "3.02": -15, "1.02": -10, "2.04": -10, "4.02": -20}
NEWS_FORMS = ("news", "pr", "press_release", "headline")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _day_key(b: Dict[str, Any]) -> int:
    return int(b["time"]) - int(b.get("minute_of_day") or 0) * 60


def _sessions(bars: Sequence[dict]) -> List[List[dict]]:
    out: List[List[dict]] = []
    for b in bars:
        if out and _day_key(out[-1][-1]) == _day_key(b):
            out[-1].append(b)
        else:
            out.append([b])
    return out


def _catalyst_score(cat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Materiality points (0-30) from the form and 8-K item codes; None when the
    filing is not a reaction catalyst (dilution, termination, rejected grade).
    `items` may be a list or a comma/semicolon separated string ("1.01,9.01")."""
    form = str(cat.get("form") or cat.get("form_type") or "").strip()
    raw = cat.get("items")
    raw = raw if isinstance(raw, (list, tuple)) else str(raw or "").replace(";", ",").split(",")
    items = [str(x).strip() for x in raw if str(x).strip()]
    if form.upper().startswith("8-K"):
        pts = sum(ITEM_POINTS.get(i, 3) for i in items) if items else 8
    elif form.lower() in NEWS_FORMS:
        pts = 12
    else:
        return None
    grade = str(cat.get("grade") or "").upper()
    if grade == "R" or pts <= 0:
        return None
    return {"form": form, "items": items, "points": min(30, pts + {"A": 10, "B": 5}.get(grade, 0)),
            "grade": grade or "n/a"}


def _rvol(days: List[List[dict]], bars: Sequence[dict], m_now: int) -> float:
    """Cumulative session volume vs the same time of day over prior sessions;
    falls back to bar volume vs the trailing 20-bar mean when history is short."""
    cum_today = sum(float(b.get("v") or 0) for b in days[-1])
    priors = []
    for d in days[-11:-1]:                       # up to 10 prior sessions
        cum = sum(float(b.get("v") or 0) for b in d if int(b["minute_of_day"]) <= m_now)
        if cum > 0:
            priors.append(cum)
    if len(priors) >= 2:
        return cum_today / (sum(priors) / len(priors))
    vols = [float(b.get("v") or 0) for b in bars[-21:-1]]
    base = sum(vols) / len(vols) if vols else 0.0
    return float(bars[-1].get("v") or 0) / base if base > 0 else 0.0


def signal(ctx: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Signal]:
    cat = ctx.get("catalyst")
    if not isinstance(cat, dict):
        return None                            # live-only: no catalyst, no trade
    bars: List[dict] = ctx["bars"]
    if len(bars) < 20:
        return None
    p = {k: cfg.get(k, v) for k, v in META.params.items()}
    age, max_age = cat.get("age_min"), float(p["max_age_min"])
    if not isinstance(age, (int, float)) or isinstance(age, bool) or not 0 <= age <= max_age:
        return None
    age = float(age)
    score = _catalyst_score(cat)
    if score is None:
        return None
    days, cur = _sessions(bars), bars[-1]
    entry = float(cur["c"])
    rvol = _rvol(days, bars, int(cur["minute_of_day"]))
    rvol_min = float(p["rvol_min"])
    if rvol < rvol_min:
        return None
    vwap = session_vwap(days[-1])
    a = atr(bars, 14)
    if vwap is None or not a or a <= 0 or entry <= vwap:
        return None
    stop = vwap - float(p["vwap_buffer_atr"]) * a
    risk = entry - stop
    t1, t2 = entry + 1.5 * risk, entry + 3.0 * risk
    dist_atr = (entry - vwap) / a
    session = str(ctx.get("session"))
    comp = {
        "freshness": 25 * _clamp(1.0 - age / max_age),
        "materiality": float(score["points"]),
        "relative_volume": 20 * _clamp((rvol - rvol_min) / (3.0 * rvol_min)),
        "vwap_position": 15 * _clamp(dist_atr / 0.5) if dist_atr <= 2.0 else 5.0,
        "session": {"premarket": 10.0, "open": 10.0, "midday": 5.0, "power_hour": 5.0}.get(
            session, 0.0),
    }
    conf = round(sum(comp.values()), 1)
    items_txt = (" items " + ", ".join(score["items"])) if score["items"] else ""
    reasons = [
        f"{score['form']}{items_txt} is {age:.0f} min old (limit {max_age:.0f} min)",
        f"Catalyst materiality {score['points']:.0f}/30 from item codes, grade {score['grade']}",
        f"Relative volume {rvol:.1f}x the same time of day over prior sessions (min {rvol_min:.1f})",
        f"Price {entry:.2f} is {dist_atr:.2f} ATR ({a:.2f}) above session VWAP {vwap:.2f}",
        f"Stop {stop:.2f} sits {p['vwap_buffer_atr']} ATR under VWAP; targets {t1:.2f} / {t2:.2f}",
    ]
    return Signal(
        direction="long", entry=entry, stop=stop, target1=t1, target2=t2,
        confidence=conf, reasons=reasons,
        invalidation=(f"A close below session VWAP ({vwap:.2f}) puts the post-news buyers "
                      "under water and ends the reaction trade."),
        expected_bars=4 if str(ctx.get("timeframe")) == "15min" else 12,   # ~1 hour
        trailing={"type": "atr", "mult": 1.5},
        features={"catalyst_form": score["form"], "catalyst_items": score["items"],
                  "catalyst_age_min": age, "catalyst_points": score["points"],
                  "catalyst_grade": score["grade"], "rvol": rvol, "vwap": vwap,
                  "vwap_dist_atr": dist_atr, "atr": a, "session": session,
                  "conf_components": comp},
    )
