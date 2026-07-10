from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_resolve_service
from app.domain.attributes import AttributeSpec, ResolveRequest
from app.domain.responses import ResolveResponse
from app.domain.claims import ClaimsResponse
from app.services.resolve_service import ResolveService

router = APIRouter()


class TextClaimsRequest(BaseModel):
    # Category-blind: no `attributes` — the consumer maps raw labels itself.
    text: str = Field(min_length=1, max_length=200_000)


class UrlResolveRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    # Same per-request fan-out cap as ResolveRequest: each attribute triggers
    # match/coerce/normalize work, so an unbounded list is a DoS vector.
    attributes: list[AttributeSpec] = Field(min_length=1, max_length=100)


class TextResolveRequest(BaseModel):
    # Bounded so one request can't blow up the parse/normalizer prompt.
    text: str = Field(min_length=1, max_length=200_000)
    attributes: list[AttributeSpec] = Field(min_length=1, max_length=100)


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


@router.post("/attributes/from-url", response_model=ResolveResponse)
async def resolve_attributes_from_url(
    body: UrlResolveRequest,
    service: ResolveService = Depends(get_resolve_service),
):
    """Resolve typed attributes from a single supplied product-page URL (no search).

    The page is fetched through the SSRF-guarded fetcher and JS-rendered with
    Playwright when static extraction is sparse; each attribute is then resolved
    against that page's specs with the same coercion / unit-conversion / enum-snap
    pipeline as POST /attributes. Use when the operator already has the source URL.
    """
    return await service.resolve_from_url(body.url, body.attributes)


@router.post("/attributes/from-text", response_model=ResolveResponse)
async def resolve_attributes_from_text(
    body: TextResolveRequest,
    service: ResolveService = Depends(get_resolve_service),
):
    """Resolve typed attributes from supplied text (parsed file content).

    A spec pool is built deterministically from "Label: value" lines, then the
    same coercion / unit-conversion / enum-snap pipeline runs. No search and no
    page rendering — a text blob has no DOM, so attributes with no matching line
    come back not_found for the caller to handle (e.g. manual entry)."""
    return await service.resolve_from_text(body.text, body.attributes)


@router.post("/claims/from-text", response_model=ClaimsResponse)
async def claims_from_text(
    body: TextClaimsRequest,
    service: ResolveService = Depends(get_resolve_service),
):
    """Return the document's raw label:value facts as category-BLIND claims
    (Phase-3 Ц1 / М1b). Deterministic — no LLM, no schema in the request: the
    consumer (catalog-service) maps `raw_label` onto its own template keys and
    runs typed coercion/validation. Each claim carries an evidence span, so a
    downstream value with no supporting claim can be rejected as a hallucination."""
    return service.claims_from_text(body.text)
