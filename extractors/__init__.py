import asyncio

from models import ExtractionCandidate, FetchedPage, ProductQuery, SearxNGResponse

from extractors.infobox import InfoboxExtractor
from extractors.jsonld import JSONLDExtractor
from extractors.css_selectors import CSSExtractor
from extractors.llm_extractor import LLMExtractor

_LLM_CONF_THRESHOLD = 0.8


async def run_pipeline(
    product: ProductQuery,
    attribute: str,
    searxng_response: SearxNGResponse,
    pages: list[FetchedPage],
) -> list[ExtractionCandidate]:
    candidates: list[ExtractionCandidate] = []

    candidates.extend(InfoboxExtractor().extract(product.search_string(), attribute, searxng_response))

    jsonld = JSONLDExtractor()
    css = CSSExtractor()
    for page in pages:
        candidates.extend(jsonld.extract(product.search_string(), attribute, page))
        candidates.extend(css.extract(product.search_string(), attribute, page))

    high_conf = [c for c in candidates if c.confidence >= _LLM_CONF_THRESHOLD]
    if not high_conf:
        llm = LLMExtractor()
        llm_tasks = [llm.extract(product, attribute, p) for p in pages[:3]]
        llm_results = await asyncio.gather(*llm_tasks, return_exceptions=True)
        for r in llm_results:
            if isinstance(r, ExtractionCandidate):
                candidates.append(r)

    return candidates
