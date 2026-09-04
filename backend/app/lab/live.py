"""Quant Lab LIVE worker: forward paper-trades the lab strategies a human has
promoted.

Every lab strategy whose LabStrategy.stage is PAPER_TRADING, PROMISING or
PRODUCTION_CANDIDATE (read from the database on every pass, so a stage change
in the Lab takes effect on the next cycle) is evaluated on the live universe
with the same causal context the harness builds: bars up to and including the
forming bar, prior-session dailies, SPY dailies, regime and session. Each long
signal opens a position in a $10,000 fleet ledger under the dynamic model id
``lab_<strategy_id>`` -- so the Lab competes on the same board and is settled
by the same exit engine (stops, targets, the day-trading flatten) as every
other model -- and is written to lab_trades (cohort "paper") for permanence.

What the live pass can and cannot see, stated rather than hidden:
  * 5-minute bars are today's session only (that is what the plan serves
    live), so 15min/30min/1hour series are resampled from them and are short;
    a strategy that needs more history returns None and is counted as scanned.
  * 1day series are prior-session dailies plus a synthetic today bar built
    from today's regular-session 5-minute bars; 4hour is not evaluated live.
  * The fleet ledger is long-only. Long signals get a ledger position; crypto
    shorts are recorded in lab_trades without a ledger position (deduped per
    day); stock/ETF shorts are dropped, as equities are long-only in paper.
No network call happens inside a strategy: every bar comes from the scheduler's
shared, TTL-cached ModelContext.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select

from ..db import SessionLocal
from ..models import LabStrategy, LabTrade, PaperPosition
from ..sse import broadcaster
from ..strategy import platform as mplat
from ..strategy.indicators import atr, sma
from ..strategy.registry import CRYPTO_UNIVERSE, ETF_UNIVERSE, MODELS
from ..util.timeutil import ET, now_et, now_utc
from . import registry
from .base import Signal, StrategyMeta

log = logging.getLogger("lab.live")

LIVE_STAGES = ("PAPER_TRADING", "PROMISING", "PRODUCTION_CANDIDATE")
LAB_ENGINE = "lab"
LAB_COLOR = "#a3e635"
MODEL_PREFIX = "lab_"
WORKER_HB = "quant_lab"                      # heartbeat for the worker itself
TF_MINUTES = {"5min": 5, "15min": 15, "30min": 30, "1hour": 60, "4hour": 240, "1day": 1440}
LIVE_TIMEFRAMES = ("5min", "15min", "30min", "1hour", "1day")
LAB_REGIMES = ("trend_up", "trend_down", "range", "high_vol", "low_vol", "bear")
RTH_OPEN, RTH_CLOSE = 570, 960               # minutes after 00:00 ET
OPEN_END, POWER_HOUR = 630, 900              # 09:30-10:29 "open"; 15:00+ "power_hour"
LOW_VOL_ATR_PCT = 0.8                        # SPY ATR(14) under 0.8% of price = quiet tape
HORIZON = {"scalp": "minutes", "intraday": "intraday", "swing": "days"}
# ETF_UNIVERSE mixes index/sector ETFs with large caps; label each by what it is.
CORE_ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLE", "XLF", "XLK", "XLV", "XLI",
             "SMH", "GDX", "TLT", "GLD", "XOP"}


# ── fleet registration ───────────────────────────────────────────────────────

def fleet_model_id(strategy_id: str) -> str:
    return MODEL_PREFIX + strategy_id


def first_sentence(text: str) -> str:
    """First sentence of a hypothesis, minus any leading 'Hypothesis:' label.
    A terminator only counts when followed by whitespace, so '2.6%' survives."""
    t = " ".join((text or "").split())
    t = re.sub(r"^hypothesis:\s*", "", t, flags=re.I)
    m = re.search(r"^(.*?[.!?])(?=\s|$)", t)
    return (m.group(1) if m else t).strip()[:240]


def ensure_fleet_model(meta: StrategyMeta) -> str:
    """Register ``lab_<id>`` in the fleet registry if absent and return the id.
    Idempotent: an existing spec is never replaced. Registration is what gives
    the strategy a $10k ledger and puts its positions in front of the fleet's
    settle_positions, which filters on MODELS.keys()."""
    mid = fleet_model_id(meta.id)
    if mid not in MODELS:
        MODELS[mid] = {
            "name": meta.name, "engine": LAB_ENGINE, "build": True,
            "asset_classes": list(meta.markets), "cadence": "intraday",
            "horizon": HORIZON.get(meta.hold, "intraday"), "color": LAB_COLOR,
            "own_worker": True, "risk_model": "standard",
            "edge": first_sentence(meta.hypothesis),
            "universe": "Quant Lab live universe for " + "/".join(meta.markets)
                        + ": movers + core list (+ crypto)",
            "lab": True, "lab_strategy_id": meta.id, "family": meta.family,
            "timeframes": list(meta.timeframes), "hypothesis": meta.hypothesis,
            "data_notes": ("Quant Lab forward paper test, stage-gated from the Lab. "
                           "Intraday series are built from today's 5-minute bars only."),
        }
    return mid


# ── bar shaping (pure, causal) ───────────────────────────────────────────────

def m5_to_lab_bars(m5: Sequence[dict]) -> List[dict]:
    """ModelContext 5-minute bars -> contract bars {o,h,l,c,v,time,minute_of_day}."""
    out: List[dict] = []
    for b in m5:
        ts = b.get("ts")
        if ts is None or b.get("c") is None:
            continue
        try:
            t = int(ts.timestamp())
            mod = b.get("minute_of_day")
            if mod is None:
                et = ts.astimezone(ET)
                mod = et.hour * 60 + et.minute
            out.append({"o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]),
                        "c": float(b["c"]), "v": float(b.get("v") or 0),
                        "time": t, "minute_of_day": int(mod)})
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["time"])
    return out


def daily_to_lab_bars(daily: Sequence[dict], today: str) -> List[dict]:
    """Prior-session dailies only: any row dated on/after ``today`` (the
    forming session, which the EOD endpoint includes intraday) is dropped."""
    out: List[dict] = []
    for b in daily:
        d = str(b.get("date") or "")[:10]
        if not d or d >= today or b.get("c") is None:
            continue
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=ET)
            out.append({"o": float(b["o"]), "h": float(b["h"]), "l": float(b["l"]),
                        "c": float(b["c"]), "v": float(b.get("v") or 0),
                        "time": int(dt.timestamp())})
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["time"])
    return out


def _day_index(b: dict) -> int:
    return (b["time"] - b["minute_of_day"] * 60) // 86400


def resample(bars: Sequence[dict], timeframe: str, anchor_minute: int = 0) -> List[dict]:
    """Causal N-minute aggregation of 5-minute bars. A bucket is
    (minute_of_day - anchor) // N within one calendar day; the last bucket is
    the forming bar. Aggregating a prefix reproduces the full series' completed
    buckets exactly, so signal(bars[:i+1]) is reproducible from history."""
    n = TF_MINUTES[timeframe]
    if n <= 5:
        return [dict(b) for b in bars]
    out: List[dict] = []
    key_prev: Optional[Tuple[int, int]] = None
    for b in bars:
        key = (_day_index(b), (b["minute_of_day"] - anchor_minute) // n)
        if key != key_prev:
            start = anchor_minute + key[1] * n
            out.append({"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"],
                        "time": b["time"] - (b["minute_of_day"] - start) * 60,
                        "minute_of_day": start})
            key_prev = key
        else:
            cur = out[-1]
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
            cur["v"] += b["v"]
    return out


def today_bar(bars5: Sequence[dict], rth_only: bool) -> Optional[dict]:
    """Synthetic daily bar for the forming session (RTH prints only for
    equities, matching the EOD convention; everything for crypto)."""
    src = [b for b in bars5 if RTH_OPEN <= b["minute_of_day"] < RTH_CLOSE] if rth_only else list(bars5)
    if not src:
        return None
    return {"o": src[0]["o"], "h": max(b["h"] for b in src), "l": min(b["l"] for b in src),
            "c": src[-1]["c"], "v": sum(b["v"] for b in src),
            "time": src[0]["time"] - src[0]["minute_of_day"] * 60, "minute_of_day": 0}


def frame_bars(bars5: Sequence[dict], prior_daily: Sequence[dict], timeframe: str,
               market: str) -> Optional[List[dict]]:
    """Working-timeframe series ending in the forming bar, or None when it
    cannot be built honestly (a 1day series with no regular-session print yet)."""
    if timeframe == "1day":
        tb = today_bar(bars5, rth_only=(market != "crypto"))
        return None if tb is None else list(prior_daily) + [tb]
    if timeframe not in TF_MINUTES or timeframe not in LIVE_TIMEFRAMES:
        return None
    # equities anchor to the 09:30 open so 1-hour bars run 09:30-10:29 rather
    # than mixing premarket and regular prints; crypto is clock-aligned
    anchor = 0 if market == "crypto" else RTH_OPEN % TF_MINUTES[timeframe]
    return resample(bars5, timeframe, anchor)


def session_of(minute_of_day: int, market: str) -> str:
    if market == "crypto":
        return "crypto"
    m = int(minute_of_day)
    if m < RTH_OPEN:
        return "premarket"
    if m < OPEN_END:
        return "open"
    if m < POWER_HOUR:
        return "midday"
    if m < RTH_CLOSE:
        return "power_hour"
    return "afterhours"


def lab_regime(last_regime: Optional[Dict[str, Any]], spy_daily: Sequence[dict]) -> str:
    """Regime controller vocabulary -> lab vocabulary. trend/up -> trend_up;
    trend/down -> bear when SPY is also under its 200-day mean, else
    trend_down; high_risk -> high_vol; range -> low_vol when SPY ATR is quiet,
    else range; uncertain (neither trending nor disorderly) -> range."""
    reg = last_regime or {}
    state = reg.get("state")
    if state == "trend":
        if reg.get("dir") == "up":
            return "trend_up"
        cl = [float(b["c"]) for b in spy_daily if b.get("c") is not None]
        s200 = sma(cl, 200) if len(cl) >= 200 else None
        return "bear" if (s200 and cl[-1] < s200) else "trend_down"
    if state == "high_risk":
        return "high_vol"
    if state == "range":
        ap = reg.get("atr_pct")
        return "low_vol" if (ap is not None and float(ap) < LOW_VOL_ATR_PCT) else "range"
    return "range"


def _assemble(symbol: str, market: str, timeframe: str, bars5: Sequence[dict],
              prior_daily: Sequence[dict], spy_daily: Sequence[dict], regime: str,
              catalyst: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    if not bars5:
        return None
    bars = frame_bars(bars5, prior_daily, timeframe, market)
    if not bars:
        return None
    return {"bars": bars, "daily": list(prior_daily), "spy_daily": list(spy_daily),
            "regime": regime, "session": session_of(bars5[-1]["minute_of_day"], market),
            "market": market, "symbol": symbol, "timeframe": timeframe,
            "catalyst": catalyst}


def build_ctx(*, symbol: str, market: str, timeframe: str, m5: Sequence[dict],
              daily: Sequence[dict], spy_daily: Sequence[dict], regime: str,
              today: str, catalyst: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Contract ctx from raw ModelContext series. Causal by construction: only
    the supplied 5-minute bars are used, the forming bar is bars[-1], and the
    daily series stop at the prior session."""
    return _assemble(symbol, market, timeframe, m5_to_lab_bars(m5),
                     daily_to_lab_bars(daily, today), daily_to_lab_bars(spy_daily, today),
                     regime, catalyst)


