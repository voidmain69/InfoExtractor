from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

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


class AttributeSpec(BaseModel):
    name: str
    type: AttrType = AttrType.STRING
    unit: str | None = None                       # desired output unit
    allowed_values: list[str] | None = None       # candidate set to snap to


class ResolveRequest(BaseModel):
    product: ProductQuery
    attributes: list[AttributeSpec] = Field(min_length=1)
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
