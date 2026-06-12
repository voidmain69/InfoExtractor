import difflib
import re

from models import ExtractionCandidate, ExtractionMethod, SourceResult

_WS = re.compile(r"\s+")

METHOD_WEIGHTS: dict[ExtractionMethod, float] = {
    ExtractionMethod.INFOBOX: 1.0,
    ExtractionMethod.CSS_SELECTOR: 1.0,
    ExtractionMethod.JSONLD: 0.9,
    ExtractionMethod.LLM: 0.7,
}

_FUZZY_GROUP_THRESHOLD = 0.72


def _norm(value: str) -> str:
    return _WS.sub(" ", value).strip().lower().strip(".,;:")


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _fuzzy_group(candidates: list[ExtractionCandidate]) -> list[list[ExtractionCandidate]]:
    """Cluster candidates by fuzzy similarity of their normalized values."""
    groups: list[list[ExtractionCandidate]] = []
    representatives: list[str] = []

    for c in candidates:
        norm_val = _norm(c.value)
        matched = False
        for i, rep in enumerate(representatives):
            if _sim(norm_val, rep) >= _FUZZY_GROUP_THRESHOLD:
                groups[i].append(c)
                matched = True
                break
        if not matched:
            groups.append([c])
            representatives.append(norm_val)

    return groups


def reconcile(
    candidates: list[ExtractionCandidate],
) -> tuple[str | None, str | None, float, list[SourceResult]]:
    if not candidates:
        return None, None, 0.0, []

    groups = _fuzzy_group(candidates)

    def group_score(group: list[ExtractionCandidate]) -> float:
        return sum(c.confidence * METHOD_WEIGHTS.get(c.source.extraction_method, 0.5) for c in group)

    groups.sort(key=group_score, reverse=True)

    winning_group = groups[0]
    winning_score = group_score(winning_group)
    total_score = sum(group_score(g) for g in groups)

    base_confidence = winning_score / total_score if total_score > 0 else 0.0

    if len(groups) > 1:
        second_score = group_score(groups[1])
        if second_score >= 0.7 * winning_score:
            base_confidence *= 0.75

    best_candidate = max(winning_group, key=lambda c: c.confidence)
    base_confidence = min(base_confidence, best_candidate.confidence)

    all_sources = [c.source for c in candidates]

    return best_candidate.value, best_candidate.unit, round(base_confidence, 4), all_sources