# ── selection helpers ────────────────────────────────────────────────────────

def timeframes_for(meta: StrategyMeta, preferred: Optional[str] = None) -> List[str]:
    """Live-buildable timeframes in the author's order, the stored best first."""
    tfs = [t for t in meta.timeframes if t in LIVE_TIMEFRAMES]
    if preferred in tfs:
        tfs.remove(preferred)
        tfs.insert(0, preferred)
    return tfs


def symbols_for(markets: Sequence[str], universe: Dict[str, List[str]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for mk in ("stocks", "etf", "crypto", "index"):
        if mk not in markets:
            continue
        for s in universe.get(mk, []):
            if s not in seen:
                seen.add(s)
                out.append((s, mk))
    return out


def _jsonable(x: Any) -> Any:
    try:
        return json.loads(json.dumps(x, default=str))
    except (TypeError, ValueError):
        return {"repr": str(x)}


def verdict_from_signal(sig: Signal, meta: StrategyMeta, atr_5m: Optional[float],
                        extra_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The fleet verdict shape record_model_signal expects."""
    return {"action": "buy", "entry": float(sig.entry), "stop": float(sig.stop),
            "target1": float(sig.target1), "target2": float(sig.target2),
            "score": float(sig.confidence), "setup": meta.id,
            "evidence": {"reasons": list(sig.reasons), "invalidation": sig.invalidation,
                         "features": _jsonable(sig.features), "family": meta.family,
                         **(extra_evidence or {})},
            "holding": meta.hold or "intraday", "atr_5m": atr_5m}


def _is_off(v: Any) -> bool:
    return str(v).strip().lower() in ("off", "false", "0", "no")


def _past_cutoff(settings: Dict[str, Any], phase: str, t: datetime) -> bool:
    if phase != "regular":
        return False
    cut = str(settings.get("model_entry_cutoff_et") or "11:30")
    try:
        h, m = (int(x) for x in cut.split(":"))
    except (TypeError, ValueError):
        return False
    return t.hour * 60 + t.minute >= h * 60 + m


# ── database ─────────────────────────────────────────────────────────────────

async def ensure_strategy_rows(db, loaded: Sequence[registry.LoadedStrategy]) -> Dict[str, LabStrategy]:
    """Every loaded strategy needs a LabStrategy row for its stage to be set
    from the Lab. Missing rows are inserted at RESEARCH; existing rows are
    never modified here (stage and params belong to the Lab and the harness)."""
    rows = {r.strategy_id: r for r in (await db.execute(select(LabStrategy))).scalars().all()}
    added = False
    for s in loaded:
        if s.id in rows:
            continue
        m = s.meta
        row = LabStrategy(strategy_id=m.id, name=m.name[:96], family=m.family,
                          category=(m.category or "")[:48], hypothesis=m.hypothesis,
                          markets=list(m.markets), timeframes=list(m.timeframes),
                          hold=m.hold, stop_method=m.stop_method, stage="RESEARCH",
                          stage_reason="registered from code by the live worker; no results yet",
                          params=dict(m.params), version=m.version)
        db.add(row)
        rows[m.id] = row
        added = True
    if added:
        await db.commit()
    return rows


async def open_lab_profiles(db) -> Set[str]:
    """Fleet model ids of open lab positions, so a demoted or renamed strategy
    stays registered until its book is flat and keeps settling."""
    rows = (await db.execute(select(PaperPosition.profile).where(
        PaperPosition.status == "open",
        PaperPosition.profile.like(MODEL_PREFIX + "%")))).all()
    return {r[0] for r in rows}


def _lab_trade_row(meta: StrategyMeta, sig: Signal, symbol: str, market: str, timeframe: str,
                   ctx: Dict[str, Any], cfg: Dict[str, Any], regime: str,
                   features: Dict[str, Any]) -> LabTrade:
    now = now_utc()
    return LabTrade(strategy_id=meta.id, cohort="paper", split="", market=market,
                    symbol=symbol, timeframe=timeframe, direction=sig.direction,
                    signal_time=now, entry_time=now, entry_price=float(sig.entry),
                    stop_price=float(sig.stop), target_1=float(sig.target1),
                    target_2=float(sig.target2), result="", regime=regime,
                    session_bucket=ctx.get("session") or "", confidence=float(sig.confidence),
                    reasons=list(sig.reasons), invalidation=sig.invalidation,
                    features={**_jsonable(sig.features), "expected_bars": sig.expected_bars,
                              "trailing": _jsonable(sig.trailing), **features},
                    params=_jsonable(cfg))


async def _write_lab_trade(meta, sig, symbol, market, timeframe, ctx, cfg, regime, mid, buy_signal) -> None:
    async with SessionLocal() as db:
        pos = (await db.execute(select(PaperPosition).where(
            PaperPosition.signal_id == buy_signal.id))).scalars().first()
        feats = {"fleet_model_id": mid, "buy_signal_uid": buy_signal.signal_uid,
                 "fill": buy_signal.sim_fill_price,
                 "fleet_position": ({"stop": pos.stop, "target1": pos.target1,
                                     "target2": pos.target2, "size_usd": pos.size_usd}
                                    if pos else None)}
        db.add(_lab_trade_row(meta, sig, symbol, market, timeframe, ctx, cfg, regime, feats))
        await db.commit()


async def _record_short(meta, sig, symbol, market, timeframe, ctx, cfg, regime, mid) -> bool:
    """Crypto shorts: lab_trades row only (the fleet ledger is long-only),
    at most one per strategy/symbol/day."""
    day_start = datetime.combine(now_et().date(), dtime(0), tzinfo=ET).astimezone(timezone.utc)
    async with SessionLocal() as db:
        dup = (await db.execute(select(LabTrade.id).where(
            LabTrade.strategy_id == meta.id, LabTrade.symbol == symbol,
            LabTrade.cohort == "paper", LabTrade.direction == "short",
            LabTrade.signal_time >= day_start))).first()
        if dup:
            return False
        db.add(_lab_trade_row(meta, sig, symbol, market, timeframe, ctx, cfg, regime,
                              {"fleet_model_id": mid,
                               "ledger": "none: the fleet paper ledger is long-only; "
                                         "recorded as evidence for bar-based settlement"}))
        await db.commit()
    return True


# ── the cycle ────────────────────────────────────────────────────────────────

async def lab_universe(sched, settings: Dict[str, Any], phase: str) -> Dict[str, List[str]]:
    """Per-market symbol lists. Equities only while the tape is open (prep,
    premarket, regular); crypto always. Movers + the scalper's radar are the
    stock list, the core list is split into ETFs and large caps by what each is."""
    intraday_ok = phase in ("prep", "premarket", "regular")
    stocks: List[str] = []
    etfs: List[str] = []
    if intraday_ok:
        try:
            movers = await sched.mctx.movers(cap=int(settings.get("movers_cap") or 50))
        except Exception:
            movers = []
        radar = [c.get("symbol") for c in (getattr(sched.ctx, "radar_live", None) or [])[:40]
                 if c.get("symbol")]
        core_stocks = [s for s in ETF_UNIVERSE if s not in CORE_ETFS]
        stocks = list(dict.fromkeys(list(movers) + radar + core_stocks))
        etfs = [s for s in ETF_UNIVERSE if s in CORE_ETFS]
    return {"stocks": stocks, "etf": etfs, "crypto": list(CRYPTO_UNIVERSE), "index": []}


async def _take_signal(sched, s, row, mid, sig, symbol, market, timeframe, ctx, cfg, bars5,
                       regime, today, past_cutoff, settings) -> str:
    meta = s.meta
    if sig.direction == "short":
        if market != "crypto":
            return "shorts_dropped"
        return ("shorts_recorded" if await _record_short(
            meta, sig, symbol, market, timeframe, ctx, cfg, regime, mid) else "not_opened")
    if market != "crypto" and past_cutoff:
        return "late"
    atr5 = atr(bars5, 14) if len(bars5) >= 15 else None
    verdict = verdict_from_signal(sig, meta, atr5, {
        "timeframe": timeframe, "session": ctx.get("session"), "lab_regime": regime,
        "regime_state": (getattr(sched, "last_regime", None) or {}).get("state"),
        "stage": row.stage, "params": _jsonable(cfg)})
    try:
        bs = await mplat.record_model_signal(mid, symbol, verdict, float(bars5[-1]["c"]),
                                             today, settings)
    except Exception as e:
        await sched._health("warn", f"lab:{meta.id}", f"{symbol}: ledger: {type(e).__name__}: {e}")
        return "errors"
    if bs is None:                     # already fired today, already held, or geometry rejected
        return "not_opened"
    try:
        await _write_lab_trade(meta, sig, symbol, market, timeframe, ctx, cfg, regime, mid, bs)
    except Exception as e:
        await sched._health("warn", f"lab:{meta.id}", f"{symbol}: lab_trades: {type(e).__name__}: {e}")
    try:
        await broadcaster.publish("buy_signal", {
            "symbol": symbol, "price": verdict["entry"], "type": "buy", "model": mid,
            "score": verdict["score"], "signal_uid": bs.signal_uid,
            "initiated_at": bs.initiated_at.isoformat() if bs.initiated_at else None})
    except Exception:
        pass
    return "fired"


async def _run_strategy(sched, s, row, *, universe, spy, regime, today, past_cutoff,
                        settings, bars_for) -> Dict[str, int]:
    meta = s.meta
    mid = ensure_fleet_model(meta)
    hb = sched.model_health.setdefault(mid, {})
    hb.update({"model_id": mid, "name": meta.name, "engine": LAB_ENGINE, "cadence": "intraday",
               "enabled": True, "stage": row.stage, "family": meta.family,
               "lab_strategy_id": meta.id, "regime": regime, "last_seen_at": now_utc().isoformat()})
    counts = {"scanned": 0, "with_data": 0, "errors": 0, "fired": 0, "late": 0,
              "shorts_recorded": 0, "shorts_dropped": 0, "not_opened": 0}
    tfs = timeframes_for(meta, row.best_timeframe or None)

    def _finish(status: str, why: Optional[str]) -> Dict[str, int]:
        now_iso = now_utc().isoformat()
        hb.update({"status": status, "skip_reason": why, "last_scan_at": now_iso,
                   "last_seen_at": now_iso, "symbols_scanned": counts["scanned"],
                   "symbols_with_data": counts["with_data"], "errors": counts["errors"],
                   "signals_this_pass": counts["fired"],
                   "signals_today": (hb.get("signals_today", 0) + counts["fired"]
                                     if hb.get("day") == today else counts["fired"]),
                   "day": today, "timeframes": tfs, "late_rejects": counts["late"],
                   "shorts_recorded": counts["shorts_recorded"],
                   "shorts_dropped": counts["shorts_dropped"],
                   "not_opened": counts["not_opened"]})
        return counts

    if meta.regimes_on is not None and regime not in meta.regimes_on:
        return _finish("WAITING", f"regime '{regime}' is not in this strategy's regimes_on "
                                  f"{list(meta.regimes_on)}")
    if not tfs:
        return _finish("WAITING", f"none of {list(meta.timeframes)} can be built live "
                                  f"(supported: {list(LIVE_TIMEFRAMES)})")
    cfg = dict(meta.params)
    cfg.update({k: v for k, v in (row.params or {}).items() if k in meta.params})
    pairs = symbols_for(meta.markets, universe)
    if not pairs:
        return _finish("WAITING", f"no symbols for markets {list(meta.markets)} right now "
                                  "(equities evaluate 03:45-16:00 ET; crypto always)")
    for symbol, market in pairs:
        counts["scanned"] += 1
        bars5, prior = await bars_for(symbol)
        if not bars5:
            continue
        counts["with_data"] += 1
        for tf in tfs:
            ctx = _assemble(symbol, market, tf, bars5, prior, spy, regime)
            if ctx is None:
                continue
            try:
                sig = s.signal(ctx, cfg)
            except Exception as e:
                counts["errors"] += 1
                await sched._health("warn", f"lab:{meta.id}",
                                    f"{symbol} {tf}: {type(e).__name__}: {e}")
                break
            if sig is None:
                continue
            outcome = await _take_signal(sched, s, row, mid, sig, symbol, market, tf, ctx,
                                         cfg, bars5, regime, today, past_cutoff, settings)
            counts[outcome] = counts.get(outcome, 0) + 1
            break                                  # one timeframe per symbol per pass
    status = "LIVE" if counts["with_data"] else ("NO_DATA" if counts["scanned"] else "WAITING")
    return _finish(status, None if counts["with_data"] else
                   f"{counts['scanned']} symbol(s) matched but none had usable bars")


async def run_cycle(sched, settings: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """One live pass. ``sched`` is the Scheduler (needs .mctx, .ctx,
    .model_health, .last_regime, ._health). Never raises."""
    now_iso = now_utc().isoformat()
    hbw = sched.model_health.setdefault(WORKER_HB, {})
    hbw.update({"model_id": WORKER_HB, "name": "Quant Lab (forward paper)", "engine": LAB_ENGINE,
                "cadence": "intraday", "enabled": not _is_off(settings.get("lab_live", "on")),
                "last_seen_at": now_iso})
    summary: Dict[str, Any] = {"strategies_loaded": 0, "strategies_live": [], "signals": 0,
                               "scanned": 0, "regime": None}
    if _is_off(settings.get("lab_live", "on")):
        hbw.update({"status": "DISABLED",
                    "skip_reason": "lab_live is off in settings; open lab positions still settle"})
        return summary
    try:
        return await _cycle(sched, settings, phase, hbw, summary)
    except Exception as e:                        # a lab fault must never stall the fleet
        log.warning("lab cycle failed: %s: %s", type(e).__name__, e)
        hbw.update({"status": "ERROR", "skip_reason": f"{type(e).__name__}: {e}"[:256],
                    "last_seen_at": now_utc().isoformat()})
        try:
            await sched._health("warn", "lab", f"{type(e).__name__}: {e}")
        except Exception:
            pass
        return summary


def _mark_not_live(sched, loaded, live_ids: Set[str], rows: Dict[str, LabStrategy]) -> None:
    """Registered but not live (demoted, or holding an orphan position): the
    card must say so rather than keep showing its last LIVE pass."""
    for s in loaded:
        mid = fleet_model_id(s.id)
        if s.id in live_ids or mid not in MODELS:
            continue
        stage = rows[s.id].stage if s.id in rows else "?"
        hb = sched.model_health.setdefault(mid, {})
        hb.update({"model_id": mid, "name": s.meta.name, "engine": LAB_ENGINE,
                   "cadence": "intraday", "status": "WAITING", "stage": stage,
                   "skip_reason": f"stage {stage} is not a live stage; open positions still settle",
                   "last_seen_at": now_utc().isoformat()})


async def _cycle(sched, settings, phase, hbw, summary) -> Dict[str, Any]:
    if getattr(sched, "mctx", None) is None:
        sched.mctx = mplat.ModelContext(sched.ctx.fmp)
    loaded = registry.load_all()
    summary["strategies_loaded"] = len(loaded)
    async with SessionLocal() as db:
        rows = await ensure_strategy_rows(db, loaded)
        held = await open_lab_profiles(db)
    live_ids = {s.id for s in loaded
                if rows.get(s.id) is not None and rows[s.id].stage in LIVE_STAGES}
    for s in loaded:                              # registered => settled by the fleet
        if s.id in live_ids or fleet_model_id(s.id) in held:
            ensure_fleet_model(s.meta)
    live = [s for s in loaded if s.id in live_ids]
    summary["strategies_live"] = [s.id for s in live]
    t = now_et()
    today = str(t.date())
    _mark_not_live(sched, loaded, live_ids, rows)
    if not live:
        hbw.update({"status": "WAITING", "day": today,
                    "skip_reason": f"{len(loaded)} lab strategies loaded; none is in "
                                   f"{'/'.join(LIVE_STAGES)} — promote one in the Lab to "
                                   "start forward paper trading",
                    "strategies_loaded": len(loaded), "strategies_live": []})
        return summary
    try:
        spy_raw = await sched.mctx.daily("SPY")
    except Exception:
        spy_raw = []
    spy = daily_to_lab_bars(spy_raw, today)
    regime = lab_regime(getattr(sched, "last_regime", None), spy)
    past_cutoff = _past_cutoff(settings, phase, t)
    universe = await lab_universe(sched, settings, phase)
    cache: Dict[str, Tuple[List[dict], List[dict]]] = {}

    async def bars_for(sym: str) -> Tuple[List[dict], List[dict]]:
        if sym not in cache:
            try:
                m5 = await sched.mctx.m5(sym)
            except Exception:
                m5 = []
            try:
                d = await sched.mctx.daily(sym)
            except Exception:
                d = []
            cache[sym] = (m5_to_lab_bars(m5), daily_to_lab_bars(d, today))
        return cache[sym]

    total = {"scanned": 0, "with_data": 0, "errors": 0, "fired": 0}
    for s in live:
        c = await _run_strategy(sched, s, rows[s.id], universe=universe, spy=spy, regime=regime,
                                today=today, past_cutoff=past_cutoff, settings=settings,
                                bars_for=bars_for)
        for k in total:
            total[k] += c.get(k, 0)
    now_iso = now_utc().isoformat()
    hbw.update({
        "status": "LIVE" if total["with_data"] else ("NO_DATA" if total["scanned"] else "WAITING"),
        "skip_reason": None if total["with_data"] else
                       ("no symbol had usable bars" if total["scanned"] else
                        "live strategies had no symbols to evaluate in this phase"),
        "last_scan_at": now_iso, "last_seen_at": now_iso,
        "symbols_scanned": total["scanned"], "symbols_with_data": total["with_data"],
        "errors": total["errors"], "signals_this_pass": total["fired"],
        "signals_today": (hbw.get("signals_today", 0) + total["fired"]
                          if hbw.get("day") == today else total["fired"]),
        "day": today, "regime": regime,
        "regime_source": (getattr(sched, "last_regime", None) or {}).get("state"),
        "strategies_loaded": len(loaded), "strategies_live": [s.id for s in live],
        "universe": {k: len(v) for k, v in universe.items()}, "past_cutoff": past_cutoff,
    })
    summary.update({"signals": total["fired"], "scanned": total["scanned"], "regime": regime})
    if total["fired"]:
        await sched._health("info", "lab", f"{total['fired']} Quant Lab signal(s) fired "
                                           f"(regime {regime})")
    return summary
