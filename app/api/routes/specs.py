from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_specs_service, product_query
from app.domain.product import ProductQuery
from app.domain.responses import SpecsResponse
from app.services.specs_service import SpecsNotFound, SpecsService

router = APIRouter()


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
