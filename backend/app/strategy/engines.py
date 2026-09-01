"""Model engines. Uniform contract:

    engine(ctx, symbol, cfg) -> verdict | None

verdict = {action: 'buy'|'watch', entry, stop, target1, target2, score(0-100),
           setup, evidence: {...}, holding: 'intraday'|'swing'|'position'}

ctx supplies bars_5m[symbol], bars_daily[symbol], quote[symbol], regime,
spy_daily, earnings[symbol], insider_clusters[symbol]. Engines are pure —
no I/O — so the same code runs live and in replay. Baseline defensible logic;
every model is UNPROVEN until forward evidence exists."""
from typing import Any, Dict, List, Optional

from .indicators import (atr, gaussian_filter, macd_hist, pivots,
                         resistance_zones, rolling_beta, rsi, sma, zscore)

MOMO_CLUSTER_CAP = 12.0   # RSI+MACD+etc share one capped momentum cluster


def _bars(ctx, sym, tf):
    return (ctx.get(f"bars_{tf}") or {}).get(sym) or []


def _closes(bars):
    return [b["c"] for b in bars]


def _mk(action, entry, stop, t1, score, setup, evidence, holding, t2=None):
    if entry is None or stop is None or entry <= stop:
        return None
    risk = entry - stop
    return {"action": action, "entry": round(entry, 4), "stop": round(stop, 4),
            "target1": round(t1 if t1 else entry + 1.5 * risk, 4),
            "target2": round(t2 if t2 else entry + 3 * risk, 4),
            "score": round(max(0, min(100, score)), 1), "setup": setup,
            "evidence": evidence, "holding": holding}


# ── regime controller (overlay) ─────────────────────────────────────────
def regime(ctx) -> Dict[str, Any]:
    spy = _bars(ctx, "SPY", "daily")
    if len(spy) < 60:
        return {"state": "uncertain", "why": "insufficient SPY history"}
    cl = _closes(spy)
    s20, s50 = sma(cl, 20), sma(cl, 50)
    a = atr(spy, 14) or 0
    atr_pct = a / cl[-1] * 100
    trend_up = s20 and s50 and cl[-1] > s20 > s50
    trend_dn = s20 and s50 and cl[-1] < s20 < s50
    rng = abs(cl[-1] - cl[-10]) / cl[-1] * 100 if len(cl) >= 10 else 0
    if atr_pct > 2.6:
        return {"state": "high_risk", "why": f"SPY ATR {atr_pct:.1f}% — disorderly",
                "atr_pct": round(atr_pct, 2)}
    if trend_up or trend_dn:
        return {"state": "trend", "dir": "up" if trend_up else "down",
                "why": "20>50 stack" if trend_up else "20<50 stack",
                "atr_pct": round(atr_pct, 2)}
    if rng < 1.5:
        return {"state": "range", "why": f"10-day travel {rng:.1f}%",
                "atr_pct": round(atr_pct, 2)}
    return {"state": "uncertain", "why": "mixed evidence",
            "atr_pct": round(atr_pct, 2)}


REGIME_ALLOW = {   # which regimes each engine may trade (blueprint table)
    "confluence": {"trend", "range", "uncertain"},
    "pairs": {"range", "uncertain", "trend"},
    "trend": {"trend"},
    "earnings": {"trend", "range", "uncertain"},
    "meanrev": {"range"},
    "resmom": {"trend", "range", "uncertain"},
    "factor": {"trend", "range", "uncertain"},
    "insider": {"trend", "range", "uncertain"},
    "orb": {"trend", "uncertain"},
    "breakout": {"trend", "uncertain"},
    "gaussian": {"trend", "uncertain"},
    "vacuum": {"trend", "range", "uncertain"},
    "opendrive": {"trend", "uncertain"},
    "rsreclaim": {"trend", "uncertain"},
}


def _vwap(bars):
    num = den = 0.0
    for b in bars:
        if b["v"] > 0:
            num += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
            den += b["v"]
    return num / den if den else None


