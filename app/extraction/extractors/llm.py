import logging

from app.core.config import settings
from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import FetchedPage
from app.domain.product import ProductQuery
from app.infrastructure.llm.ollama import OllamaGateway

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a product specification extractor. "
    "Given page text, find the value of the requested attribute for the product. "
    "If the value is NOT explicitly stated in the text, return found=false. "
    "Do NOT infer, extrapolate, or use general knowledge. "
    'Respond ONLY with JSON: {"found": bool, "value": string|null, "unit": string|null, "confidence": float}'
)

_WINDOW = 2500  # chars around the attribute keyword hit


def _focused_text(text: str, attribute: str, window: int = _WINDOW) -> str:
    """Return the best window around an attribute keyword.

    Picks the occurrence whose surrounding text is densest in digits — a proxy
    for "this is the spec table" rather than a passing mention in a nav menu or
    a 'related products' block (which is where the first hit often lands).
    """
    low = text.lower()
    needle = attribute.lower()
    keywords = [needle] + [t for t in needle.split() if len(t) > 3]

    best_chunk: str | None = None
    best_density = -1
    for kw in keywords:
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx < 0:
                break
            s = max(0, idx - window // 2)
            e = min(len(text), idx + window // 2)
            chunk = text[s:e]
            density = sum(c.isdigit() for c in chunk)
            if density > best_density:
                best_density = density
                best_chunk = chunk
            start = idx + len(kw)

    if best_chunk is not None:
        return best_chunk
    # No hit — return the end of the text (specs are usually after navigation)
    return text[-window * 2:] if len(text) > window * 2 else text


class LLMExtractor:
    def __init__(self, ollama: OllamaGateway):
        self._ollama = ollama

    async def extract(
        self,
        product: ProductQuery,
        attribute: str,
        page: FetchedPage,
    ) -> ExtractionCandidate | None:
        text_chunk = _focused_text(page.text, attribute)
        user_content = (
            f"Product: {product.display_name()}\n"
            f"Find attribute: {attribute}\n\n"
            f"Page text:\n{text_chunk}"
        )
        try:
            data = await self._ollama.chat_json(
                _SYSTEM_PROMPT, user_content,
                timeout=settings.llm_extraction_timeout_seconds,
            )
            return self._parse_result(data, page)
        except Exception as exc:
            logger.debug("llm_extractor failed for %s: %s", page.url, exc)
            return None

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
