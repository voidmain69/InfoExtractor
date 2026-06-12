"""Extract ALL product specifications from a page as structured groups."""
from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag

from models import SpecEntry, SpecGroup

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "aside", "head", "noscript"})
_HEADING_TAGS = frozenset({"h2", "h3", "h4"})

# Class/id fragments that strongly suggest a spec container
_SPEC_CLASS_FRAGS = frozenset({
    "spec", "tech", "detail", "feature", "attr", "param",
    "datasheet", "characterist", "propert", "product-info",
})

# Class fragments for div/span elements that act as section headings
_HEADING_LIKE_FRAGS = frozenset({"title", "heading", "caption", "subtitle"})

_NOISE_RE = re.compile(
    r"^(related|also|similar|recommend|accessor|review|comment|"
    r"share|buy|price|cart|menu|navigation|newsletter|social|follow)",
    re.I,
)

_DEFAULT_GROUP = "Specifications"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _class_str(tag: Tag) -> str:
    return (" ".join(tag.get("class") or []) + " " + (tag.get("id") or "")).lower()


def _looks_spec(tag: Tag) -> bool:
    hints = _class_str(tag)
    return any(frag in hints for frag in _SPEC_CLASS_FRAGS)


def _looks_heading_div(tag: Tag) -> bool:
    hints = _class_str(tag)
    return any(frag in hints for frag in _HEADING_LIKE_FRAGS)


def _is_noise_heading(text: str) -> bool:
    return bool(_NOISE_RE.match(text.strip()))


# ── Group detection ──────────────────────────────────────────────────────────

def _nearest_heading(tag: Tag, max_levels: int = 6) -> str | None:
    """Walk up the DOM tree, checking previous siblings for headings."""
    node: Tag | None = tag
    for _ in range(max_levels):
        if node is None:
            break
        for sib in node.previous_siblings:
            if not isinstance(sib, Tag):
                continue
            if sib.name in _HEADING_TAGS:
                t = _norm(sib.get_text())
                if t and not _is_noise_heading(t) and len(t) < 100:
                    return t
            # Heading-like divs: div.spec-title, div.section-heading, etc.
            if sib.name in ("div", "span") and _looks_heading_div(sib):
                t = _norm(sib.get_text())
                if t and not _is_noise_heading(t) and 2 < len(t) < 60:
                    return t
            # Heading nested inside a sibling wrapper
            h = sib.find(list(_HEADING_TAGS))
            if isinstance(h, Tag):
                t = _norm(h.get_text())
                if t and not _is_noise_heading(t) and len(t) < 100:
                    return t
        node = node.parent
    return None


# ── Site-specific: ASUS rowTable__ ──────────────────────────────────────────

def _cls_match(tag: Tag, fragment: str) -> bool:
    return any(fragment in c.lower() for c in (tag.get("class") or []))


def _asus_groups(soup: BeautifulSoup) -> dict[str, list[SpecEntry]]:
    rows = soup.find_all(
        lambda t: isinstance(t, Tag) and _cls_match(t, "rowtable__")
    )
    if not rows:
        return {}

    groups: dict[str, list[SpecEntry]] = {}
    for row in rows:
        group = _nearest_heading(row) or _DEFAULT_GROUP
        title_el = row.find(lambda t: isinstance(t, Tag) and _cls_match(t, "rowtabletitle"))
        value_el = row.find(lambda t: isinstance(t, Tag) and _cls_match(t, "rowtableitemviewbox"))
        if not (title_el and value_el):
            continue
        label = _norm(title_el.get_text())
        value = _norm(value_el.get_text())
        if label and value:
            groups.setdefault(group, []).append(SpecEntry(name=label, value=value))
    return groups


# ── Generic table extraction ─────────────────────────────────────────────────

def _score_table(table: Tag, context: str) -> int:
    rows = table.find_all("tr")
    if len(rows) < 2:
        return 0
    two_col = sum(1 for r in rows if len(r.find_all(["td", "th"])) == 2)
    score = two_col * 2
    if _looks_spec(table):
        score += 5
    if any(kw in context.lower() for kw in ("spec", "tech", "detail", "характерис", "param")):
        score += 3
    max_cols = max((len(r.find_all(["td", "th"])) for r in rows), default=0)
    if max_cols > 4:
        score -= 10
    return score


