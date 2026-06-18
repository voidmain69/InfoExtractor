from __future__ import annotations

from pydantic import BaseModel


class SpecEntry(BaseModel):
    name: str
    value: str


class SpecGroup(BaseModel):
    name: str
    specs: list[SpecEntry]
