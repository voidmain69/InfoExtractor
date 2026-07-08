"""Match requested attribute names against specs already extracted from the
shared product pages — the cheap path that avoids any extra I/O or LLM calls."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import FetchedPage
from app.extraction.all_specs import extract_all_specs
from app.extraction.coerce import has_unit, label_unit
from app.extraction.label_similarity import similarity as _similarity
from app.extraction.text_repair import fix_text

_WS = re.compile(r"\s+")

# "Label: value" line, e.g. "Потужність: 2100 Вт" / "Print speed: 30 ppm". The
# label is 2–70 chars and value non-empty — same shape the HTML colon-line
# extractor uses, so text and page pools agree on what counts as a spec line.
_TEXT_PAIR_RE = re.compile(r"^\s*([^:\n]{2,70})\s*[::]\s*(\S.{0,300})$")


def _value_with_label_unit(spec: PooledSpec) -> str:
    """Carry a unit stated in the label onto a bare-number value, so
    ('Потужність (кВт)', '2,1') can later convert to W correctly."""
    unit = label_unit(spec.name)
    if unit and not has_unit(spec.value):
        return f"{spec.value} {unit}"
    return spec.value


@dataclass
class PooledSpec:
    name: str
    value: str
    url: str
    title: str


def page_pool(page: FetchedPage) -> list[PooledSpec]:
    """Extract one page's specs as pool entries."""
    return [
        PooledSpec(entry.name, entry.value, page.url, page.title)
        for group in extract_all_specs(page.html)
        for entry in group.specs
    ]


def build_spec_pool(pages: list[FetchedPage]) -> list[PooledSpec]:
    """Pre-extract every spec from every shared page into one flat pool."""
    pool: list[PooledSpec] = []
    for page in pages:
        pool.extend(page_pool(page))
    return pool


def text_pool(text: str, url: str = "imported-text", title: str = "") -> list[PooledSpec]:
    """Parse operator-supplied text (parsed file content) into pool entries.

    Deterministic, no LLM: each "Label: value" line becomes one PooledSpec. Cells
    joined with " | " (how the xlsx flattener emits a row) are split first, so a
    row "Потужність: 2100 | Вага: 5.2" yields two specs. Duplicate (label, value)
    pairs are dropped; mojibake in either half is repaired to match page pools.
    """
    out: list[PooledSpec] = []
    seen: set[tuple[str, str]] = set()
    for raw_line in text.splitlines():
        segments = raw_line.split(" | ") if " | " in raw_line else [raw_line]
        for seg in segments:
            m = _TEXT_PAIR_RE.match(_WS.sub(" ", fix_text(seg)).strip())
            if not m:
                continue
            name = m.group(1).strip()
            value = m.group(2).strip()
            if not name or not value or name == value:
                continue
            key = (name.lower(), value)
            if key in seen:
                continue
            seen.add(key)
            out.append(PooledSpec(name=name, value=value, url=url, title=title))
    return out


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
            value = _value_with_label_unit(spec)
            out.append(
                ExtractionCandidate(
                    value=value,
                    confidence=confidence,
                    source=SourceResult(
                        url=spec.url,
                        title=spec.title,
                        extraction_method=ExtractionMethod.CSS_SELECTOR,
                        confidence=confidence,
                        raw_value=value,
                    ),
                )
            )
    return out


# ── dimension blobs ──────────────────────────────────────────────────────────
# Pages often publish one row "Габарити (ШхВхГ): 60x84.5x44 см" while the PIM
# asks for width/height/depth separately. Parse the axis order from the label
# and pick the matching figure.

_DIM_WORDS: dict[str, tuple[str, ...]] = {
    "width": ("ширина", "width"),
    "height": ("висота", "высота", "height"),
    "depth": ("глибина", "глубина", "depth"),
}
_DIM_LETTER = {"ш": "width", "в": "height", "г": "depth",
               "w": "width", "h": "height", "d": "depth", "t": "depth"}
_DIM_LABEL_RE = re.compile(r"габарит|розмір|размер|dimension|\bразмеры\b", re.I)
_ORDER_RE = re.compile(r"([швгwhdt])\s*[x×х*]\s*([швгwhdt])\s*[x×х*]\s*([швгwhdt])", re.I)
_DIM_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_DIM_UNIT_RE = re.compile(r"\b(мм|см|mm|cm)\b", re.I)


def dimension_candidates(attribute_name: str, pool: list[PooledSpec]) -> list[ExtractionCandidate]:
    attr_low = attribute_name.lower()
    dim = next(
        (k for k, words in _DIM_WORDS.items() if any(w in attr_low for w in words)),
        None,
    )
    if dim is None:
        return []
    out: list[ExtractionCandidate] = []
    for spec in pool:
        if not _DIM_LABEL_RE.search(spec.name):
            continue
        m = _ORDER_RE.search(spec.name.lower())
        if not m:
            continue  # unknown axis order — guessing would corrupt data
        order = [_DIM_LETTER.get(g.lower()) for g in m.groups()]
        if dim not in order:
            continue
        nums = _DIM_NUM_RE.findall(spec.value)
        if len(nums) < 3:
            continue
        val = nums[order.index(dim)]
        unit_m = _DIM_UNIT_RE.search(spec.value)
        unit = unit_m.group(1) if unit_m else (label_unit(spec.name) or "")
        value = f"{val} {unit}".strip()
        out.append(
            ExtractionCandidate(
                value=value,
                confidence=0.72,
                source=SourceResult(
                    url=spec.url, title=spec.title,
                    extraction_method=ExtractionMethod.CSS_SELECTOR,
                    confidence=0.72, raw_value=f"{spec.name}: {spec.value}",
                ),
            )
        )
    return out


def pool_candidates(
    attribute_name: str,
    pages: list[FetchedPage],
    threshold: float,
) -> list[ExtractionCandidate]:
    """Full pool-matching stage over already-fetched pages: fuzzy label match,
    then curated synonyms, then dimension-blob parsing. Lets the single-attribute
    endpoint see everything the batch resolver sees (incl. embedded-JSON specs)."""
    from app.services.synonyms import find_synonym_label

    pool = build_spec_pool(pages)
    if not pool:
        return []
    candidates = match_in_pool(attribute_name, pool, threshold)
    if not candidates:
        syn = find_synonym_label(attribute_name, pool_labels(pool))
        if syn:
            candidates = candidates_for_label(pool, syn)
    if not candidates:
        candidates = dimension_candidates(attribute_name, pool)
    return candidates


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
            value = _value_with_label_unit(spec)
            candidates.append(
                ExtractionCandidate(
                    value=value,
                    confidence=conf,
                    source=SourceResult(
                        url=spec.url,
                        title=spec.title,
                        extraction_method=ExtractionMethod.CSS_SELECTOR,
                        confidence=conf,
                        raw_value=value,
                    ),
                )
            )
    return candidates
