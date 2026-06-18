"""AI normalization layer: coerce extracted values to the requested type,
convert units, and snap to an allowed-value list — in one batched Ollama call."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import settings
from app.infrastructure.llm.ollama import OllamaGateway

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You normalize already-extracted product specification values. You are given a "
    "JSON array of items; each has: name, type (string|number|integer|boolean|enum), "
    "unit (desired output unit or null), allowed_values (candidate list or null), and "
    "raw_value (the text extracted from a page). For EACH item return an object with:\n"
    '- "value": the raw value coerced to the requested type, converted to the target '
    "unit when one is given and the raw value uses a different unit. Put only the number "
    "in value for numeric types (no unit text). Booleans must be \"true\"/\"false\".\n"
    '- "unit": the unit of the returned value (target unit if converted, else the source '
    "unit, else null).\n"
    '- "matched_allowed": if allowed_values is provided, true when value equals one of '
    "them (pick the single closest valid match), false if none clearly match; null when "
    "allowed_values is not provided.\n"
    '- "confidence": 0..1, your certainty in this normalization.\n'
    "Do NOT invent data: if raw_value does not contain the answer, set value=null and "
    "confidence=0. Respond ONLY with a JSON array, one object per item, SAME order."
)


@dataclass
class NormItem:
    name: str
    type: str
    unit: str | None
    allowed_values: list[str] | None
    raw_value: str


@dataclass
class NormResult:
    value: str | None
    unit: str | None
    matched_allowed: bool | None
    confidence: float


def _to_payload(items: list[NormItem]) -> str:
    import json
    return json.dumps(
        [
            {
                "name": it.name,
                "type": it.type,
                "unit": it.unit,
                "allowed_values": it.allowed_values,
                "raw_value": it.raw_value,
            }
            for it in items
        ],
        ensure_ascii=False,
    )


def _parse_one(obj: dict) -> NormResult:
    value = obj.get("value")
    value = None if value in (None, "", "null") else str(value)
    unit = obj.get("unit")
    unit = None if unit in (None, "", "null") else str(unit)
    matched = obj.get("matched_allowed")
    matched = matched if isinstance(matched, bool) else None
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        conf = 0.6
    return NormResult(value=value, unit=unit, matched_allowed=matched,
                      confidence=max(0.0, min(conf, 1.0)))


class ValueNormalizer:
    def __init__(self, ollama: OllamaGateway):
        self._ollama = ollama

    async def normalize(self, items: list[NormItem]) -> list[NormResult]:
        if not items:
            return []
        try:
            data = await self._ollama.chat_json(
                _SYSTEM_PROMPT, _to_payload(items),
                timeout=settings.normalize_timeout_seconds,
            )
            if isinstance(data, list) and len(data) == len(items):
                return [_parse_one(o if isinstance(o, dict) else {}) for o in data]
            logger.warning("normalizer: length mismatch (%s vs %s), falling back",
                           len(data) if isinstance(data, list) else "?", len(items))
        except Exception as exc:
            logger.warning("normalizer batch failed: %s", exc)

        # Fallback: passthrough each raw value with reduced confidence.
        return [
            NormResult(value=it.raw_value or None, unit=it.unit, matched_allowed=None, confidence=0.5)
            for it in items
        ]
