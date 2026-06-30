from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

# Bounded identity strings — these flow into search queries and LLM prompts, so
# unbounded sizes would be a resource-exhaustion vector.
_Str = Annotated[str, StringConstraints(max_length=300)]


class ProductQuery(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    category: _Str | None = None
    brand: _Str | None = None
    article: _Str | None = None
    mpn: _Str | None = None
    ean13: _Str | None = None
    upc: _Str | None = None

    def search_string(self) -> str:
        """Compact string for search queries: brand + name + article/mpn."""
        parts = []
        if self.brand:
            parts.append(self.brand)
        parts.append(self.name)
        if self.article:
            parts.append(self.article)
        elif self.mpn:
            parts.append(self.mpn)
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
        """Most precise identifier available: EAN > UPC > MPN > article > search_string."""
        return self.ean13 or self.upc or self.mpn or self.article or self.search_string()
