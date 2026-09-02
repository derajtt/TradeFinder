"""Live (paper) worker for EXTREME_BB_RSI.

Runs the same scan() the backtester runs, on freshly fetched CLOSED bars, then
puts each confirmed signal through the platform risk layer to produce a full
trade plan and roadmap before persisting it. A signal that cannot be turned
into a checkable plan is recorded as detected-but-unactionable with the reason,
never shown as a tradeable BUY.

No real orders are placed anywhere in this module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from ..db import SessionLocal
from ..models import ReversionSignal
from ..risk.engine import build_trade_plan, circuit_breaker_state, RISK_DEFAULTS
from ..risk.qc import qc_check
from ..risk.roadmap import build_roadmap
from ..util.timeutil import now_et, now_utc
from . import reversion as R

STRATEGY_ID = "extreme_reversion"

# Bars needed before any signal can form (BB + RSI + ADX warmup + margin).
MIN_BARS = 260

STOCK_UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "META",
                  "GOOGL", "TSLA", "AMD", "SMH", "XLE", "XLF", "MARA", "RIOT",
                  "PLTR", "SOFI", "COIN", "GDX"]
CRYPTO_UNIVERSE = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "DOGEUSD", "ADAUSD"]
TIMEFRAMES = ["15min", "1hour"]
FAST_TIMEFRAME = "5min"
FAST_SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "BTCUSD", "ETHUSD"]

# Signals expire after this many bars of the signal's own timeframe.
EXPIRY_BARS = 5
BAR_SECONDS = {"5min": 300, "15min": 900, "30min": 1800, "1hour": 3600,
               "4hour": 14400, "1day": 86400}


def session_bucket(ts: datetime, asset_class: str) -> str:
    et = ts.astimezone(now_et().tzinfo)
    if asset_class == "crypto":
        h = ts.astimezone(timezone.utc).hour
        if 0 <= h < 8:
            return "asian_session"
        if 8 <= h < 13:
            return "european_session"
        return "us_session"
    m = et.hour * 60 + et.minute
    if m < 570:
        return "premarket"
    if m < 600:
        return "open_930_1000"
    if m < 690:
        return "morning_1000_1130"
    if m < 810:
        return "midday"
    if m < 900:
        return "afternoon"
    if m < 960:
        return "power_hour"
    return "after_hours"


async def fetch_bars(fmp, symbol: str, interval: str,
                     lookback_days: int = 60) -> List[dict]:
    """Closed bars only. The most recent bar of an in-progress interval is
    dropped so a forming candle can never confirm a signal."""
    today = now_et().date()
    try:
        data = await fmp._get(f"historical-chart/{interval}",
                              {"symbol": symbol,
                               "from": str(today - timedelta(days=lookback_days)),
                               "to": str(today + timedelta(days=1))},
                              cache_ttl=120, endpoint_name=f"rev-{interval}")
    except Exception:
        return []
    rows = []
    for r in (data if isinstance(data, list) else []):
        try:
            ds = r["date"]
            dt = datetime.strptime(ds, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=now_et().tzinfo)
            rows.append({"date": ds, "time": int(dt.timestamp()),
                         "o": float(r["open"]), "h": float(r["high"]),
                         "l": float(r["low"]), "c": float(r["close"]),
                         "v": float(r.get("volume") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda b: b["time"])
    if not rows:
        return []
    # drop a bar whose interval has not elapsed yet
    span = BAR_SECONDS.get(interval, 900)
    now_ts = now_utc().timestamp()
    while rows and rows[-1]["time"] + span > now_ts:
        rows.pop()
    return rows


async def scan_symbol_multi(fmp, symbol: str, interval: str, asset_class: str,
                            variants: List[str],
                            overrides: Optional[dict] = None
                            ) -> Tuple[Dict[str, List[dict]], int]:
    """Fetch the series ONCE, then evaluate every variant against it.

    Variants differ only in parameters, so this costs no extra API calls — and
    running them side by side on identical bars is what makes their forward
    records directly comparable.
    """
    bars = await fetch_bars(fmp, symbol, interval)
    if len(bars) < MIN_BARS:
        return {}, len(bars)
    htf_trend = _htf_from(bars)
    out: Dict[str, List[dict]] = {}
    for v in variants:
        p = R.params_for(v, overrides or {})
        sigs = R.scan(bars, p, htf_trend=htf_trend,
                      start_at=max(0, len(bars) - 6))
        for sig in sigs:
            sig["symbol"] = symbol
            sig["timeframe"] = interval
            sig["asset_class"] = asset_class
        out[v] = sigs
    return out, len(bars)


def _htf_from(bars: List[dict]) -> str:
    """Coarse trend from the same series' own long EMAs when no higher
    timeframe was fetched. Causal — uses only bars already closed."""
    from .indicators import ema_series
    if len(bars) < 220:
        return "unknown"
    cl = [b["c"] for b in bars]
    e200, e50 = ema_series(cl, 200), ema_series(cl, 50)
    if not e200[-1] or not e50[-1]:
        return "unknown"
    if cl[-1] > e200[-1] and e50[-1] > e200[-1]:
        return "up"
    if cl[-1] < e200[-1] and e50[-1] < e200[-1]:
        return "down"
    return "neutral"


async def scan_symbol(fmp, symbol: str, interval: str, asset_class: str,
                      params: Dict[str, Any],
                      htf_bars: Optional[List[dict]] = None
                      ) -> Tuple[List[dict], int]:
    bars = await fetch_bars(fmp, symbol, interval)
    if len(bars) < MIN_BARS:
        return [], len(bars)
    htf_trend = "unknown"
    if htf_bars and len(htf_bars) >= 200:
        from .indicators import ema_series
        cl = [b["c"] for b in htf_bars]
        e200 = ema_series(cl, 200)
        e50 = ema_series(cl, 50)
        if e200[-1] and e50[-1]:
            if cl[-1] > e200[-1] and e50[-1] > e200[-1]:
                htf_trend = "up"
            elif cl[-1] < e200[-1] and e50[-1] < e200[-1]:
                htf_trend = "down"
            else:
                htf_trend = "neutral"
    # Only look at the tail: a signal is actionable only on the newest bars.
    sigs = R.scan(bars, params, htf_trend=htf_trend,
                  start_at=max(0, len(bars) - 6))
    for s in sigs:
        s["symbol"] = symbol
        s["timeframe"] = interval
        s["asset_class"] = asset_class
        s["bars"] = bars
    return sigs, len(bars)


async def persist_signal(sig: Dict[str, Any], params: Dict[str, Any],
                         risk_settings: Dict[str, Any],
                         open_positions: List[dict],
                         strategy_stats: Optional[dict],
                         breaker: Dict[str, Any],
                         dataset_run: int = 1) -> Optional[ReversionSignal]:
    """Build the plan, run QC, and store. Immutable once written."""
    snap, lv = sig["snapshot"], sig.get("levels")
    if not lv:
        return None
    tf = sig["timeframe"]
    bar_time = int(sig.get("confirm_time") or 0)

    async with SessionLocal() as db:
        dup = (await db.execute(select(ReversionSignal.id).where(
            ReversionSignal.strategy_id == STRATEGY_ID,
            ReversionSignal.variant == params.get("variant", "adaptive"),
            ReversionSignal.symbol == sig["symbol"],
            ReversionSignal.timeframe == tf,
            ReversionSignal.direction == sig["direction"],
            ReversionSignal.bar_time == bar_time))).first()
        if dup:
            return None

        plan_input = {
            "symbol": sig["symbol"], "direction": sig["direction"],
            "entry": lv["entry"], "stop": lv["stop"],
            "stop_basis": lv["stop_basis"], "targets": lv["targets"],
            "entry_zone": lv["entry_zone"], "score": sig["score"],
            "invalidation": (f"price closes back outside the band "
                             f"or below {lv['stop']:.4f}"),
            "expires_at": None,
        }
        plan = build_trade_plan(plan_input, risk_settings,
                                open_positions=open_positions,
                                strategy_stats=strategy_stats,
                                breaker=breaker)
        qc = qc_check(plan) if plan.get("actionable") else {"passed": True, "errors": []}
        if plan.get("actionable") and not qc["passed"]:
            plan = {"actionable": False, "status": "BLOCKED_QC",
                    "reason": "; ".join(qc["errors"])}

        roadmap = build_roadmap(plan, current_price=snap.get("c"),
                                stage="CONFIRMED",
                                strategy_label="Extreme Reversion",
                                why_lines=sig.get("explain") or [])
        ts = datetime.fromtimestamp(bar_time, tz=timezone.utc) if bar_time else now_utc()
        expires = ts + timedelta(seconds=BAR_SECONDS.get(tf, 900) * EXPIRY_BARS)

        row = ReversionSignal(
            signal_uid=uuid.uuid4().hex,
            strategy_id=STRATEGY_ID,
            strategy_version=params.get("strategy_version", "1.2.0"),
            variant=params.get("variant", "adaptive"),
            dataset_run=dataset_run, cohort="paper",
            symbol=sig["symbol"], asset_class=sig["asset_class"],
            timeframe=tf, direction=sig["direction"],
            setup_detected_at=(datetime.fromtimestamp(sig["setup_time"], tz=timezone.utc)
                               if sig.get("setup_time") else None),
            confirmed_at=ts, bar_time=bar_time,
            signal_score=sig["score"], score_band=sig["score_band"],
            score_parts=sig.get("score_parts") or {},
            entry_price=lv["entry"],
            entry_zone_low=lv["entry_zone"]["ideal"][0],
            entry_zone_high=lv["entry_zone"]["ideal"][1],
            no_chase_price=lv["entry_zone"]["no_chase"],
            stop_price=lv["stop"], stop_basis=lv["stop_basis"][:96],
            target_1=lv["targets"][0]["price"] if len(lv["targets"]) > 0 else None,
            target_2=lv["targets"][1]["price"] if len(lv["targets"]) > 1 else None,
            target_3=lv["targets"][2]["price"] if len(lv["targets"]) > 2 else None,
            targets_json=lv["targets"],
            parameters_json={k: v for k, v in params.items()},
            indicator_snapshot_json={k: v for k, v in snap.items()
                                     if k not in ("i",)},
            market_regime=snap.get("regime", ""),
            adx=snap.get("adx"), atr=snap.get("atr"), rsi=snap.get("rsi"),
            rsi_extreme=(sig.get("setup") or {}).get("rsi_extreme"),
            bb_basis=snap.get("bb_basis"), bb_upper=snap.get("bb_upper"),
            bb_lower=snap.get("bb_lower"), bb_width=snap.get("bb_width"),
            rvol=snap.get("rvol"),
            vwap_distance_atr=((snap["c"] - snap["vwap"]) / snap["atr"])
                              if (snap.get("vwap") and snap.get("atr")) else None,
            htf_trend=snap.get("htf_trend") or "unknown",
            divergence=(sig.get("divergence") or {}).get("type", ""),
            session_bucket=session_bucket(ts, sig["asset_class"]),
            trade_plan=plan, roadmap=roadmap,
            explain_lines=sig.get("explain") or [],
            status="CONFIRMED" if plan.get("actionable") else "NO_TRADE",
            events=[{"t": now_utc().isoformat(), "e": "confirmed",
                     "price": lv["entry"], "score": sig["score"]}],
            expires_at=expires,
            data_source=f"fmp:historical-chart/{tf}",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def update_open_signals(fmp, quotes: Optional[Dict[str, dict]] = None
                              ) -> Dict[str, int]:
    """Advance live signals through their lifecycle against real bars.

    The plan is obeyed literally: the stop exits, each target takes its planned
    partial, and TP1 moves the stop to breakeven. Original entry/stop/targets
    are never rewritten — every action appends an event instead, so the record
    of what was promised stays intact next to what happened.
    """
    stats = {"checked": 0, "filled": 0, "tp": 0, "stopped": 0, "expired": 0,
             "closed": 0}
    live_states = ("CONFIRMED", "ENTRY_ZONE", "ACTIVE", "TP1_HIT", "TP2_HIT")
    async with SessionLocal() as db:
        rows = (await db.execute(select(ReversionSignal).where(
            ReversionSignal.status.in_(live_states)))).scalars().all()
        if not rows:
            return stats
        # group by (symbol, timeframe) so each series is fetched once
        need: Dict[Tuple[str, str], List[ReversionSignal]] = {}
        for r in rows:
            need.setdefault((r.symbol, r.timeframe), []).append(r)

        for (sym, tf), group in need.items():
            bars = await fetch_bars(fmp, sym, tf, lookback_days=10)
            if not bars:
                continue
            for r in group:
                stats["checked"] += 1
                ev = list(r.events or [])
                after = [b for b in bars if b["time"] > (r.bar_time or 0)]
                if not after:
                    continue
                filled = r.status in ("ACTIVE", "TP1_HIT", "TP2_HIT")
                long = r.direction == "long"
                entry, stop = r.entry_price, r.stop_price
                tps = [t for t in (r.targets_json or [])]
                hit_names = {e.get("target") for e in ev if e.get("e") == "target_hit"}

                for b in after:
                    hi, lo, close = b["h"], b["l"], b["c"]
                    if not filled:
                        # expiry before entry
                        if r.expires_at and b["time"] > r.expires_at.timestamp():
                            r.status = "EXPIRED"
                            ev.append({"t": now_utc().isoformat(), "e": "expired",
                                       "detail": "entry never triggered inside "
                                                 f"{EXPIRY_BARS} bars"})
                            stats["expired"] += 1
                            break
                        # no-chase guard: a runaway gap means the trade is MISSED
                        if r.no_chase_price is not None:
                            if (long and lo > r.no_chase_price) or \
                               (not long and hi < r.no_chase_price):
                                r.status = "MISSED"
                                ev.append({"t": now_utc().isoformat(), "e": "missed",
                                           "detail": "price ran past the no-chase "
                                                     "level before entry"})
                                break
                        touched = (lo <= entry <= hi)
                        if touched:
                            filled = True
                            r.status = "ACTIVE"
                            stats["filled"] += 1
                            ev.append({"t": now_utc().isoformat(), "e": "entry_filled",
                                       "price": entry, "bar": b["date"]})
                        else:
                            continue
                    # ---- position is live: obey the plan, stop first --------
                    risk = abs(entry - stop) or 1e-9
                    mfe = ((hi - entry) if long else (entry - lo)) / risk
                    mae = ((lo - entry) if long else (entry - hi)) / risk
                    r.mfe_r = round(max(r.mfe_r or 0, mfe), 3)
                    r.mae_r = round(min(r.mae_r or 0, mae), 3)

                    stop_hit = (lo <= stop) if long else (hi >= stop)
                    tp_hit = next((t for t in tps
                                   if t["name"] not in hit_names
                                   and ((hi >= t["price"]) if long
                                        else (lo <= t["price"]))), None)
                    if stop_hit and tp_hit:
                        # both inside one bar: unknowable order, never a win
                        r.status, r.exit_reason = "CLOSED", "AMBIGUOUS"
                        r.win_loss = "AMBIGUOUS"
                        r.exit_price, r.closed_at = stop, now_utc()
                        ev.append({"t": now_utc().isoformat(), "e": "ambiguous_bar",
                                   "detail": "stop and target both inside one bar — "
                                             "resolved as not-a-win"})
                        stats["closed"] += 1
                        break
                    if stop_hit:
                        r.status, r.exit_reason = "CLOSED", "STOP"
                        r.exit_price, r.closed_at = stop, now_utc()
                        stats["stopped"] += 1
                        stats["closed"] += 1
                        ev.append({"t": now_utc().isoformat(), "e": "stop_hit",
                                   "price": stop, "bar": b["date"]})
                        break
                    if tp_hit:
                        hit_names.add(tp_hit["name"])
                        stats["tp"] += 1
                        ev.append({"t": now_utc().isoformat(), "e": "target_hit",
                                   "target": tp_hit["name"], "price": tp_hit["price"],
                                   "allocation_pct": tp_hit.get("allocation_pct"),
                                   "bar": b["date"]})
                        if tp_hit["name"] == "TP1" and len(tps) > 1:
                            stop = entry          # breakeven, never wider
                            ev.append({"t": now_utc().isoformat(),
                                       "e": "stop_to_breakeven", "price": entry})
                        r.status = f"{tp_hit['name']}_HIT" if len(hit_names) < len(tps) \
                            else "CLOSED"
                        if r.status == "CLOSED":
                            r.exit_reason = "ALL_TARGETS"
                            r.exit_price, r.closed_at = tp_hit["price"], now_utc()
                            stats["closed"] += 1
                            break

                if r.status == "CLOSED" and r.exit_price:
                    gross = ((r.exit_price - entry) if long else (entry - r.exit_price))
                    costs = (entry + r.exit_price) * \
                        (float(RISK_DEFAULTS["commission_pct"]) / 100.0)
                    net = gross - costs
                    r.gross_return_pct = round(gross / entry * 100, 4)
                    r.net_return_pct = round(net / entry * 100, 4)
                    r.r_multiple = round(net / (abs(entry - r.stop_price) or 1e-9), 4)
                    if r.win_loss != "AMBIGUOUS":
                        r.win_loss = "WIN" if net > 0 else ("LOSS" if net < 0
                                                            else "BREAKEVEN")
                r.events = ev
        await db.commit()
    return stats
