from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_attribute_service, product_query
from app.domain.product import ProductQuery
from app.domain.responses import AttributeResponse
from app.services.attribute_service import AttributeNotFound, AttributeService

router = APIRouter()


@router.get("/attribute", response_model=AttributeResponse)
async def get_attribute(
    product: ProductQuery = Depends(product_query),
    attribute: str = Query(..., description="Attribute to find, e.g. 'rear USB ports'"),
    max_sources: int = Query(default=5, ge=1, le=10),
    official_only: bool = Query(default=False, description="Restrict results to manufacturer's official site"),
    service: AttributeService = Depends(get_attribute_service),
):
    try:
        return await service.get_attribute(product, attribute, max_sources, official_only)
    except AttributeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
