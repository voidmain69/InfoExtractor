from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
