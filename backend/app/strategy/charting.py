"""Deterministic chart-pattern engine. Pure functions over completed bars —
confirmed pivots only, so nothing repaints; every detection is reproducible
and auditable. Powers the Chart Workstation overlays AND the chart_patterns
competition model."""
from typing import Any, Dict, List, Optional

from .indicators import pivots


def _zones(bars, kind: str, tol_pct: float = 0.7, min_touches: int = 2):
    hs, ls = pivots(bars)
    pts = hs if kind == "resistance" else ls
    zones: List[dict] = []
    for idx, price in pts:
        placed = False
        for z in zones:
            if abs(price - z["level"]) / z["level"] * 100 <= tol_pct:
                z["touches"] += 1
                z["idxs"].append(idx)
                z["level"] = (z["level"] * (z["touches"] - 1) + price) / z["touches"]
                placed = True
                break
        if not placed:
            zones.append({"level": price, "touches": 1, "idxs": [idx], "kind": kind})
    return [dict(z, level=round(z["level"], 4)) for z in zones
            if z["touches"] >= min_touches]


def _trendline(pts: List[tuple]) -> Optional[dict]:
    """Least-squares fit through pivot points; needs 3+ touches within tolerance."""
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    b = my - slope * mx
    # every pivot must sit near the line (real trendline, not scatter)
    for x, y in pts:
        fit = slope * x + b
        if abs(y - fit) / max(1e-9, y) * 100 > 1.2:
            return None
    return {"slope": slope, "intercept": b, "i1": xs[0], "i2": xs[-1],
            "p1": round(slope * xs[0] + b, 4), "p2": round(slope * xs[-1] + b, 4)}


def detect(bars: List[dict]) -> Dict[str, Any]:
    """Full detection pass. bars: [{o,h,l,c,v}] chronological, completed only."""
    out: Dict[str, Any] = {"zones": [], "trendlines": [], "patterns": [],
                           "signals": []}
    if len(bars) < 30:
        return out
    res = _zones(bars, "resistance")
    sup = _zones(bars, "support")
    out["zones"] = sorted(res + sup, key=lambda z: -z["touches"])[:8]
    hs, ls = pivots(bars)

    up = _trendline(ls[-4:]) if len(ls) >= 3 else None
    if up and up["slope"] > 0:
        out["trendlines"].append({**up, "kind": "uptrend_support"})
    dn = _trendline(hs[-4:]) if len(hs) >= 3 else None
    if dn and dn["slope"] < 0:
        out["trendlines"].append({**dn, "kind": "downtrend_resistance"})

    # double top / bottom: two pivots within 0.8%, separated by 5+ bars
    for pts, kind in ((hs, "double_top"), (ls, "double_bottom")):
        if len(pts) >= 2:
            (i1, p1), (i2, p2) = pts[-2], pts[-1]
            if i2 - i1 >= 5 and abs(p1 - p2) / p1 * 100 <= 0.8:
                out["patterns"].append({"type": kind, "i1": i1, "i2": i2,
                                        "level": round((p1 + p2) / 2, 4),
                                        "note": f"two pivots within 0.8% "
                                                f"({i2 - i1} bars apart)"})

    # compression: recent range under 45% of prior range (volatility contraction)
    if len(bars) >= 30:
        recent = bars[-8:]
        prior = bars[-30:-8]
        r_rng = max(b["h"] for b in recent) - min(b["l"] for b in recent)
        p_rng = max(b["h"] for b in prior) - min(b["l"] for b in prior)
        if p_rng > 0 and r_rng / p_rng < 0.45:
            out["patterns"].append({"type": "compression",
                                    "i1": len(bars) - 8, "i2": len(bars) - 1,
                                    "hi": round(max(b["h"] for b in recent), 4),
                                    "lo": round(min(b["l"] for b in recent), 4),
                                    "note": "range contracted to "
                                            f"{r_rng / p_rng * 100:.0f}% of prior"})

    # causal breakout/breakdown signals: scan bar-by-bar; a signal at bar i uses
    # only zones/lines confirmed by bars < i (pivot confirmation lag respected)
    med_v = sorted(b["v"] for b in bars)[len(bars) // 2] or 1
    for i in range(20, len(bars)):
        b = bars[i]
        vol_ok = b["v"] >= 1.3 * med_v
        for z in res:
            if max(z["idxs"]) + 3 >= i:      # zone not yet confirmed at bar i
                continue
            prev_below = bars[i - 1]["c"] <= z["level"]
            if prev_below and b["c"] > z["level"] * 1.001 and vol_ok:
                out["signals"].append({"i": i, "kind": "buy_breakout",
                                       "price": round(b["c"], 4),
                                       "level": z["level"],
                                       "reason": f"close above {z['touches']}-touch "
                                                 f"resistance {z['level']} on "
                                                 f"{b['v'] / med_v:.1f}x volume"})
        for z in sup:
            if max(z["idxs"]) + 3 >= i:
                continue
            prev_above = bars[i - 1]["c"] >= z["level"]
            if prev_above and b["c"] < z["level"] * 0.999 and vol_ok:
                out["signals"].append({"i": i, "kind": "sell_breakdown",
                                       "price": round(b["c"], 4),
                                       "level": z["level"],
                                       "reason": f"close below {z['touches']}-touch "
                                                 f"support {z['level']} on "
                                                 f"{b['v'] / med_v:.1f}x volume"})
    # double-top sell trigger: neckline (lowest low between tops) broken
    for p in out["patterns"]:
        if p["type"] == "double_top":
            neck = min(b["l"] for b in bars[p["i1"]:p["i2"] + 1])
            for i in range(p["i2"] + 3, len(bars)):
                if bars[i]["c"] < neck * 0.999:
                    out["signals"].append({"i": i, "kind": "sell_double_top",
                                           "price": round(bars[i]["c"], 4),
                                           "level": round(neck, 4),
                                           "reason": "double-top neckline break"})
                    break
        if p["type"] == "double_bottom":
            neck = max(b["h"] for b in bars[p["i1"]:p["i2"] + 1])
            for i in range(p["i2"] + 3, len(bars)):
                if bars[i]["c"] > neck * 1.001:
                    out["signals"].append({"i": i, "kind": "buy_double_bottom",
                                           "price": round(bars[i]["c"], 4),
                                           "level": round(neck, 4),
                                           "reason": "double-bottom neckline break"})
                    break
    out["signals"] = out["signals"][-25:]
    return out
