import difflib
import json
import logging

from bs4 import BeautifulSoup

from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import FetchedPage
from app.extraction.base import BaseExtractor

logger = logging.getLogger(__name__)


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip(), autojunk=False).ratio()


class JSONLDExtractor(BaseExtractor):
    def extract(
        self,
        product: str,
        attribute: str,
        source: FetchedPage,
    ) -> list[ExtractionCandidate]:
        soup = BeautifulSoup(source.html, "lxml")
        candidates = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            candidates.extend(self._parse_node(data, attribute, source))
        return candidates

    def _parse_node(self, node, attribute: str, page: FetchedPage) -> list[ExtractionCandidate]:
        candidates = []
        if isinstance(node, list):
            for item in node:
                candidates.extend(self._parse_node(item, attribute, page))
            return candidates

        if not isinstance(node, dict):
            return candidates

        # Unwrap @graph
        if "@graph" in node:
            return self._parse_node(node["@graph"], attribute, page)

        node_type = node.get("@type", "")
        if isinstance(node_type, list):
            node_type = " ".join(node_type)

        if "Product" in node_type or "TechArticle" in node_type:
            candidates.extend(self._from_product(node, attribute, page))

        # Recurse into nested objects
        for val in node.values():
            if isinstance(val, (dict, list)):
                candidates.extend(self._parse_node(val, attribute, page))

        return candidates

    def _from_product(self, node: dict, attribute: str, page: FetchedPage) -> list[ExtractionCandidate]:
        candidates = []
        for prop in node.get("additionalProperty", []):
            if not isinstance(prop, dict):
                continue
            name = prop.get("name") or prop.get("propertyID") or ""
            value = prop.get("value") or prop.get("unitText") or ""
            if not name or not value:
                continue
            sim = _similarity(name, attribute)
            if sim >= 0.75:
                conf = 0.9 if sim >= 0.85 else 0.75
                raw = str(value)
                candidates.append(
                    ExtractionCandidate(
                        value=raw,
                        confidence=conf,
                        source=SourceResult(
                            url=page.url,
                            title=page.title,
                            extraction_method=ExtractionMethod.JSONLD,
                            confidence=conf,
                            raw_value=raw,
                        ),
                    )
                )

        description = node.get("description") or node.get("text") or ""
        if description and not candidates:
            lower = description.lower()
            if any(t in lower for t in attribute.lower().split() if len(t) > 3):
                snippet = description[:300]
                candidates.append(
                    ExtractionCandidate(
                        value=snippet,
                        confidence=0.75,
                        source=SourceResult(
                            url=page.url,
                            title=page.title,
                            extraction_method=ExtractionMethod.JSONLD,
                            confidence=0.75,
                            raw_value=snippet,
                        ),
                    )
                )

        return candidates
