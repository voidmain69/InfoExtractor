"""Network-free tests for the SemanticMatcher vector tier (dense ANN + optional
cross-encoder rerank). A fake TeiGateway feeds controlled vectors/scores; the
Ollama gateway is mocked so we can assert WHEN the LLM tail runs.

    pytest test_semantic.py -q
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.semantic_matcher import SemanticMatcher


def _vec(cos: float) -> list[float]:
    # dot() uses both components; make the first component the cosine vs [1, 0].
    return [cos, math.sqrt(max(0.0, 1 - cos * cos))]


class FakeTei:
    def __init__(self, vectors, rerank=None):
        self._vectors = vectors
        self._rerank = rerank
        self.rerank_enabled = rerank is not None
        self.embed = AsyncMock(return_value=vectors)
        self.rerank = AsyncMock(return_value=rerank or [])


def _ollama(labels_for=None):
    o = MagicMock()
    o.chat_json = AsyncMock(return_value=labels_for or [])
    return o


@pytest.mark.asyncio
async def test_ann_resolves_clear_winner_without_llm():
    # labels [Update frequency, Color]; attr [Refresh rate] ≈ label0 (0.95), far from label1.
    tei = FakeTei([_vec(0.95), _vec(0.20), _vec(1.0)])
    o = _ollama()
    m = SemanticMatcher(o, tei)
    out = await m.match(["Refresh rate"], ["Update frequency", "Color"])
    assert out == ["Update frequency"]
    o.chat_json.assert_not_called()


@pytest.mark.asyncio
async def test_ann_defers_ambiguous_to_llm():
    # Two labels near-tied on cosine (0.75 vs 0.74, margin < 0.04) → LLM tail runs.
    tei = FakeTei([_vec(0.75), _vec(0.74), _vec(1.0)])
    o = _ollama([{"attribute": "x", "label": "A"}])
    m = SemanticMatcher(o, tei)
    out = await m.match(["x"], ["A", "B"])
    assert out == ["A"]
    o.chat_json.assert_called_once()


@pytest.mark.asyncio
async def test_rerank_breaks_a_near_tie():
    # Dense near-tie A(0.80) ~ B(0.79); reranker confidently prefers B.
    tei = FakeTei([_vec(0.80), _vec(0.79), _vec(1.0)], rerank=[(0, 0.15), (1, 0.95)])
    o = _ollama()
    m = SemanticMatcher(o, tei)
    out = await m.match(["x"], ["A", "B"])
    assert out == ["B"]
    o.chat_json.assert_not_called()


@pytest.mark.asyncio
async def test_tei_unavailable_falls_through_to_llm():
    tei = FakeTei([])  # embed returns nothing → all None → LLM
    o = _ollama([{"attribute": "x", "label": "A"}])
    m = SemanticMatcher(o, tei)
    out = await m.match(["x"], ["A", "B"])
    assert out == ["A"]
    o.chat_json.assert_called_once()


@pytest.mark.asyncio
async def test_no_tei_is_llm_only():
    o = _ollama([{"attribute": "x", "label": "A"}])
    m = SemanticMatcher(o)  # no TeiGateway
    out = await m.match(["x"], ["A", "B"])
    assert out == ["A"]
    o.chat_json.assert_called_once()
