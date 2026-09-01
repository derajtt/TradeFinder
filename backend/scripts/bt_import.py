"""Push backtest artifacts to the droplet dashboard + install the primary policy.
Usage: bt_import.py <api_base> <api_key_file>"""
import json, os, sys
import httpx

base = sys.argv[1]
key = open(sys.argv[2]).read().strip()
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bt")
rep = json.load(open(f"{OUT}/final_report.json"))

# compact the payload for transport: drop per-trade paths, cap big blobs
res = dict(rep)
res.pop("jitter_detail", None)
tour = res.get("tournament") or {}
for k, v in tour.items():
    if isinstance(v, dict):
        v.pop("by_tier", None)
        v.pop("by_grade", None)
h = httpx.Client(timeout=60, headers={"X-API-Key": key})
r = h.post(f"{base}/api/backtest/import", json={
    "kind": "walkforward", "config": res.get("search", {}).get("locked") or {},
    "config_hash": (res.get("primary") or {}).get("strategy_version", ""),
    "result": res})
print("import:", r.status_code, r.text[:200])

primary = res.get("primary")
if primary:
    # install winner parameters as live settings (prospective-only, versioned)
    e = primary["entry"]
    patch = {
        "watch_max_gap_pct": e.get("max_gap"),
        "max_ext_above_vwap_pct": e.get("max_ext_vwap"),
        "min_pm_dollar_volume": e.get("min_dollar_vol"),
        "rotation_hard_cap": e.get("rotation_max"),
        "watch_score_min": max(35, e.get("min_score", 35)),
    }
    patch = {k: v for k, v in patch.items() if v is not None}
    r2 = h.put(f"{base}/api/settings", json=patch)
    print("settings install:", r2.status_code,
          {k: json.loads(r2.text)["settings"].get(k) for k in patch} if r2.status_code == 200 else r2.text[:200])
    print("primary:", primary["strategy_version"], "| exit:", primary["exit"],
          "| mode:", primary["mode"])
