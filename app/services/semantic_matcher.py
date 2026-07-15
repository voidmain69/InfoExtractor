"""Semantically map requested attribute names onto the labels actually present
on a product page — bridging synonyms and translations (e.g. "Refresh rate" ↔
"Update frequency", "Curvature" ↔ "Curved screen") that string-fuzzy matching misses."""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.infrastructure.llm.ollama import OllamaGateway
from app.infrastructure.tei.tei_gateway import TeiGateway

logger = logging.getLogger(__name__)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

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


# Dense candidates handed to the cross-encoder reranker per attribute, and the
# min rerank-score lead for the reranker to break a dense near-tie confidently.
_RERANK_TOP_K = 8
_RERANK_MIN_LEAD = 0.1


class SemanticMatcher:
    def __init__(self, ollama: OllamaGateway, tei: TeiGateway | None = None):
        self._ollama = ollama
        self._tei = tei

    async def match(self, attributes: list[str], labels: list[str]) -> list[str | None]:
        """Return, per requested attribute (same order), the matching page label or None.

        Tiered: a dense-embedding (+ optional rerank) pass resolves the confident
        cases without the LLM; only the leftovers go to the LLM semantic call. The
        vector tier is skipped entirely when no TeiGateway is configured — behaviour
        is then identical to the previous LLM-only matcher."""
        if not attributes or not labels:
            return [None] * len(attributes)

        out: list[str | None] = [None] * len(attributes)

        # Tier 1 — dense ANN (+ optional cross-encoder rerank).
        if self._tei is not None:
            out = await self._ann_match(attributes, labels)

        # Tier 2 — LLM tail for whatever the vector tier left unresolved.
        pending = [i for i, lbl in enumerate(out) if lbl is None]
        if pending:
            llm = await self._llm_match([attributes[i] for i in pending], labels)
            for i, lbl in zip(pending, llm):
                out[i] = lbl
        return out

    async def _ann_match(self, attributes: list[str], labels: list[str]) -> list[str | None]:
        """Dense cosine match per attribute. A clear dense winner (>= accept, and
        beating the runner-up by margin) is taken outright. In the ambiguous
        near-tie zone the cross-encoder — when configured — breaks the tie, but
        only when it leads confidently and its pick still clears accept. Everything
        else stays None for the LLM tail. Any TEI failure ⇒ all None."""
        out: list[str | None] = [None] * len(attributes)
        vecs = await self._tei.embed(labels + attributes)
        if len(vecs) != len(labels) + len(attributes):
            return out
        label_vecs = vecs[: len(labels)]
        attr_vecs = vecs[len(labels):]
        accept, margin = settings.semantic_ann_accept, settings.semantic_ann_margin

        for a_i, av in enumerate(attr_vecs):
            scored = sorted(((_dot(av, lv), j) for j, lv in enumerate(label_vecs)), reverse=True)
            best_score, best_j = scored[0]
            second = scored[1][0] if len(scored) > 1 else -1.0
            if best_score < accept:
                continue  # not confident enough → LLM tail
            if best_score - second >= margin:
                out[a_i] = labels[best_j]  # clear dense winner
                continue
            # Near-tie: let the reranker break it, if it leads confidently.
            if self._tei.rerank_enabled:
                tie = [(s, j) for s, j in scored[:_RERANK_TOP_K] if s >= accept - margin]
                if len(tie) > 1:
                    rr = await self._tei.rerank(attributes[a_i], [labels[j] for _, j in tie])
                    if rr:
                        rr.sort(key=lambda x: x[1], reverse=True)
                        lead = len(rr) == 1 or (rr[0][1] - rr[1][1]) >= _RERANK_MIN_LEAD
                        local = rr[0][0]
                        if lead and 0 <= local < len(tie) and tie[local][0] >= accept:
                            out[a_i] = labels[tie[local][1]]
            # else: ambiguous, no confident reranker → leave for the LLM
        return out

    async def _llm_match(self, attributes: list[str], labels: list[str]) -> list[str | None]:
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
