import difflib
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.domain.extraction import ExtractionCandidate, ExtractionMethod, SourceResult
from app.domain.page import FetchedPage
from app.extraction.base import BaseExtractor

_WS = re.compile(r"\s+")

# (label_sel, value_sel) — flat zip-based pairing. Only for structures where
# label/value counts are guaranteed 1:1 within a scoped container (e.g. <dl>).
SITE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "techpowerup.com": [
        ("section.techpowerup-specs dl dt", "section.techpowerup-specs dl dd"),
    ],
}

# (row_container_sel, label_within_row_sel, value_within_row_sel) — label and
# value are resolved *within the same row*, so missing/rowspan cells can't shift
# the pairing the way a global zip() does.
SITE_ROW_PATTERNS: dict[str, list[tuple[str, str, str]]] = {
    "asus.com": [
        ("[class*=rowTable__]", "[class*=rowTableTitle]", "[class*=rowTableItemViewBox]"),
    ],
    "gsmarena.com": [
        ("table.specs-list tr", "td.ttl", "td.nfo"),
    ],
    "notebookcheck.net": [
        ("table.datasheet tr", "th", "td"),
    ],
    "anandtech.com": [
        ("table.specification tr", "td:first-child", "td:last-child"),
    ],
    "rtings.com": [
        ("table.test-results tr", "td:first-child", "td:last-child"),
    ],
    "techpowerup.com": [
        (".gpuz-specs table tr", "td:first-child", "td:last-child"),
    ],
}


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    ratio = difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()
    # Boost: if the shorter string is fully contained in the longer one
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short and short in long_:
        ratio = max(ratio, 0.80)
    return ratio


def _make_candidate(value: str, conf: float, page: FetchedPage) -> ExtractionCandidate:
    return ExtractionCandidate(
        value=value,
        confidence=conf,
        source=SourceResult(
            url=page.url,
            title=page.title,
            extraction_method=ExtractionMethod.CSS_SELECTOR,
            confidence=conf,
            raw_value=value,
        ),
    )


class CSSExtractor(BaseExtractor):
    def extract(
        self,
        product: str,
        attribute: str,
        source: FetchedPage,
    ) -> list[ExtractionCandidate]:
        soup = BeautifulSoup(source.html, "lxml")
        hostname = urlparse(source.url).hostname or ""

        for site_key, row_patterns in SITE_ROW_PATTERNS.items():
            if hostname.endswith(site_key):
                for row_pattern in row_patterns:
                    candidates = self._apply_row_pattern(soup, attribute, row_pattern, source)
                    if candidates:
                        return candidates

        for site_key, patterns in SITE_PATTERNS.items():
            if hostname.endswith(site_key):
                candidates = self._apply_patterns(soup, attribute, patterns, source)
                if candidates:
                    return candidates

        return self._generic_tables(soup, attribute, source)

    def _apply_row_pattern(
        self,
        soup: BeautifulSoup,
        attribute: str,
        pattern: tuple[str, str, str],
        page: FetchedPage,
    ) -> list[ExtractionCandidate]:
        row_sel, label_sel, value_sel = pattern
        candidates = []
        for row in soup.select(row_sel):
            label_el = row.select_one(label_sel)
            if not label_el:
                continue
            label_text = label_el.get_text(separator=" ", strip=True)
            sim = _similarity(label_text, attribute)
            if sim >= 0.75:
                value_el = row.select_one(value_sel)
                if not value_el:
                    continue
                val = value_el.get_text(separator=" ", strip=True)
                conf = 1.0 if sim >= 0.95 else (0.85 if sim >= 0.8 else 0.75)
                candidates.append(_make_candidate(val, conf, page))
        return candidates

    def _apply_patterns(
        self,
        soup: BeautifulSoup,
        attribute: str,
        patterns: list[tuple[str, str]],
        page: FetchedPage,
    ) -> list[ExtractionCandidate]:
        candidates = []
        for label_sel, value_sel in patterns:
            labels = soup.select(label_sel)
            values = soup.select(value_sel)
            for label_el, value_el in zip(labels, values):
                label_text = label_el.get_text(separator=" ", strip=True)
                sim = _similarity(label_text, attribute)
                if sim >= 0.75:
                    val = value_el.get_text(separator=" ", strip=True)
                    conf = 1.0 if sim >= 0.95 else (0.85 if sim >= 0.8 else 0.75)
                    candidates.append(_make_candidate(val, conf, page))
        return candidates

    def _generic_tables(
        self,
        soup: BeautifulSoup,
        attribute: str,
        page: FetchedPage,
    ) -> list[ExtractionCandidate]:
        candidates = []

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                label_text = cells[0].get_text(separator=" ", strip=True)
                sim = _similarity(label_text, attribute)
                if sim >= 0.75:
                    val = cells[1].get_text(separator=" ", strip=True)
                    conf = 1.0 if sim >= 0.95 else (0.85 if sim >= 0.8 else 0.75)
                    candidates.append(_make_candidate(val, conf, page))

        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                label_text = dt.get_text(separator=" ", strip=True)
                sim = _similarity(label_text, attribute)
                if sim >= 0.75:
                    val = dd.get_text(separator=" ", strip=True)
                    conf = 1.0 if sim >= 0.95 else (0.85 if sim >= 0.8 else 0.75)
                    candidates.append(_make_candidate(val, conf, page))

        return candidates
