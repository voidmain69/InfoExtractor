from __future__ import annotations

from pydantic import BaseModel


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
