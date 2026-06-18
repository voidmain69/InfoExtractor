from __future__ import annotations

from enum import Enum

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


class ExtractionCandidate(BaseModel):
    value: str
    unit: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: SourceResult
