from fastapi import APIRouter, Depends

from app.api.deps import get_resolve_service
from app.domain.attributes import ResolveRequest
from app.domain.responses import ResolveResponse
from app.services.resolve_service import ResolveService

router = APIRouter()


@router.post("/attributes", response_model=ResolveResponse)
async def resolve_attributes(
    body: ResolveRequest,
    service: ResolveService = Depends(get_resolve_service),
):
    """Resolve a batch of typed attributes for one product in a single request.

    The product's source pages are fetched and parsed once; each attribute is
    resolved against that shared content, then values are coerced to the
    requested type, unit-converted, and snapped to allowed_values by the AI layer.
    """
    return await service.resolve(
        body.product, body.attributes, body.official_only, body.max_sources
    )
