import asyncio
import json
import logging

import httpx

from config import settings
from models import ExtractionCandidate, ExtractionMethod, FetchedPage, ProductQuery, SourceResult

logger = logging.getLogger(__name__)

_http_client = httpx.AsyncClient(timeout=settings.llm_extraction_timeout_seconds + 5)

_SYSTEM_PROMPT = (
    "You are a product specification extractor. "
    "Given page text, find the value of the requested attribute for the product. "
    "If the value is NOT explicitly stated in the text, return found=false. "
    "Do NOT infer, extrapolate, or use general knowledge. "
    'Respond ONLY with JSON: {"found": bool, "value": string|null, "unit": string|null, "confidence": float}'
)

_WINDOW = 2500  # chars around the attribute keyword hit


def _focused_text(text: str, attribute: str, window: int = _WINDOW) -> str:
    """Return a focused window around the first occurrence of any attribute keyword."""
    needle = attribute.lower()
    # Try full attribute first, then each token
    candidates = [needle] + [t for t in needle.split() if len(t) > 3]
    for kw in candidates:
        idx = text.lower().find(kw)
        if idx >= 0:
            start = max(0, idx - window // 2)
            end = min(len(text), idx + window // 2)
            return text[start:end]
    # No hit — return the end of the text (specs are usually after navigation)
    return text[-window * 2:] if len(text) > window * 2 else text


class LLMExtractor:
    async def extract(
        self,
        product: ProductQuery,
        attribute: str,
        page: FetchedPage,
    ) -> ExtractionCandidate | None:
        text_chunk = _focused_text(page.text, attribute)
        payload = {
            "model": settings.ollama_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Product: {product.display_name()}\n"
                        f"Find attribute: {attribute}\n\n"
                        f"Page text:\n{text_chunk}"
                    ),
                },
            ],
            "stream": False,
        }
        try:
            result = await asyncio.wait_for(
                self._call_ollama(payload),
                timeout=settings.llm_extraction_timeout_seconds,
            )
            return self._parse_result(result, page)
        except Exception as exc:
            logger.debug("llm_extractor failed for %s: %s", page.url, exc)
            return None

    async def _call_ollama(self, payload: dict) -> dict:
        resp = await _http_client.post(f"{settings.ollama_url}/api/chat", json=payload)
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        return json.loads(content)

    def _parse_result(self, data: dict, page: FetchedPage) -> ExtractionCandidate | None:
        if not data.get("found"):
            return None
        value = data.get("value")
        if not value:
            return None

        conf = float(data.get("confidence") or 0.7)
        conf = max(0.0, min(conf, 0.85))

        return ExtractionCandidate(
            value=str(value),
            unit=data.get("unit"),
            confidence=conf,
            source=SourceResult(
                url=page.url,
                title=page.title,
                extraction_method=ExtractionMethod.LLM,
                confidence=conf,
                raw_value=str(value),
                unit=data.get("unit"),
            ),
        )
