"""Match requested attribute names against specs already extracted from the
shared product pages — the cheap path that avoids any extra I/O or LLM calls."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import FetchedPage
from app.extraction.all_specs import extract_all_specs

_WS = re.compile(r"\s+")


@dataclass
class PooledSpec:
    name: str
    value: str
    url: str
    title: str


def build_spec_pool(pages: list[FetchedPage]) -> list[PooledSpec]:
    """Pre-extract every spec from every shared page into one flat pool."""
    pool: list[PooledSpec] = []
    for page in pages:
        for group in extract_all_specs(page.html):
            for entry in group.specs:
                pool.append(PooledSpec(entry.name, entry.value, page.url, page.title))
    return pool


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    ratio = difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short and short in long_:
        ratio = max(ratio, 0.80)
    return ratio


def _confidence(sim: float) -> float:
    if sim >= 0.92:
        return 0.88
    if sim >= 0.82:
        return 0.80
    return 0.72


def pool_labels(pool: list[PooledSpec]) -> list[str]:
    """Unique spec labels in first-seen order (input to the semantic matcher)."""
    seen: set[str] = set()
    out: list[str] = []
    for spec in pool:
        if spec.name not in seen:
            seen.add(spec.name)
            out.append(spec.name)
    return out


def candidates_for_label(
    pool: list[PooledSpec],
    label: str,
    confidence: float = 0.80,
) -> list[ExtractionCandidate]:
    """Build candidates for every pool entry whose label exactly equals `label`
    (used after a semantic match resolves a requested attribute to a page label)."""
    out: list[ExtractionCandidate] = []
    for spec in pool:
        if spec.name == label:
            out.append(
                ExtractionCandidate(
                    value=spec.value,
                    confidence=confidence,
                    source=SourceResult(
                        url=spec.url,
                        title=spec.title,
                        extraction_method=ExtractionMethod.CSS_SELECTOR,
                        confidence=confidence,
                        raw_value=spec.value,
                    ),
                )
            )
    return out


def match_in_pool(
    attribute_name: str,
    pool: list[PooledSpec],
    threshold: float,
) -> list[ExtractionCandidate]:
    """Return extraction candidates for pool entries whose label matches."""
    candidates: list[ExtractionCandidate] = []
    for spec in pool:
        sim = _similarity(spec.name, attribute_name)
        if sim >= threshold:
            conf = _confidence(sim)
            candidates.append(
                ExtractionCandidate(
                    value=spec.value,
                    confidence=conf,
                    source=SourceResult(
                        url=spec.url,
                        title=spec.title,
                        extraction_method=ExtractionMethod.CSS_SELECTOR,
                        confidence=conf,
                        raw_value=spec.value,
                    ),
                )
            )
    return candidates
