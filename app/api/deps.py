from __future__ import annotations

from fastapi import Query, Request

from app.domain.product import ProductQuery
from app.services.attribute_service import AttributeService
from app.services.specs_service import SpecsService


def product_query(
    name: str = Query(..., description="Product name (required)"),
    category: str | None = Query(default=None, description="Product category"),
    brand: str | None = Query(default=None, description="Brand / manufacturer"),
    article: str | None = Query(default=None, description="Article / SKU"),
    ean13: str | None = Query(default=None, description="EAN-13 barcode"),
    upc: str | None = Query(default=None, description="UPC barcode"),
) -> ProductQuery:
    return ProductQuery(
        name=name,
        category=category,
        brand=brand,
        article=article,
        ean13=ean13,
        upc=upc,
    )


def get_attribute_service(request: Request) -> AttributeService:
    return request.app.state.attribute_service


def get_specs_service(request: Request) -> SpecsService:
    return request.app.state.specs_service


def get_searxng(request: Request):
    return request.app.state.searxng
