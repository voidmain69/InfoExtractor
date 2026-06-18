import asyncio

from app.domain.extraction import ExtractionCandidate
from app.domain.page import FetchedPage, SearxNGResponse
from app.domain.product import ProductQuery
from app.extraction.extractors.css_selectors import CSSExtractor
from app.extraction.extractors.infobox import InfoboxExtractor
from app.extraction.extractors.jsonld import JSONLDExtractor
from app.extraction.extractors.llm import LLMExtractor
from app.infrastructure.llm.ollama import OllamaGateway

_LLM_CONF_THRESHOLD = 0.8


class ExtractionPipeline:
    def __init__(self, ollama: OllamaGateway):
        self._infobox = InfoboxExtractor()
        self._jsonld = JSONLDExtractor()
        self._css = CSSExtractor()
        self._llm = LLMExtractor(ollama)

    async def run(
        self,
        product: ProductQuery,
        attribute: str,
        searxng_response: SearxNGResponse,
        pages: list[FetchedPage],
    ) -> list[ExtractionCandidate]:
        candidates: list[ExtractionCandidate] = []

        candidates.extend(self._infobox.extract(product.search_string(), attribute, searxng_response))

        for page in pages:
            candidates.extend(self._jsonld.extract(product.search_string(), attribute, page))
            candidates.extend(self._css.extract(product.search_string(), attribute, page))

        high_conf = [c for c in candidates if c.confidence >= _LLM_CONF_THRESHOLD]
        if not high_conf:
            llm_tasks = [self._llm.extract(product, attribute, p) for p in pages[:3]]
            llm_results = await asyncio.gather(*llm_tasks, return_exceptions=True)
            for r in llm_results:
                if isinstance(r, ExtractionCandidate):
                    candidates.append(r)

        return candidates
