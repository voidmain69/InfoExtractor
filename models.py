from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProductQuery(BaseModel):
    name: str
    category: str | None = None
    brand: str | None = None
    article: str | None = None
    ean13: str | None = None
    upc: str | None = None

    def search_string(self) -> str:
        """Compact string for search queries: brand + name + article."""
        parts = []
        if self.brand:
            parts.append(self.brand)
        parts.append(self.name)
        if self.article:
            parts.append(self.article)
        return " ".join(parts)

    def display_name(self) -> str:
        """Human-readable label for logs and responses."""
        parts = []
        if self.category:
            parts.append(self.category)
        if self.brand:
            parts.append(self.brand)
        parts.append(self.name)
        if self.article:
            parts.append(f"({self.article})")
        return " ".join(parts)

    def identifier(self) -> str:
        """Most precise identifier available: EAN > UPC > article > search_string."""
        return self.ean13 or self.upc or self.article or self.search_string()


class ExtractionMethod(str, Enum):
    INFOBOX = "infobox"
    JSONLD = "jsonld"
    CSS_SELECTOR = "css_selector"
    LLM = "llm"


class SourceResult(BaseModel):
    url: str
    title: str
    extraction_method: ExtractionMethod
    confidence: float = Field(ge=0.0, le=1.0)
    raw_value: str | None = None
    unit: str | None = None


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


class SpecEntry(BaseModel):
    name: str
    value: str


class SpecGroup(BaseModel):
    name: str
    specs: list[SpecEntry]


class SpecsResponse(BaseModel):
    product: ProductQuery
    groups: list[SpecGroup]
    source_url: str | None = None
    total_specs: int = 0
    cached: bool = False


class SearxNGResult(BaseModel):
    url: str
    title: str
    content: str | None = None
    score: float | None = None


class SearxNGResponse(BaseModel):
    results: list[SearxNGResult] = Field(default_factory=list)
    infoboxes: list[dict[str, Any]] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)


class FetchedPage(BaseModel):
    url: str
    title: str
    html: str
    text: str
    status_code: int


class ExtractionCandidate(BaseModel):
    value: str
    unit: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: SourceResult
