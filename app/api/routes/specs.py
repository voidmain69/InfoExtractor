from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_specs_service, product_query
from app.domain.product import ProductQuery
from app.domain.responses import SpecsResponse
from app.services.specs_service import SpecsNotFound, SpecsService

router = APIRouter()


class UrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.get("/specs", response_model=SpecsResponse)
async def get_all_specs(
    product: ProductQuery = Depends(product_query),
    official_only: bool = Query(default=False),
    service: SpecsService = Depends(get_specs_service),
):
    """Extract all product specifications as structured groups."""
    try:
        return await service.get_specs(product, official_only)
    except SpecsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/specs/from-url", response_model=SpecsResponse)
async def get_specs_from_url(
    body: UrlRequest,
    service: SpecsService = Depends(get_specs_service),
):
    """Extract specs from a single supplied product-page URL (no search).

    The URL is fetched through the same SSRF-guarded fetcher and falls back to a
    Playwright render when static extraction is sparse."""
    try:
        return await service.get_specs_from_url(body.url)
    except SpecsNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