def _table_groups(table: Tag, fallback: str) -> list[tuple[str, list[SpecEntry]]]:
    """Return [(group_name, entries)] from a table; handles sub-group <th> rows."""
    out: list[tuple[str, list[SpecEntry]]] = []
    current_name = fallback
    entries: list[SpecEntry] = []

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        if len(cells) == 1 and cells[0].name == "th":
            t = _norm(cells[0].get_text())
            if t:
                if entries:
                    out.append((current_name, entries))
                    entries = []
                current_name = t
            continue
        if len(cells) >= 2:
            label = _norm(cells[0].get_text())
            value = _norm(cells[1].get_text())
            if label and value and label != value and len(label) < 120:
                entries.append(SpecEntry(name=label, value=value))

    if entries:
        out.append((current_name, entries))
    return out


# ── Definition lists ─────────────────────────────────────────────────────────

def _dl_entries(dl: Tag) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    dt: str | None = None
    for child in dl.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "dt":
            dt = _norm(child.get_text())
        elif child.name == "dd" and dt:
            v = _norm(child.get_text())
            if v:
                entries.append(SpecEntry(name=dt, value=v))
            dt = None
    return entries


# ── UL/LI pair extraction (each li has exactly 2 child tags = label + value) ─

def _ul_li_pairs(ul: Tag) -> list[SpecEntry]:
    """Extract specs from <ul>/<ol> where each <li> has exactly 2 child tags."""
    entries: list[SpecEntry] = []
    for li in ul.find_all("li", recursive=False):
        kids = [k for k in li.children if isinstance(k, Tag)]
        if len(kids) == 2:
            label = _norm(kids[0].get_text())
            value = _norm(kids[1].get_text())
            if label and value and label != value and 2 < len(label) < 100 and 0 < len(value) < 500:
                entries.append(SpecEntry(name=label, value=value))
    return entries


# ── Generic div-pair extraction ──────────────────────────────────────────────

def _div_pairs(container: Tag) -> list[SpecEntry]:
    """Extract label-value pairs from direct children that each have exactly 2 child tags."""
    entries: list[SpecEntry] = []
    for child in container.find_all(True, recursive=False):
        if not isinstance(child, Tag):
            continue
        kids = [k for k in child.children if isinstance(k, Tag)]
        if len(kids) == 2:
            label = _norm(kids[0].get_text())
            value = _norm(kids[1].get_text())
            if (label and value and label != value
                    and 2 < len(label) < 100 and 0 < len(value) < 500):
                entries.append(SpecEntry(name=label, value=value))
    return entries


# ── Merge helper ─────────────────────────────────────────────────────────────

def _merge(acc: dict[str, list[SpecEntry]], group: str, entries: list[SpecEntry]) -> None:
    if not entries:
        return
    seen = {e.name for e in acc.get(group, [])}
    acc.setdefault(group, []).extend(e for e in entries if e.name not in seen)


# ── Main entry point ─────────────────────────────────────────────────────────

def extract_all_specs(html: str) -> list[SpecGroup]:
    """Parse HTML and return all discovered spec groups."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(list(_SKIP_TAGS)):
        tag.decompose()

    acc: dict[str, list[SpecEntry]] = {}

    # 1. ASUS-specific (very precise; if found at all, trust and return immediately)
    asus = _asus_groups(soup)
    if asus:
        for g, entries in asus.items():
            _merge(acc, g, entries)
        return _to_groups(acc)

    # 2. Linear DOM walk — updates group context from headings
    current_heading = _DEFAULT_GROUP

    for el in soup.find_all(True):
        if not isinstance(el, Tag):
            continue
        if el.name in _HEADING_TAGS:
            t = _norm(el.get_text())
            if t and not _is_noise_heading(t) and len(t) < 100:
                current_heading = t

        elif el.name == "table":
            score = _score_table(el, current_heading)
            if score >= 1:
                for gname, entries in _table_groups(el, current_heading):
                    _merge(acc, gname, entries)

        elif el.name == "dl":
            _merge(acc, current_heading, _dl_entries(el))

        elif el.name in ("ul", "ol"):
            entries = _ul_li_pairs(el)
            if len(entries) >= 3:
                group = _nearest_heading(el) or current_heading
                _merge(acc, group, entries)

        elif el.name in ("div", "section") and _looks_spec(el):
            entries = _div_pairs(el)
            if len(entries) >= 3:
                group = _nearest_heading(el) or current_heading
                _merge(acc, group, entries)

    return _to_groups(acc)


def _to_groups(acc: dict[str, list[SpecEntry]]) -> list[SpecGroup]:
    return [SpecGroup(name=g, specs=es) for g, es in acc.items() if len(es) >= 2]
