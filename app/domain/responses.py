from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.extraction import SourceResult
from app.domain.product import ProductQuery
from app.domain.specs import SpecGroup


class AttributeResponse(BaseModel):
    product: ProductQuery
    attribute: str
    value: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    sources: list[SourceResult] = Field(default_factory=list)
    search_queries_used: list[str] = Field(default_factory=list)
    official_domain: str | None = None
    official_only_fallback: bool = False
    cached: bool = False


class SpecsResponse(BaseModel):
    product: ProductQuery
    groups: list[SpecGroup]
    source_url: str | None = None
    total_specs: int = 0
    cached: bool = False
