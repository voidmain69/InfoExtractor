from fastapi import APIRouter, Depends, Query

from app.api.deps import get_searxng
from app.infrastructure.search.searxng import SearxNGClient

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/search")
async def search_proxy(
    q: str = Query(..., description="Search query"),
    searxng: SearxNGClient = Depends(get_searxng),
):
    return await searxng.search(q)
