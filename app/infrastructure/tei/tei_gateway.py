"""Thin gateway for TEI (Text Embeddings Inference): dense embeddings + optional
cross-encoder rerank. Everything is FAIL-SOFT — any error or shape mismatch
returns an empty result so callers degrade to their non-vector path (curated
synonyms + the LLM semantic tail) instead of failing."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TeiGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        dense_url: str,
        *,
        rerank_url: str | None = None,
        timeout: float = 10.0,
    ):
        self._client = client
        self._dense_url = dense_url.rstrip("/")
        self._rerank_url = rerank_url.rstrip("/") if rerank_url else None
        self._timeout = timeout

    @property
    def rerank_enabled(self) -> bool:
        return self._rerank_url is not None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """L2-normalized dense vectors, one per input; [] on any failure."""
        if not texts:
            return []
        try:
            resp = await self._client.post(
                f"{self._dense_url}/embed",
                json={"inputs": texts, "normalize": True, "truncate": True},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and len(data) == len(texts):
                return data
            logger.warning("TEI embed: expected %d vectors, got %s", len(texts), type(data))
        except Exception as exc:  # noqa: BLE001 — degrade, never raise into the resolve path
            logger.warning("TEI embed failed: %s", exc)
        return []

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """(index, score) per document from the cross-encoder; [] when the
        reranker is unconfigured or on any failure."""
        if not self._rerank_url or not documents:
            return []
        try:
            resp = await self._client.post(
                f"{self._rerank_url}/rerank",
                json={"query": query, "texts": documents, "truncate": True},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                (int(d["index"]), float(d["score"]))
                for d in data
                if isinstance(d, dict) and "index" in d and "score" in d
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("TEI rerank failed: %s", exc)
        return []
