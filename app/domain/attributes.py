from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.domain.extraction import SourceResult
from app.domain.product import ProductQuery


class AttrType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"


class ResolveStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


# Bounded string aliases — keep request payloads (and the LLM prompts built from
# them) from growing without limit, which would let one request fan out into an
# unbounded number/size of Ollama calls (resource-exhaustion DoS).
_ShortStr = Annotated[str, StringConstraints(max_length=120)]


class AttributeSpec(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    type: AttrType = AttrType.STRING
    unit: _ShortStr | None = None                 # desired output unit
    # candidate set to snap to — bounded in count and per-item length so an
    # attacker can't blow up the normalizer prompt with a huge allowed list.
    allowed_values: list[_ShortStr] | None = Field(default=None, max_length=200)


class ResolveRequest(BaseModel):
    product: ProductQuery
    # Upper bound caps the per-request fan-out: each attribute can trigger search
    # + fetch + LLM work, so an unbounded list is a DoS vector. Callers that need
    # more should chunk (the catalog admin already sends ≤8 per request).
    attributes: list[AttributeSpec] = Field(min_length=1, max_length=100)
    official_only: bool = False
    max_sources: int = Field(default=5, ge=1, le=10)


class ResolvedAttribute(BaseModel):
    name: str
    type: AttrType
    value: str | None = None            # normalized / coerced / unit-converted
    unit: str | None = None
    raw_value: str | None = None        # extracted before normalization
    matched_allowed: bool | None = None  # only when allowed_values was provided
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source_url: str | None = None        # page the value was extracted from
    status: ResolveStatus = ResolveStatus.NOT_FOUND
    sources: list[SourceResult] = Field(default_factory=list)
