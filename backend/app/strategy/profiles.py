"""Concurrent strategy models ("profiles"). Every enabled profile evaluates the
same live data each cycle with its OWN settings overrides; signals, watches,
rejections and paper positions are tagged per profile. Zero extra API cost —
evaluation is pure compute over shared market data."""
from typing import Any, Dict

# defaults; user-editable per profile via /api/profiles (stored in settings blob)
DEFAULT_PROFILES: Dict[str, Dict[str, Any]] = {
    "primary": {
        "name": "Primary", "enabled": True, "color": "#38bdf8",
        "description": "The frozen primary paper strategy (v2 gates as configured).",
        "overrides": {},
    },
    "accuracy": {
        "name": "Accuracy", "enabled": True, "color": "#34d399",
        "description": "Maximize reliable win rate: Grade-A catalysts only, tight "
                       "extension/rotation, stronger liquidity floors.",
        "overrides": {"watch_max_gap_pct": 60.0, "max_ext_above_vwap_pct": 12.0,
                      "rotation_hard_cap": 0.4, "min_pm_dollar_volume": 250_000,
                      "watch_score_min": 55},
    },
    "aggressive": {
        "name": "Aggressive", "enabled": True, "color": "#fbbf24",
        "description": "Research profile: wider gaps/extension, lower floors — "
                       "expected to trade more and win less; for comparison only.",
        "overrides": {"watch_max_gap_pct": 150.0, "max_ext_above_vwap_pct": 35.0,
                      "rotation_hard_cap": 2.0, "min_pm_dollar_volume": 50_000,
                      "watch_score_min": 30},
    },
    "penny": {
        "name": "Penny", "enabled": False, "color": "#f87171",
        "description": "Sub-$1 focus with the hard T1/T2 tier protections.",
        "overrides": {"price_min": 0.10, "price_max": 1.00,
                      "min_pm_dollar_volume": 150_000},
    },
}


def get_profiles(settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    stored = settings.get("profiles") or {}
    out: Dict[str, Dict[str, Any]] = {}
    for pid, base in DEFAULT_PROFILES.items():
        cfg = dict(base)
        if pid in stored and isinstance(stored[pid], dict):
            cfg = {**cfg, **{k: v for k, v in stored[pid].items()
                             if k in ("name", "enabled", "color", "description")}}
            if isinstance(stored[pid].get("overrides"), dict):
                cfg["overrides"] = {**base["overrides"], **stored[pid]["overrides"]}
        out[pid] = cfg
    for pid, cfg in stored.items():          # user-created custom profiles
        if pid not in out and isinstance(cfg, dict):
            out[pid] = {"name": cfg.get("name", pid), "enabled": bool(cfg.get("enabled")),
                        "color": cfg.get("color", "#93a1bd"),
                        "description": cfg.get("description", ""),
                        "overrides": cfg.get("overrides") or {}}
    return out


def profile_settings(settings: Dict[str, Any], pid: str,
                     profiles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(settings)
    merged.update(profiles.get(pid, {}).get("overrides") or {})
    return merged
