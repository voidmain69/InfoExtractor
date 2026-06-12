from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    product: str
    attribute: str
    value: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    sources: list[SourceResult] = Field(default_factory=list)
    search_queries_used: list[str] = Field(default_factory=list)
    official_domain: str | None = None
    official_only_fallback: bool = False
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
