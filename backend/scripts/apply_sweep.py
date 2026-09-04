#!/usr/bin/env python
"""Apply scripts/engine_sweep.py results.

  * Engine parameter winners -> model_settings[mid].overrides (persisted
    setting, read by the scheduler each pass). Only engines where the sweep
    overrode the default are touched; others are left exactly as they were.
  * Geometry -> constants in app/strategy/platform.py (pooled choice plus any
    per-engine overrides), written as code so they are reviewable in git.

Prints a change log. Dry run unless --apply.
"""
import json, os, re, sys, pathlib, asyncio
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
rep = json.load(open(ROOT.parent / "data" / "sweep_out" / "engine_sweep.json"))
APPLY = "--apply" in sys.argv
DO_GEOM = APPLY and ("--settings-only" not in sys.argv)      # code constants (run locally, commit)
DO_SETTINGS = APPLY and ("--geometry-only" not in sys.argv)  # persisted model_settings (run where the live DB is)

print("=== geometry ===")
g = rep["geometry_pooled"]["chosen"]; gt = rep["geometry_pooled"]["table"][json.dumps(g)]
print(f"pooled: mult={g['mult']} floor={g['floor']} t1={g['t1']}  (n={gt['n']}, exp={gt['exp_r']}R, win={gt['win_pct']}%)")
overrides = {}
for mid, e in rep["engines"].items():
    if e.get("geometry_override"):
        go = e["geometry_override"]; overrides[mid] = {"mult": go["geom"]["mult"], "floor": go["geom"]["floor"], "t1_r": go["geom"]["t1"]}
        print(f"  per-engine: {mid}: {overrides[mid]} (n={go['n']}, exp={go['exp_r']}R)")

print("=== engine parameters ===")
model_settings_patch = {}
for mid, e in rep["engines"].items():
    d, c = e["default"], e["chosen"]
    flag = "OVERRIDE" if e["overrode_default"] else "keep default"
    print(f"  {mid:24s} {flag:13s} default n={d.get('n',0)} exp={d.get('exp_r')}  -> {c['params']} n={c.get('n',0)} exp={c.get('exp_r')} win={c.get('win_pct')}% lb={c.get('wilson_lb')}")
    if e["overrode_default"]:
        model_settings_patch[mid] = c["params"]

if not APPLY:
    print("\ndry run — pass --apply to write"); sys.exit(0)

# ── geometry into code
if DO_GEOM:
  p = ROOT / "app/strategy/platform.py"; s = p.read_text()
  s = re.sub(r"ATR_STOP_MULT_DEFAULT = [0-9.]+", f"ATR_STOP_MULT_DEFAULT = {g['mult']}", s)
  s = re.sub(r"DAY_STOP_FLOOR = [0-9.]+", f"DAY_STOP_FLOOR = {g['floor']}", s)
  s = re.sub(r"DAY_T1_R = [0-9.]+", f"DAY_T1_R = {g['t1']}", s)
  s = re.sub(r"DAY_GEOM_OVERRIDE: Dict\[str, Dict\[str, float\]\] = \{[^\n]*\}",
             "DAY_GEOM_OVERRIDE: Dict[str, Dict[str, float]] = " + json.dumps(overrides), s)
  p.write_text(s); print("platform.py geometry updated")

# ── parameters into the persisted setting (merge, never clobber other models)
async def push():
    from app.db import SessionLocal
    from app.settings_service import get_settings, update_settings
    async with SessionLocal() as db:
        cur = (await get_settings(db)).get("model_settings") or {}
        if isinstance(cur, str): cur = json.loads(cur)
        for mid, params in model_settings_patch.items():
            cur.setdefault(mid, {})["overrides"] = params
            cur[mid]["sweep_basis"] = {"sessions": rep["sessions"], **{k: rep["engines"][mid]["chosen"].get(k) for k in ("n", "exp_r", "win_pct", "wilson_lb")}}
        await update_settings(db, {"model_settings": cur})
        print(f"model_settings updated for: {sorted(model_settings_patch) or 'nothing (no engine beat its default)'}")
if DO_SETTINGS:
    asyncio.run(push())
