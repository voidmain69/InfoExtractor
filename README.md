# getAttrService

A self-hosted microservice that retrieves specific product specifications from the web. Given a product name and a target attribute (or a request for all specs), it searches via a local [SearxNG](https://searxng.github.io/searxng/) instance, fetches multiple pages in parallel, extracts the data through a multi-stage pipeline, and returns a structured JSON response with a confidence score.

All LLM operations use a locally deployed [Ollama](https://ollama.com/) instance — no external AI API calls are made.

The service is built to stay reliable against real-world web friction:

- **Anti-blocking fetcher** — rotating browser profiles where the User-Agent, `Sec-CH-UA` Client Hints and `Sec-Fetch-*`/`Referer` headers stay mutually consistent (so cross-checks don't flag the request), rotating `Accept-Language`, exponential-backoff retries on `429/503/403`, request jitter, optional proxy rotation, and a stealth-patched headless Chromium.
- **Anti-throttle search** — diversified SearxNG engine set, an in-process search-response cache, and an outbound rate limiter so a batch request can't burst the upstream engines into CAPTCHA.
- **High-quality extraction** — mojibake repair (UTF-8 served as cp1252), deterministic type coercion + unit conversion, a curated synonym/translation matcher, and verbose-blob trimming, so values come back clean and correctly typed without leaning on the LLM for the easy cases.

---

## How it works

```
GET /attribute?name=H610M-K&brand=ASUS&attribute=rear+USB+ports
         │
    TTL cache check
         │ MISS
    Ollama → 3 targeted SearxNG search queries
         │
    SearxNG ×3 in parallel
         │
    Deduplicate URLs → fetch top 5 pages (httpx, parallel)
         │
    Extraction pipeline per page:
      Stage 1: SearxNG infoboxes / answer boxes  (instant)
      Stage 2: JSON-LD schema.org ProductSpecs
      Stage 3: Site-specific CSS selectors (ASUS, GSMArena, TechPowerUp, …)
      Stage 4: Ollama LLM fallback (only when stages 1–3 confidence < 0.8)
         │
    Reconciler — weighted vote, fuzzy grouping, confidence score
         │
    Cache result → return AttributeResponse
```

For `/specs`, the same search flow is used but extraction collects **all** spec key-value pairs at once. If the initial httpx extraction yields few results, a headless Chromium (Playwright) instance renders the page and actively **reveals specs hidden behind JS** before re-extracting:

1. **Dismiss consent overlays** — cookie/GDPR banners (uk/ru/en, plus common id/class selectors) are closed first so they stop intercepting clicks.
2. **Scroll** the page in steps to trigger lazy-loaded spec sections.
3. **Open spec tabs/sections** — "Specifications" / "Технічні характеристики" / "Характеристики" tabs, buttons, or links (a spec *link* often leads to the full spec page).
4. **Expand collapsed content** — repeatedly clicks "Show more" / "Докладніше" / "Показати ще" / "Развернуть" buttons and force-opens every `<details>` / `aria-expanded` accordion, over several passes (expanding one block frequently unveils more), with link-clicks deliberately avoided here so we never navigate off the page.

For `POST /attributes` (batch typed resolution), the product's pages are fetched and parsed **once** into a shared spec pool, then every requested attribute is resolved against it:

```
POST /attributes  (product + list of typed attributes)
         │
    Per-attribute TTL cache check → resolve only the misses
         │
    Build shared pool once: rank → fetch top-N relevant pages → extract all specs
         │
    Match each attribute to a page label:
      a) curated synonym/translation table   (deterministic; e.g. Refresh rate = Update frequency)
      b) string-fuzzy match against the pool
      c) one LLM semantic call for the leftovers
      d) extraction pipeline / a cheap targeted search only if the pool lacks it
         │
    Normalize each value:
      a) deterministic coercion  (integer, number+unit conversion, boolean, enum snap)
      b) batched Ollama normalizer only for what coercion can't resolve
         │
    Cache found results → return ResolveResponse (value, confidence, source_url per attribute)
```

---

## Requirements

- Docker & Docker Compose
- Local [SearxNG](https://searxng.github.io/searxng/) instance (default port `8080`)
- Local [Ollama](https://ollama.com/) instance with a pulled model (e.g. `gemma3:4b`, `gemma4:e4b`)

SearxNG must have JSON output enabled in its `settings.yml`:
```yaml
search:
  formats:
    - html
    - json
```

For resilience against per-host throttling, configure a **diverse engine set** rather than relying on the CAPTCHA-prone defaults. On a datacenter IP, Google/DuckDuckGo/Startpage CAPTCHA quickly; Bing and Brave are reliable, with Mojeek/Qwant/Wikipedia as independents:
```yaml
outgoing:
  retries: 0          # don't re-hit a blocked engine; rely on diversity
engines:
  - { name: bing,       engine: bing,       shortcut: b,  timeout: 5.0 }
  - { name: brave,      engine: brave,      shortcut: br, timeout: 5.0 }
  - { name: mojeek,     engine: mojeek,     shortcut: mj, timeout: 5.0 }
  - { name: qwant,      engine: qwant,      shortcut: qw, timeout: 5.0 }
  - { name: duckduckgo, engine: duckduckgo, shortcut: d,  timeout: 5.0 }
  - { name: google,     engine: google,     shortcut: g,  timeout: 5.0 }
  - { name: wikipedia,  engine: wikipedia,  shortcut: wp, timeout: 4.0 }
```

---

## Quickstart

```bash
# 1. Clone and configure
git clone <repo-url>
cd getAttrService
cp .env.example .env   # then edit .env

# 2. Build and start
docker compose up --build -d

# 3. Verify
curl http://localhost:8000/health
```

> **Note:** After changing `.env`, always use `docker compose up -d --force-recreate` (not `restart`) to reload environment variables.

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://localhost:8080` | SearxNG base URL. Use `http://host.docker.internal:8080` when SearxNG runs on the Docker host. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama base URL. |
| `OLLAMA_MODEL` | `gemma3:4b` | Model name as it appears in Ollama (e.g. `gemma4:e4b`, `llama3:8b`). |
| `CACHE_TTL_SECONDS` | `3600` | How long a cached result is kept (seconds). |
| `CACHE_MAX_SIZE` | `2000` | Maximum number of cached entries (LRU). |
| `MAX_CONCURRENT_FETCHES` | `5` | Maximum parallel page fetches per request. |
| `PAGE_FETCH_TIMEOUT_SECONDS` | `8.0` | Per-page HTTP timeout. |
| `MAX_SOURCES` | `5` | Maximum pages fetched per `/attribute` request. |
| `QUERY_BUILDER_TIMEOUT_SECONDS` | `6.0` | Timeout for Ollama query generation. |
| `LLM_EXTRACTION_TIMEOUT_SECONDS` | `60.0` | Timeout for Ollama spec extraction per page. |
| `USE_PLAYWRIGHT` | `true` | Enable Playwright JS rendering for `/specs`. Set `false` to disable. |
| `PLAYWRIGHT_TIMEOUT_SECONDS` | `30.0` | Timeout for Playwright page load + clicks. |
| `PLAYWRIGHT_SCORE_THRESHOLD` | `25` | Trigger the JS render when the best static spec score is below this. Higher = lower barrier (Playwright runs more often). |
| `PLAYWRIGHT_MAX_URLS` | `2` | Render up to this many of the top URLs with Playwright (concurrently) and merge the specs each reveals. |
| `PLAYWRIGHT_MAX_CONCURRENCY` | `2` | Cap on concurrent headless Chromium instances (memory guard). |

**Security:**

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | _(empty)_ | Required value of the `X-API-Key` header. **Empty disables auth** — every endpoint is then open. `/health` and the docs are always exempt. |
| `RATE_LIMIT_REQUESTS` | `60` | Max requests per client IP per window. `0` disables the limit. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60.0` | Length of the rate-limit window (seconds). |
| `MAX_PAGE_BYTES` | `3000000` | Hard cap on a fetched page body (memory-exhaustion guard). |
| `SEARXNG_SAFESEARCH` | `1` | SearxNG content filter: `0`=off, `1`=moderate, `2`=strict. |

> **Hardening notes**
> - **SSRF:** every fetched/redirected URL (HTTP and Playwright) is resolved and rejected if it maps to a private, loopback, link-local, or otherwise non-public address. This blocks reaching internal services or cloud metadata via crafted search results or redirects.
> - **Container:** the image runs as a non-root user (`uid 10001`). Chromium still uses `--no-sandbox` (the in-container sandbox needs privileges we don't grant), so run the container with a seccomp profile and a read-only root filesystem in production.
> - **Auth:** set `API_KEY` before exposing the service anywhere beyond localhost. The `/search` endpoint is a thin proxy to SearxNG and is covered by the same auth.
> - **Dependencies:** scan with `pip-audit -r requirements.txt` (or Dependabot) periodically — versions are pinned but drift behind security patches over time.

**Concurrency & batch resolution:**

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_MAX_CONCURRENCY` | `4` | Global cap on concurrent Ollama calls (protects the single model server). `0` disables the limit. |
| `RESOLVE_MAX_CONCURRENCY` | `4` | Max attributes resolved in parallel within one `POST /attributes` request. |
| `RESOLVE_TARGETED_FALLBACK` | `true` | Allow a cheap per-attribute web search when the shared pool lacks the attribute. |
| `RESOLVE_MATCH_THRESHOLD` | `0.78` | Fuzzy threshold for matching an attribute name to a spec-pool label. |
| `RESOLVE_POOL_MAX_PAGES` | `3` | Cap the shared spec pool to the top-N most relevant pages (drops vendor-list/download noise). |
| `RESOLVE_POOL_RICH_THRESHOLD` | `40` | If the pool has at least this many specs, skip the per-attribute web fallback (avoids self-throttling). |
| `NORMALIZE_TIMEOUT_SECONDS` | `30.0` | Timeout for the batched Ollama normalization / semantic-match calls. |

**Anti-blocking (page fetcher):**

| Variable | Default | Description |
|---|---|---|
| `USER_AGENT` | Chrome UA | Base User-Agent (the fetcher also rotates through a built-in set of real browser UAs). |
| `FETCH_RETRY_ATTEMPTS` | `3` | Max retries on `429/503/502/403` (respects `Retry-After`). |
| `FETCH_RETRY_BACKOFF` | `1.0` | Base backoff seconds; doubles per retry. |
| `FETCH_JITTER_MAX` | `0.4` | Max random delay (seconds) before each request, to avoid bursts. |
| `PROXY_LIST` | `""` | Comma-separated proxy URLs (`http://user:pass@host:port,…`); one is picked per request/launch. Empty = direct. |

**Anti-throttle (SearxNG client):**

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_CACHE_TTL_SECONDS` | `1800` | TTL for cached search responses (deduplicates overlapping queries). |
| `SEARXNG_CACHE_MAX_SIZE` | `1000` | Max cached search responses. |
| `SEARXNG_MIN_INTERVAL_SECONDS` | `0.34` | Minimum spacing between outgoing SearxNG queries. |
| `SEARXNG_MAX_CONCURRENCY` | `2` | Max parallel SearxNG queries. |
| `SEARXNG_RETRY_ATTEMPTS` | `2` | Retries when a search returns empty (engines transiently blocked). |
| `SEARXNG_RETRY_BACKOFF` | `0.8` | Base backoff seconds between search retries. |

---

## API

### `GET /health`

Liveness check.

```json
{"status": "ok"}
```

---

### `GET /attribute` — Extract a single attribute

Find the value of one specific attribute for a product.

#### Query parameters

**Product identity** (at least `name` is required):

| Parameter | Required | Description |
|---|---|---|
| `name` | **Yes** | Product model name. E.g. `H610M-K`, `G3424B`. |
| `brand` | No | Manufacturer / brand. E.g. `ASUS`, `2E GAMING`. Improves search precision and official-site detection. |
| `category` | No | Product category. E.g. `motherboard`, `monitor`. Helps the query builder. |
| `article` | No | Article number / SKU. E.g. `2E-G3424B-01.UA`. Used for exact-match search. |
| `ean13` | No | EAN-13 barcode. Highest-priority identifier when provided. |
| `upc` | No | UPC barcode. |

> **Note:** `mpn` (manufacturer part number) is supported in `POST /attributes` request body but is **not** a query parameter for GET endpoints.

**Request options:**

| Parameter | Default | Description |
|---|---|---|
| `attribute` | **required** | The specification to look up. E.g. `rear USB ports`, `screen diagonal`, `TDP`. |
| `max_sources` | `5` | How many pages to fetch and analyse (1–10). |
| `official_only` | `false` | Restrict results to the manufacturer's official website. The service first resolves the official domain via Ollama + SearxNG, then filters search results to that domain. Falls back to normal search if no official results are found (`official_only_fallback: true` in the response). |

#### Example

```bash
curl "http://localhost:8000/attribute?name=H610M-K&brand=ASUS&category=motherboard&attribute=rear+USB+ports"
```

#### Response

```json
{
  "product": {
    "name": "H610M-K",
    "category": "motherboard",
    "brand": "ASUS",
    "article": null,
    "mpn": null,
    "ean13": null,
    "upc": null
  },
  "attribute": "rear USB ports",
  "value": "2 x USB 3.2 Gen 1 (5G), 4 x USB 2.0",
  "unit": null,
  "confidence": 0.92,
  "sources": [
    {
      "url": "https://www.asus.com/motherboards-components/motherboards/prime/prime-h610m-k/techspec/",
      "title": "PRIME H610M-K - Tech Specs | ASUS",
      "extraction_method": "css_selector",
      "confidence": 1.0,
      "raw_value": "2 x USB 3.2 Gen 1 (5G) ports (Type-A), 4 x USB 2.0 ports (Type-A)",
      "unit": null
    }
  ],
  "search_queries_used": [
    "ASUS H610M-K rear USB ports specifications",
    "H610M-K I/O panel ports listing",
    "ASUS H610M-K motherboard manual rear panel"
  ],
  "official_domain": null,
  "official_only_fallback": false,
  "cached": false
}
```

| Field | Description |
|---|---|
| `value` | Extracted specification value. `null` if not found. |
| `unit` | Unit of measurement if separately identified. |
| `confidence` | Score 0–1. Above 0.8 is reliable; 0.5–0.8 is a best-effort LLM result. |
| `sources` | Each page that contributed a candidate value. `extraction_method` is one of `infobox`, `jsonld`, `css_selector`, `llm`. |
| `official_domain` | Resolved manufacturer domain when `official_only=true`. |
| `official_only_fallback` | `true` when the official domain was found but had no matching search results, so normal search was used instead. |
| `cached` | `true` if the result was served from cache. |

---

### `GET /specs` — Extract all specifications

Retrieve all product specifications as structured groups.

#### Query parameters

Same product identity parameters as `/attribute` — `name` (required), `brand`, `category`, `article`, `ean13`, `upc` — plus:

| Parameter | Default | Description |
|---|---|---|
| `official_only` | `false` | Same behaviour as in `/attribute`. |

No `attribute` parameter — the service discovers all specs automatically.

#### Example

```bash
curl "http://localhost:8000/specs?name=G3424B&brand=2E+GAMING&article=2E-G3424B-01.UA"
```

#### Response

```json
{
  "product": {
    "name": "G3424B",
    "category": null,
    "brand": "2E GAMING",
    "article": "2E-G3424B-01.UA",
    "mpn": null,
    "ean13": null,
    "upc": null
  },
  "groups": [
    {
      "name": "Main",
      "specs": [
        {"name": "Device type",     "value": "monitor"},
        {"name": "Screen diagonal", "value": "34 \""},
        {"name": "Type of matrix",  "value": "VA"},
        {"name": "Curved screen",   "value": "CURVED R1500"}
      ]
    },
    {
      "name": "Specifications",
      "specs": [
        {"name": "Recommended resolution", "value": "3440×1440"},
        {"name": "Contrast (static)",      "value": "4000:1"},
        {"name": "Reaction time",          "value": "1 ms"},
        {"name": "Update frequency",       "value": "180 Hz"}
      ]
    }
  ],
  "source_url": "https://2egaming.com/en/product/2e-gaming-monitor-g3424b/",
  "total_specs": 25,
  "cached": false
}
```

| Field | Description |
|---|---|
| `groups` | Ordered list of spec groups. Group names come from the page's section headings (`<h2>`, `<h3>`, `div.spec-title`, etc.). If the page has no grouping, all specs are placed in a single `"Specifications"` group. |
| `groups[].name` | Section heading as it appears on the source page. |
| `groups[].specs` | List of `{name, value}` pairs in page order. |
| `source_url` | The single page that produced the best (most specs) result. |
| `total_specs` | Total number of individual spec entries across all groups. |

---

### `POST /attributes` — Batch typed resolution

Resolve **many typed attributes for one product** in a single request. The product's
source pages are fetched and parsed **once**, every attribute is resolved against that
shared content, and an AI layer then coerces each value to the requested type, converts
units, and snaps to the allowed-value list.

Prefer this over many concurrent `GET /attribute` calls: attributes of one product
overwhelmingly come from the same pages, so batching avoids re-searching/re-fetching and
sharply reduces load on the single Ollama/SearxNG backend. A 1-element array is a valid
"single attribute" request.

#### Request body

```jsonc
{
  "product": {                       // same fields as GET /attribute, plus mpn
    "name": "G3424B", "brand": "2E GAMING", "category": "monitor",
    "article": "2E-G3424B-01.UA", "mpn": null, "ean13": null, "upc": null
  },
  "attributes": [
    { "name": "Refresh rate", "type": "number", "unit": "Hz",
      "allowed_values": ["60","120","144","165","180"] },
    { "name": "Panel type",   "type": "enum", "allowed_values": ["IPS","VA","TN","OLED"] },
    { "name": "Curvature",    "type": "string" }
  ],
  "official_only": false,
  "max_sources": 5
}
```

| Field | Description |
|---|---|
| `attributes[].name` | Attribute / characteristic to resolve. |
| `attributes[].type` | `string` \| `number` \| `integer` \| `boolean` \| `enum`. Drives coercion. |
| `attributes[].unit` | Optional desired output unit. The AI layer converts if the source uses another unit. |
| `attributes[].allowed_values` | Optional candidate set. The AI layer snaps the value to the closest match or reports none. |
| `official_only` | Restrict to the manufacturer's official site (same behaviour as elsewhere). |
| `max_sources` | Pages to fetch for the shared pool (1–10). |

#### Response

```jsonc
{
  "product": { ... },
  "results": [
    {
      "name": "Refresh rate", "type": "number",
      "value": "180", "unit": "Hz", "raw_value": "180 Hz",
      "matched_allowed": true,
      "confidence": 0.81,                                  // always present
      "source_url": "https://2egaming.com/.../g3424b/",    // page the value came from
      "status": "found",
      "sources": [ { "url": "...", "extraction_method": "css_selector", "confidence": 0.86 } ]
    }
  ],
  "cached": false
}
```

| Field | Description |
|---|---|
| `value` | Normalized/coerced/unit-converted result (or the snapped allowed value). |
| `raw_value` | The text extracted from the page before normalization. |
| `unit` | Unit of `value` (target unit if converted). |
| `matched_allowed` | When `allowed_values` was given: `true` if `value` is one of them, else `false`; `null` otherwise. |
| `confidence` | Extraction confidence × normalization certainty. **Always returned.** |
| `source_url` | URL of the page the value was extracted from. **Always returned for found values.** |
| `status` | `found` \| `not_found` \| `ambiguous` (had data but no allowed value matched). |
| `sources` | Full provenance (every corroborating source for this value). |

Per-attribute results are cached, so re-running a batch (or overlapping batches) returns
cached attributes instantly and only resolves the misses.

---

### `GET /search` — Debug proxy

Passes a raw query directly to SearxNG and returns the JSON response. Useful for debugging search results.

```bash
curl "http://localhost:8000/search?q=ASUS+H610M-K+specifications"
```

---

## Extraction pipeline detail

| Stage | Method | Typical confidence | When used |
|---|---|---|---|
| 1 | SearxNG infoboxes & answer boxes | 0.70 – 0.95 | Always |
| 2 | JSON-LD `schema.org/Product` | 0.75 – 0.90 | Always |
| 3 | Site-specific CSS selectors | 0.75 – 1.00 | Always |
| 4 | Ollama LLM (focused text window) | 0.50 – 0.85 | Only when no stage 1–3 result has confidence ≥ 0.8 |

Results from all stages are reconciled using weighted voting with fuzzy value grouping (`difflib.SequenceMatcher`, `autojunk=False`). Method weights: `infobox=1.0`, `css_selector=1.0`, `jsonld=0.9`, `llm=0.7`.

The LLM is given a focused ±2500-character text window around the attribute keyword rather than the full page text, which significantly reduces extraction time and hallucination risk.

---

## Value quality & normalization

Applied across `/specs` and `POST /attributes` so values come back clean and correctly typed, with the LLM reserved for genuinely ambiguous cases:

| Step | Module | What it does |
|---|---|---|
| **Mojibake repair** | `extraction/text_repair.py` | Fixes UTF-8 served as cp1252 (`IntelÂ®` → `Intel®`, `34â³` → `34″`, broken emoji) via `ftfy` + a latin-1↔utf-8 fallback, then NFC-normalizes. Applied to page text, spec-pool labels/values, and final values. |
| **Synonym / translation match** | `services/synonyms.py` | A curated table resolves well-known label equivalences deterministically (`Refresh rate` = `Update frequency`, `Response time` = `Reaction time`, `Panel type` = `Type of matrix`, incl. UA/RU), before any LLM semantic call. |
| **Deterministic coercion** | `extraction/coerce.py` | Parses the requested type locally: integer (`2 x DIMM slots` → `2`), number + unit conversion (`Max. 96GB` → `96 GB`; `MHz`↔`GHz`, `inch`↔`mm`, …), boolean, and enum snapping (`VA matrix` → `VA`, `180` → allowed list). |
| **Blob trimming** | `extraction/value_cleaner.py` | Trims glued dimension tails and attribute-label echoes (`micro-ATX Form Factor9.2 inch x 8.0 inch` → `micro-ATX`), strips leading emoji/bullets, and derives the unit from the final value so a trimmed tail can't leak its unit. |
| **LLM normalizer** | `services/value_normalizer.py` | One batched Ollama call, used only for the items deterministic coercion can't confidently resolve (fuzzy enum snapping, unusual unit conversions). |

---

## Project structure

The codebase follows a layered architecture with a strict dependency direction: `api → services → infrastructure → domain`. The `domain` layer is pure data (no I/O); `core` holds cross-cutting config/logging.

```
getAttrService/
├── main.py                          # Composition root: create_app() + lifespan wiring
└── app/
    ├── core/
    │   ├── config.py                # Pydantic-settings singleton (reads .env)
    │   └── logging.py               # Logging setup
    ├── domain/                      # Pure Pydantic models, zero I/O
    │   ├── product.py               # ProductQuery
    │   ├── attributes.py            # AttrType, AttributeSpec, ResolvedAttribute, ResolveRequest
    │   ├── extraction.py            # ExtractionMethod, SourceResult, ExtractionCandidate
    │   ├── specs.py                 # SpecEntry, SpecGroup
    │   ├── page.py                  # FetchedPage, SearxNGResult, SearxNGResponse
    │   └── responses.py             # AttributeResponse, SpecsResponse, ResolveResponse
    ├── api/
    │   ├── deps.py                  # FastAPI Depends providers (product query, services)
    │   ├── middleware.py            # APIKeyMiddleware (X-API-Key) + RateLimitMiddleware
    │   └── routes/
    │       ├── attribute.py         # GET /attribute
    │       ├── specs.py             # GET /specs
    │       ├── resolve.py           # POST /attributes
    │       └── system.py            # GET /health, GET /search
    ├── services/                    # Application orchestration
    │   ├── attribute_service.py     # Single-attribute workflow
    │   ├── specs_service.py         # All-specs workflow (+ Playwright fallback)
    │   ├── resolve_service.py       # POST /attributes batch orchestrator
    │   ├── attribute_matcher.py     # Match attribute names to the spec pool (fuzzy)
    │   ├── semantic_matcher.py      # LLM synonym/translation matcher
    │   ├── synonyms.py              # Curated synonym/translation table (deterministic)
    │   ├── value_normalizer.py      # Batched Ollama value normalizer
    │   ├── product_match.py         # Page-vs-product relevance scoring / filtering
    │   ├── source_ranking.py        # Rank candidate source URLs before fetching
    │   ├── official_site.py         # Manufacturer-domain resolution
    │   └── url_filter.py            # Domain-match helper
    ├── infrastructure/              # Adapters to external systems
    │   ├── llm/ollama.py            # Single Ollama gateway (+ global concurrency limit)
    │   ├── search/searxng.py        # SearxNG client (+ response cache + rate limiter)
    │   ├── fetch/http_fetcher.py    # Parallel httpx fetcher (UA rotation, retries, proxy)
    │   ├── fetch/browser_fetcher.py # Playwright Chromium fetcher (stealth, proxy)
    │   ├── cache/ttl_cache.py       # Thread-safe TTLCache wrapper
    │   └── query/query_builder.py   # Ollama → targeted search queries
    └── extraction/                  # Extraction pipeline (domain logic)
        ├── pipeline.py              # 4-stage orchestrator
        ├── reconciler.py            # Weighted vote + fuzzy grouping
        ├── base.py                  # BaseExtractor ABC
        ├── all_specs.py             # /specs full-page spec extraction + merge
        ├── coerce.py                # Deterministic type coercion + unit conversion
        ├── value_cleaner.py         # Blob trimming, leading-symbol strip, unit split
        ├── text_repair.py           # Mojibake repair (ftfy + fallback) + NFC
        └── extractors/
            ├── infobox.py           # Stage 1: SearxNG infoboxes
            ├── jsonld.py            # Stage 2: JSON-LD schema.org
            ├── css_selectors.py     # Stage 3: site-specific + generic tables
            └── llm.py               # Stage 4: Ollama fallback
├── Dockerfile / docker-compose.yml / requirements.txt
└── .env / .env.example
```

All Ollama interaction goes through the single `OllamaGateway` (`app/infrastructure/llm/ollama.py`); the shared `httpx.AsyncClient` for Ollama + SearxNG is created once in `main.py`'s lifespan and closed on shutdown.
