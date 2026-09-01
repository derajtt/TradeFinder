"""OpenAI analysis adapter. Strict JSON schema, one retry on malformed output,
content-hash caching handled by the caller, monthly budget enforcement by the caller.
The model classifies evidence only — it never invents market data or score arithmetic."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import httpx

from ..config import get_config

ANALYSIS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "direction": {"type": "string", "enum": ["positive", "negative", "mixed", "neutral"]},
        "materiality": {"type": "integer", "minimum": 0, "maximum": 100,
                        "description": "0-100 scale: 0-10 routine, 20-40 modest, 50-70 market-moving for a microcap, 80-100 transformative"},
        "novelty": {"type": "string", "enum": ["new", "update", "recycled", "unrelated"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "catalyst_type": {"type": "string"},
        "facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"label": {"type": "string"}, "value": {"type": "string"},
                           "source_ref": {"type": "string"}},
            "required": ["label", "value", "source_ref"]}},
        "risks": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"type": {"type": "string"},
                           "severity": {"type": "integer", "minimum": 0, "maximum": 100},
                           "source_ref": {"type": "string"}},
            "required": ["type", "severity", "source_ref"]}},
        "dilution_detected": {"type": "boolean"},
        "going_concern_detected": {"type": "boolean"},
        "plain_english_summary": {"type": "string"},
    },
    "required": ["direction", "materiality", "novelty", "confidence", "catalyst_type",
                 "facts", "risks", "dilution_detected", "going_concern_detected",
                 "plain_english_summary"],
}

SYSTEM_PROMPT = (
    "You are a disciplined equity-catalyst analyst. Classify only what the provided "
    "evidence supports. Never invent prices, volumes, timestamps, filing types, or "
    "numbers not present in the evidence.\n"
    "SCALES (follow exactly):\n"
    "- materiality: integer 0-100 on a percentage-like scale. 0-10 = routine/noise "
    "(scheduled filings, minor PR). 20-40 = modest (small contract, minor analyst note). "
    "50-70 = clearly market-moving for a microcap (meaningful contract/order, positive "
    "trial update, strategic partnership). 80-100 = transformative (FDA approval, "
    "acquisition, massive contract vs company size).\n"
    "- confidence: 0.0-1.0 that your classification is correct from this evidence.\n"
    "- novelty: new = first report within the last day; update = new development in a "
    "known story; recycled = re-publication of old news; unrelated = not about this company.\n"
    "Routine or recycled items are NOT material. Flag dilution (offerings, ATMs, "
    "shelfs, warrants, convertibles) and going-concern language whenever present. "
    "Keep the summary under 80 words, plain English."
)


from ..strategy.catalyst import (ANALYSIS_SCHEMA_V2, SYSTEM_PROMPT_V2,
                                 validate_extraction)


class OpenAiProvider:
    def __init__(self):
        cfg = get_config()
        self.key = cfg.openai_api_key
        self.model = cfg.openai_model
        self.client = httpx.AsyncClient(timeout=60.0)
        self.last_usage: Dict[str, int] = {}

    async def close(self):
        await self.client.aclose()

    @property
    def configured(self) -> bool:
        return bool(self.key)

    async def analyze_v2(self, evidence_text: str) -> Optional[Dict[str, Any]]:
        """Enum-only structured extraction (v2 contract). Malformed => None."""
        if not self.configured:
            return None
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_V2},
                {"role": "user", "content": evidence_text[:24000]},
            ],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "catalyst_extract_v2", "strict": True,
                "schema": ANALYSIS_SCHEMA_V2}},
            "temperature": 0,
        }
        for attempt in range(2):
            try:
                resp = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json=body)
                if resp.status_code >= 400:
                    if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                        continue
                    return None
                payload = resp.json()
                self.last_usage = payload.get("usage") or {}
                data = json.loads(payload["choices"][0]["message"]["content"])
                return validate_extraction(data)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError):
                if attempt == 0:
                    continue
                return None
        return None

    async def analyze(self, evidence_text: str) -> Optional[Dict[str, Any]]:
        """Returns validated analysis dict, or None on failure (caller marks pending/failed)."""
        if not self.configured:
            return None
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": evidence_text[:24000]},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "catalyst_analysis", "strict": True,
                                "schema": ANALYSIS_SCHEMA},
            },
            "temperature": 0.1,
        }
        for attempt in range(2):  # one retry on malformed output
            try:
                resp = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json=body,
                )
                if resp.status_code >= 400:
                    # never log body (may echo key context); status only
                    if resp.status_code in (429, 500, 502, 503) and attempt == 0:
                        continue
                    return None
                payload = resp.json()
                self.last_usage = payload.get("usage") or {}
                content = payload["choices"][0]["message"]["content"]
                data = json.loads(content)
                return self._validate(data)
            except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError):
                if attempt == 0:
                    continue
                return None
        return None

    @staticmethod
    def _validate(d: Dict[str, Any]) -> Dict[str, Any]:
        assert d["direction"] in ("positive", "negative", "mixed", "neutral")
        d["materiality"] = max(0, min(100, int(d["materiality"])))
        assert d["novelty"] in ("new", "update", "recycled", "unrelated")
        d["confidence"] = max(0.0, min(1.0, float(d["confidence"])))
        d["dilution_detected"] = bool(d["dilution_detected"])
        d["going_concern_detected"] = bool(d["going_concern_detected"])
        d["facts"] = [f for f in d.get("facts", []) if f.get("label") and f.get("source_ref")][:20]
        d["risks"] = [r for r in d.get("risks", []) if r.get("type")][:20]
        d["plain_english_summary"] = str(d.get("plain_english_summary", ""))[:1000]
        return d
