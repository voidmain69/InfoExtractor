import httpx

from config import settings
from models import SearxNGResponse, SearxNGResult


async def search(query: str, num_results: int = 10) -> SearxNGResponse:
    params = {
        "q": query,
        "format": "json",
        "language": "en",
        "safesearch": "0",
        "pageno": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.searxng_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return SearxNGResponse()

    raw_results = data.get("results", []) or []
    results = []
    for r in raw_results[:num_results]:
        url = r.get("url", "")
        if not url:
            continue
        results.append(
            SearxNGResult(
                url=url,
                title=r.get("title", ""),
                content=r.get("content"),
                score=r.get("score"),
            )
        )

    raw_answers = data.get("answers", []) or []
    answers = list(dict.fromkeys(str(a) for a in raw_answers if a))

    infoboxes = data.get("infoboxes", []) or []

    return SearxNGResponse(results=results, infoboxes=infoboxes, answers=answers)
