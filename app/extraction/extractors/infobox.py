import difflib

from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import SearxNGResponse
from app.extraction.base import BaseExtractor

_ANSWER_URL = "searxng://answer"
_INFOBOX_URL = "searxng://infobox"


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip(), autojunk=False).ratio()


class InfoboxExtractor(BaseExtractor):
    def extract(
        self,
        product: str,
        attribute: str,
        source: SearxNGResponse,
    ) -> list[ExtractionCandidate]:
        candidates: list[ExtractionCandidate] = []
        candidates.extend(self._from_answers(attribute, source.answers))
        candidates.extend(self._from_infoboxes(attribute, source.infoboxes))
        return candidates

    def _from_answers(self, attribute: str, answers: list[str]) -> list[ExtractionCandidate]:
        candidates = []
        for answer in answers:
            if not answer:
                continue
            lower = answer.lower()
            attr_tokens = attribute.lower().split()
            if any(t in lower for t in attr_tokens) and any(ch.isdigit() for ch in answer):
                candidates.append(
                    ExtractionCandidate(
                        value=answer,
                        confidence=0.7,
                        source=SourceResult(
                            url=_ANSWER_URL,
                            title="SearxNG direct answer",
                            extraction_method=ExtractionMethod.INFOBOX,
                            confidence=0.7,
                            raw_value=answer,
                        ),
                    )
                )
        return candidates

    def _from_infoboxes(self, attribute: str, infoboxes: list[dict]) -> list[ExtractionCandidate]:
        candidates = []
        for box in infoboxes:
            url = box.get("id") or box.get("url") or _INFOBOX_URL
            title = box.get("infobox") or box.get("title") or "Infobox"

            for attr_item in box.get("attributes", []):
                label = attr_item.get("label") or attr_item.get("name") or ""
                value = attr_item.get("value") or attr_item.get("value_string") or ""
                if not label or not value:
                    continue
                sim = _similarity(label, attribute)
                if sim >= 0.75:
                    conf = 0.95 if sim >= 0.9 else 0.85
                    candidates.append(
                        ExtractionCandidate(
                            value=str(value),
                            confidence=conf,
                            source=SourceResult(
                                url=url,
                                title=title,
                                extraction_method=ExtractionMethod.INFOBOX,
                                confidence=conf,
                                raw_value=str(value),
                            ),
                        )
                    )

            content = box.get("content") or ""
            if content:
                lower_content = content.lower()
                for token in attribute.lower().split():
                    if len(token) > 3 and token in lower_content:
                        # A 300-char prose snippet is a weak signal, not a clean
                        # value — keep its confidence low so it can't dominate.
                        candidates.append(
                            ExtractionCandidate(
                                value=content[:300],
                                confidence=0.5,
                                source=SourceResult(
                                    url=url,
                                    title=title,
                                    extraction_method=ExtractionMethod.INFOBOX,
                                    confidence=0.5,
                                    raw_value=content[:300],
                                ),
                            )
                        )
                        break

        return candidates
