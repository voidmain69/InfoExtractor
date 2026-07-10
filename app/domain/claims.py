"""Claims contract (Phase-3 Ц1 / М1b).

A *claim* is a raw label:value fact extracted from a document, WITHOUT any
knowledge of a target category's schema. The consumer (catalog-service) maps
`raw_label` onto its own template keys and runs typed coercion / validation /
consensus. Moving the schema out of the extractor is what lets one extractor
serve hundreds of categories.

`kind`:
  - 'structured' — the claim is a verbatim source row (label:value line, table
    cell, JSON-LD property). No LLM involved, so it cannot be a hallucination;
    `evidence` points at the exact source span.
  - 'extracted'  — an LLM lifted the value out of prose (reserved; the text
    endpoint is fully structured/deterministic today).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimEvidence(BaseModel):
    """Where the claim came from in the source — the anti-hallucination anchor."""
    char_start: int | None = Field(default=None, description="Start offset of the label in the source text, if locatable.")
    char_end: int | None = Field(default=None, description="End offset of the label in the source text.")
    snippet: str = Field(description="The source row the claim was read from (capped).")


class Claim(BaseModel):
    raw_label: str = Field(description="The label exactly as it appears in the source.")
    raw_value: str = Field(description="The value exactly as it appears in the source.")
    kind: str = Field(default="structured", description="'structured' (verbatim row) | 'extracted' (LLM).")
    evidence: ClaimEvidence


class ClaimsResponse(BaseModel):
    claims: list[Claim]
    cached: bool = False
