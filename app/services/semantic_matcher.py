"""Semantically map requested attribute names onto the labels actually present
on a product page — bridging synonyms and translations (e.g. "Refresh rate" ↔
"Update frequency", "Curvature" ↔ "Curved screen") that string-fuzzy matching misses."""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.infrastructure.llm.ollama import OllamaGateway

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You map requested product attributes to the spec labels actually found on a page, "
    "by MEANING — accounting for synonyms, translations (Ukrainian/Russian/English), and "
    "unit-implied names. Input JSON: {\"labels\": [page labels], \"attributes\": [requested "
    "names]}. For EACH requested attribute, choose the single label that denotes the SAME "
    "characteristic, or null if none does. Examples: 'Refresh rate' = 'Update frequency'; "
    "'Response time' = 'Reaction time'; 'Curvature' = 'Curved screen'; 'Panel type' = "
    "'Type of matrix'. Do not guess when unsure — return null. Respond ONLY with a JSON "
    "array, one object per attribute, SAME order: "
    "[{\"attribute\": <name>, \"label\": <one of labels exactly, or null>}]."
)


class SemanticMatcher:
    def __init__(self, ollama: OllamaGateway):
        self._ollama = ollama

    async def match(self, attributes: list[str], labels: list[str]) -> list[str | None]:
        """Return, per requested attribute (same order), the matching page label or None."""
        if not attributes or not labels:
            return [None] * len(attributes)

        payload = json.dumps({"labels": labels, "attributes": attributes}, ensure_ascii=False)
        label_set = set(labels)
        try:
            data = await self._ollama.chat_json(
                _SYSTEM_PROMPT, payload, timeout=settings.normalize_timeout_seconds
            )
            if isinstance(data, list) and len(data) == len(attributes):
                out: list[str | None] = []
                for obj in data:
                    label = obj.get("label") if isinstance(obj, dict) else None
                    out.append(label if isinstance(label, str) and label in label_set else None)
                return out
            logger.warning("semantic match: length/shape mismatch, skipping")
        except Exception as exc:
            logger.warning("semantic match failed: %s", exc)
        return [None] * len(attributes)