def _rvol_today(bars_5m_today, daily, n=20):
    if not bars_5m_today or len(daily) < n:
        return None
    vol_today = sum(b["v"] for b in bars_5m_today)
    frac = max(0.05, min(1.0, len(bars_5m_today) / 78))
    med = sorted(d["v"] for d in daily[-n:])[n // 2]
    return vol_today / (med * frac) if med else None


# ── Model 2: Technical Confluence ───────────────────────────────────────
def confluence(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    m5 = _bars(ctx, sym, "5m")
    if len(d) < 60 or len(m5) < 20:
        return None
    cl = _closes(d)
    clusters: Dict[str, float] = {}
    s20, s50 = sma(cl, 20), sma(cl, 50)
    clusters["trend"] = 10.0 if (s20 and s50 and cl[-1] > s20 > s50) else \
        (5.0 if s20 and cl[-1] > s20 else 0.0)
    momo = 0.0
    r = rsi(cl)
    if r is not None and 50 <= r <= 70: momo += 6
    mh = macd_hist(cl)
    if mh is not None and mh > 0: momo += 6
    clusters["momentum"] = min(MOMO_CLUSTER_CAP, momo)   # capped: no double count
    rv = _rvol_today(m5, d)
    clusters["volume"] = 10.0 if rv and rv >= 2 else (5.0 if rv and rv >= 1.3 else 0.0)
    vw = _vwap(m5)
    px = m5[-1]["c"]
    clusters["structure"] = 8.0 if (vw and px > vw) else 0.0
    zones = resistance_zones(d[-90:])
    above = [z for z in zones if z["level"] < px]
    near_break = any(abs(px - z["level"]) / px < 0.01 for z in zones)
    clusters["levels"] = 8.0 if near_break else (4.0 if above else 0.0)
    active = sum(1 for v in clusters.values() if v > 0)
    score = sum(clusters.values()) * 2
    if active < int(cfg.get("min_clusters", 3)) or score < cfg.get("min_score", 55):
        return None
    stop = vw if vw and vw < px else (s20 if s20 and s20 < px else px * 0.97)
    return _mk("buy", px, stop, None, score, "multi_cluster_confluence",
               {"clusters": clusters, "rvol": rv, "vwap": vw,
                "active_clusters": active}, "swing")


# ── Model 3: Pairs ──────────────────────────────────────────────────────
def pairs(ctx, sym, cfg) -> Optional[dict]:
    # sym encodes "A|B"
    if "|" not in sym:
        return None
    a, b = sym.split("|")
    da, db = _bars(ctx, a, "daily"), _bars(ctx, b, "daily")
    if len(da) < 90 or len(db) < 90:
        return None
    ca, cb = _closes(da), _closes(db)
    import math as _m
    beta = rolling_beta(ca, cb, 60)
    if beta is None or not (0.2 < beta < 4):
        return None
    n = min(len(ca), len(cb))
    spread = [_m.log(ca[i]) - beta * _m.log(cb[i]) for i in range(-n, 0)]
    z = zscore(spread, 40)
    if z is None or z > -float(cfg.get("entry_z", 2.0)):
        return None   # long-the-laggard research variant: only deep negative z
    px = ca[-1]
    stop = px * (1 - float(cfg.get("stop_pct", 4.0)) / 100)
    return _mk("buy", px, stop, px * (1 + 2.5 / 100), 55 + min(30, abs(z) * 10),
               "pair_spread_z", {"pair": f"{a}/{b}", "beta": round(beta, 3),
                                 "z": round(z, 2),
                                 "note": "long-leg only (borrow data not in plan); "
                                         "hedged variant is research"},
               "swing")


# ── Model 4: Trend Following ────────────────────────────────────────────
def trend(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    if len(d) < 60:
        return None
    cl = _closes(d)
    hh = max(x["h"] for x in d[-int(cfg.get("channel", 20)) - 1:-1])
    a = atr(d, 14)
    if a is None or cl[-1] <= hh:
        return None
    s50 = sma(cl, 50)
    if not s50 or cl[-1] < s50:
        return None
    stop = cl[-1] - 2.5 * a
    vol_scale = min(1.0, 0.02 * cl[-1] / (a or 1e-9))
    return _mk("buy", cl[-1], stop, cl[-1] + 3 * a, 60 + 20 * vol_scale,
               f"channel_breakout_{cfg.get('channel', 20)}d",
               {"atr": round(a, 3), "vol_scale": round(vol_scale, 2),
                "prior_high": round(hh, 2)}, "position")


# ── Model 5: Earnings Drift ─────────────────────────────────────────────
def earnings(ctx, sym, cfg) -> Optional[dict]:
    ev = (ctx.get("earnings") or {}).get(sym)
    d = _bars(ctx, sym, "daily")
    if not ev or len(d) < 21:
        return None
    surp = ev.get("surprise_pct")
    if surp is None or surp < float(cfg.get("min_surprise_pct", 10.0)):
        return None
    cl = _closes(d)
    gap = (cl[-1] - cl[-2]) / cl[-2] * 100 if len(cl) >= 2 else 0
    if gap < 1.0 or gap > 25.0:   # must react, must not exhaust
        return None
    a = atr(d, 14) or cl[-1] * 0.03
    return _mk("buy", cl[-1], cl[-1] - 1.5 * a, cl[-1] + 2.5 * a,
               55 + min(25, surp / 2) + min(10, gap),
               "earnings_surprise_drift",
               {"surprise_pct": round(surp, 1), "gap_pct": round(gap, 1),
                "consensus_quality": ev.get("quality", "ESTIMATED"),
                "report_date": ev.get("date")}, "swing")


# ── Model 6: Intraday Mean Reversion ────────────────────────────────────
def meanrev(ctx, sym, cfg) -> Optional[dict]:
    m5 = _bars(ctx, sym, "5m")
    if len(m5) < 30:
        return None
    vw = _vwap(m5)
    px = m5[-1]["c"]
    if not vw:
        return None
    cl5 = _closes(m5)
    z = zscore(cl5, 30)
    dev_pct = (px - vw) / vw * 100
    if z is None or z > -float(cfg.get("entry_z", 2.2)) or dev_pct > -0.6:
        return None
    last3v = sum(b["v"] for b in m5[-3:])
    prior3v = sum(b["v"] for b in m5[-6:-3])
    if prior3v and last3v / prior3v > 0.9:
        return None   # need fading volume (shock absorbed, not accelerating)
    if m5[-1]["c"] <= m5[-1]["o"]:
        return None   # reversal confirmation bar
    stop = min(b["l"] for b in m5[-3:])
    return _mk("buy", px, stop, vw, 55 + min(25, abs(z) * 8),
               "vwap_reversion",
               {"z": round(z, 2), "dev_from_vwap_pct": round(dev_pct, 2),
                "vwap": round(vw, 3)}, "intraday")


# ── Model 7: Residual Momentum ──────────────────────────────────────────
def resmom(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    spy = ctx.get("spy_daily") or []
    if len(d) < 130 or len(spy) < 130:
        return None
    cl, cs = _closes(d), _closes(spy)
    beta = rolling_beta(cl, cs, 60)
    if beta is None:
        return None
    import math as _m
    n = 120
    resid = []
    for i in range(-n, -21):   # skip most recent month (reversal-prone)
        r_st = _m.log(cl[i] / cl[i - 1])
        r_mk = _m.log(cs[i] / cs[i - 1])
        resid.append(r_st - beta * r_mk)
    cum = sum(resid)
    vol = (sum(r * r for r in resid) / len(resid)) ** 0.5 or 1e-9
    t_stat = cum / (vol * (len(resid) ** 0.5))
    if t_stat < float(cfg.get("min_t", 1.5)):
        return None
    a = atr(d, 14) or cl[-1] * 0.03
    return _mk("buy", cl[-1], cl[-1] - 2 * a, cl[-1] + 4 * a,
               55 + min(30, t_stat * 10), "residual_momentum_12_1",
               {"beta": round(beta, 2), "resid_t": round(t_stat, 2)}, "position")


# ── Model 8: Multi-Factor ───────────────────────────────────────────────
def factor(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    f = (ctx.get("fundamentals") or {}).get(sym) or {}
    if len(d) < 130:
        return None
    cl = _closes(d)
    mom = cl[-21] / cl[-126] - 1 if len(cl) >= 126 else 0
    a = atr(d, 60)
    lowvol = -(a / cl[-1]) if a else 0
    pe = f.get("pe")
    value = (1 / pe) if pe and pe > 0 else 0
    comp = 0.5 * mom + 0.3 * lowvol * 10 + 0.2 * min(0.2, value)
    if comp < float(cfg.get("min_composite", 0.05)):
        return None
    return _mk("buy", cl[-1], cl[-1] * 0.92, cl[-1] * 1.12,
               50 + min(35, comp * 200), "factor_composite",
               {"momentum_12_1": round(mom, 3), "lowvol": round(lowvol, 4),
                "value_ep": round(value, 4),
                "fundamentals_quality": "CURRENT_VALUE_SNAPSHOT"}, "position")


# ── Model 9: Insider Cluster ────────────────────────────────────────────
def insider(ctx, sym, cfg) -> Optional[dict]:
    cluster = (ctx.get("insider_clusters") or {}).get(sym)
    d = _bars(ctx, sym, "daily")
    if not cluster or len(d) < 30:
        return None
    if cluster.get("buyers", 0) < int(cfg.get("min_buyers", 2)):
        return None
    cl = _closes(d)
    a = atr(d, 14) or cl[-1] * 0.04
    score = 50 + min(20, cluster["buyers"] * 6) + \
        (10 if cluster.get("officer") else 0)
    return _mk("buy", cl[-1], cl[-1] - 2 * a, cl[-1] + 4 * a, score,
               "form4_cluster",
               {"buyers_5d": cluster["buyers"], "officer": cluster.get("officer"),
                "accessions": cluster.get("accessions", [])[:5]}, "position")


# ── Model 10: Opening-Range Breakout ────────────────────────────────────
def orb(ctx, sym, cfg) -> Optional[dict]:
    m5 = _bars(ctx, sym, "5m")
    today = [b for b in m5 if b.get("minute_of_day", 0) >= 570]
    n_range = int(cfg.get("range_min", 15)) // 5
    if len(today) <= n_range:
        return None
    orange = today[:n_range]
    hi = max(b["h"] for b in orange)
    lo = min(b["l"] for b in orange)
    px = today[-1]["c"]
    if px <= hi or (hi - lo) / px * 100 > float(cfg.get("max_range_pct", 1.5)):
        return None
    rv = sum(b["v"] for b in today[n_range:][-3:]) / \
        max(1, sum(b["v"] for b in orange) / n_range * 3)
    if rv < 1.2:
        return None
    return _mk("buy", px, lo, px + 2 * (px - lo), 55 + min(25, rv * 8),
               f"orb_{cfg.get('range_min', 15)}m",
               {"or_high": round(hi, 3), "or_low": round(lo, 3),
                "vol_expansion": round(rv, 2)}, "intraday")


# ── Model 11: Breakout Finder ───────────────────────────────────────────
def breakout(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    m5 = _bars(ctx, sym, "5m")
    if len(d) < 60 or len(m5) < 6:
        return None
    zones = resistance_zones(d[-120:], tol_pct=float(cfg.get("zone_tol", 0.6)),
                             min_touches=int(cfg.get("min_touches", 2)))
    px = m5[-1]["c"]
    broken = [z for z in zones
              if z["level"] < px <= z["level"] * (1 + float(cfg.get("max_ext_pct", 2.0)) / 100)]
    if not broken:
        return None
    z = max(broken, key=lambda x: x["touches"])
    persist = sum(1 for b in m5[-3:] if b["c"] > z["level"])
    if persist < 2:
        return None
    rv = _rvol_today([b for b in m5 if b.get("minute_of_day", 0) >= 570] or m5, d)
    if not rv or rv < float(cfg.get("min_rvol", 1.5)):
        return None
    stop = z["level"] * 0.99
    height = z["level"] - min(x["l"] for x in d[-20:])
    return _mk("buy", px, stop, z["level"] + height * 0.5, 55 + min(20, z["touches"] * 5)
               + min(15, rv * 4), "resistance_zone_break",
               {"level": round(z["level"], 3), "touches": z["touches"],
                "persist_bars": persist, "rvol": round(rv, 2),
                "target_method": "measured_move_50pct"}, "swing",
               t2=z["level"] + height)


# ── Model 12: Gaussian Channel ──────────────────────────────────────────
def gaussian(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    if len(d) < 60:
        return None
    cl = _closes(d)
    period = int(cfg.get("period", 20))
    poles = int(cfg.get("poles", 3))
    g = gaussian_filter(cl[:-0] if False else cl, period, poles)
    tr = [max(b["h"] - b["l"], abs(b["h"] - cl[i - 1]), abs(b["l"] - cl[i - 1]))
          for i, b in enumerate(d) if i > 0]
    ftr = gaussian_filter(tr, period, poles)
    mult = float(cfg.get("mult", 1.4))
    upper = g[-1] + mult * ftr[-1]
    slope_up = g[-1] > g[-2] > g[-3]
    if not slope_up or cl[-1] <= upper:
        return None
    if (cl[-1] - upper) / upper * 100 > float(cfg.get("max_ext_pct", 2.0)):
        return None
    return _mk("buy", cl[-1], g[-1], cl[-1] + 2 * (cl[-1] - g[-1]),
               60 + min(20, (cl[-1] / upper - 1) * 400), "gaussian_upper_break",
               {"center": round(g[-1], 3), "upper": round(upper, 3),
                "period": period, "poles": poles, "slope": "rising"}, "swing")


# ── EXP-1: Liquidity Vacuum ─────────────────────────────────────────────
def vacuum(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    m5 = _bars(ctx, sym, "5m")
    if len(d) < 40 or len(m5) < 10:
        return None
    rng = [(b["h"] - b["l"]) for b in d]
    contracting = rng[-1] < min(rng[-8:-1]) or \
        (sma(rng, 5) or 9e9) < 0.7 * (sma(rng, 20) or 1e-9)
    vols = [b["v"] for b in d]
    vz = zscore(vols, 20)
    if not contracting or vz is None or vz > -0.8:
        return None
    hi5 = max(b["h"] for b in d[-6:-1])
    px = m5[-1]["c"]
    if px <= hi5:
        return None   # entry only on first expansion through compression high
    last3 = sum(b["v"] for b in m5[-3:])
    base3 = sorted(b["v"] for b in m5)[len(m5) // 2] * 3 or 1
    if last3 / base3 < 1.5:
        return None
    lo_comp = min(b["l"] for b in d[-6:-1])
    return _mk("buy", px, lo_comp, px + 2 * (px - lo_comp),
               58 + min(20, abs(vz) * 10), "compression_expansion",
               {"vol_z": round(vz, 2), "compression_high": round(hi5, 3),
                "expansion_vol_x": round(last3 / base3, 2)}, "swing")


# ── EXP-2: Opening Drive ────────────────────────────────────────────────
def opendrive(ctx, sym, cfg) -> Optional[dict]:
    m5 = _bars(ctx, sym, "5m")
    d = _bars(ctx, sym, "daily")
    today = [b for b in m5 if b.get("minute_of_day", 0) >= 570]
    if len(today) < 3 or len(d) < 61:
        return None
    drive = (today[2]["c"] - today[0]["o"]) / today[0]["o"] * 100
    cl = _closes(d)
    hist = [abs(cl[i] - cl[i - 1]) / cl[i - 1] * 100 for i in range(-60, 0)]
    hist.sort()
    pct85 = hist[int(0.85 * len(hist))] * 0.35   # 15-min share of a strong day
    if drive < max(0.25, pct85):
        return None
    vw = _vwap(today)
    px = today[-1]["c"]
    if not vw or px < vw:
        return None
    return _mk("buy", px, vw * 0.998, px * (1 + drive / 100), 55 + min(25, drive * 10),
               "opening_drive_percentile",
               {"drive_15m_pct": round(drive, 2), "pctl_threshold": round(pct85, 2),
                "vwap": round(vw, 3)}, "intraday")


# ── EXP-3: RS Leaders Reclaim ───────────────────────────────────────────
def rsreclaim(ctx, sym, cfg) -> Optional[dict]:
    d = _bars(ctx, sym, "daily")
    spy = ctx.get("spy_daily") or []
    m5 = _bars(ctx, sym, "5m")
    if len(d) < 10 or len(spy) < 10 or len(m5) < 8:
        return None
    cl, cs = _closes(d), _closes(spy)
    rs5 = (cl[-1] / cl[-6]) / (cs[-1] / cs[-6]) - 1
    if rs5 < float(cfg.get("min_rs5_pct", 3.0)) / 100:
        return None
    vw = _vwap(m5)
    px = m5[-1]["c"]
    if not vw:
        return None
    below = any(b["c"] < vw for b in m5[-8:-2])
    above_now = m5[-1]["c"] > vw and m5[-2]["c"] > vw
    if not (below and above_now):
        return None   # reclaim = was below, now holds above
    return _mk("buy", px, vw * 0.995, px * (1 + rs5), 55 + min(25, rs5 * 300),
               "rs_leader_vwap_reclaim",
               {"rs_5d_vs_spy_pct": round(rs5 * 100, 2), "vwap": round(vw, 3)},
               "swing")


ENGINES = {"confluence": confluence, "pairs": pairs, "trend": trend,
           "earnings": earnings, "meanrev": meanrev, "resmom": resmom,
           "factor": factor, "insider": insider, "orb": orb,
           "breakout": breakout, "gaussian": gaussian, "vacuum": vacuum,
           "opendrive": opendrive, "rsreclaim": rsreclaim}
