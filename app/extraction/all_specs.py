"""Extract ALL product specifications from a page as structured groups.

Coverage strategy — manufacturer sites vary wildly, so several independent
extractors run over the same DOM and their results merge:

1. Site-specific (ASUS rowTable__) — precise, trusted when present.
2. JSON-LD schema.org Product.additionalProperty — machine-readable, common on
   retail and many manufacturer pages.
3. Embedded JSON state (__NEXT_DATA__ / application/json scripts) — modern
   React/Next/Nuxt sites render specs client-side; the data is in the HTML as
   JSON even when the static DOM carries only a teaser.
4. Generic DOM walk: spec-scored <table>, <dl>, label/value <li>/<div> pairs
   (positional 2-child rows and class-hinted label/value rows), and
   "Label: value" text lines inside spec-looking containers.
"""
from __future__ import annotations

import json
import re
from bs4 import BeautifulSoup, Tag

from app.domain.specs import SpecEntry, SpecGroup
from app.extraction.text_repair import fix_text

_SKIP_TAGS = frozenset({"script", "style", "nav", "footer", "header", "aside", "head", "noscript"})
_HEADING_TAGS = frozenset({"h2", "h3", "h4"})

# Class/id fragments that strongly suggest a spec container
_SPEC_CLASS_FRAGS = frozenset({
    "spec", "tech", "detail", "feature", "attr", "param",
    "datasheet", "characterist", "propert", "product-info",
    "harakteristik", "options", "product-params", "sku-props",
})

# Class fragments for div/span elements that act as section headings
_HEADING_LIKE_FRAGS = frozenset({"title", "heading", "caption", "subtitle"})

# Class fragments marking the label / value halves of a spec row. Only used
# inside spec-looking containers to keep false positives down.
_LABEL_CLASS_FRAGS = ("label", "name", "term", "key", "ttl", "caption")
_VALUE_CLASS_FRAGS = ("value", "val", "data", "nfo", "desc")

_NOISE_RE = re.compile(
    r"^(related|also|similar|recommend|accessor|review|comment|"
    r"share|buy|price|cart|menu|navigation|newsletter|social|follow)",
    re.I,
)

_DEFAULT_GROUP = "Specifications"

# "Label: value" line, e.g. "Потужність: 2100 Вт" / "Print speed: 30 ppm".
_COLON_PAIR_RE = re.compile(r"^\s*([^:\n]{2,70})\s*[::]\s*(\S.{0,300})$")

_MAX_LABEL_LEN = 100
# Generous: motherboard/printer rows legitimately run long (a memory row lists
# every supported O.C. frequency); the value cleaner trims downstream.
_MAX_VALUE_LEN = 1500


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", fix_text(s)).strip()


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


_NUM_TOKEN_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def _facet_like(value: str) -> bool:
    """True for catalog filter-facet blobs ('10 кг 10.5 кг 11 кг … 9 кг'):
    many numbers with at most a couple of distinct words. A real spec value
    with that many numbers (a memory-frequency list, a ports row) always
    carries a varied vocabulary around them."""
    tokens = value.split()
    if len(tokens) < 8:
        return False
    nums = sum(1 for t in tokens if _NUM_TOKEN_RE.match(t))
    alpha = {t.lower().strip(".,;:()") for t in tokens if not _NUM_TOKEN_RE.match(t)}
    return nums >= 5 and len(alpha) <= 2


def _valid_pair(label: str, value: str) -> bool:
    return bool(
        label and value and label != value
        and 1 < len(label) < _MAX_LABEL_LEN and 0 < len(value) < _MAX_VALUE_LEN
        and not _facet_like(value)
    )


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


# ── JSON-LD schema.org Product specs ─────────────────────────────────────────

def _jsonld_value(value) -> str | None:
    """Render a JSON-LD property value (scalar or QuantitativeValue) as text."""
    if isinstance(value, (str, int, float)):
        s = str(value).strip()
        return s or None
    if isinstance(value, dict):
        val = value.get("value")
        if val is None:
            return None
        s = str(val).strip()
        if not s:
            return None
        # unitCode is UN/CEFACT (e.g. KGM) — not human-readable; only unitText.
        unit_text = str(value.get("unitText") or "").strip()
        return f"{s} {unit_text}".strip()
    return None


def _jsonld_product_entries(node: dict) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    props = node.get("additionalProperty")
    if isinstance(props, dict):
        props = [props]
    for prop in props or []:
        if not isinstance(prop, dict):
            continue
        name = _norm(str(prop.get("name") or prop.get("propertyID") or ""))
        value = _jsonld_value(prop.get("value") if prop.get("value") is not None else prop.get("unitText"))
        if name and value and _valid_pair(name, _norm(value)):
            entries.append(SpecEntry(name=name, value=_norm(value)))
    # A few direct Product properties are specs in their own right.
    for key, label in (("weight", "Weight"), ("width", "Width"),
                       ("height", "Height"), ("depth", "Depth"),
                       ("color", "Color"), ("material", "Material")):
        v = _jsonld_value(node.get(key))
        if v:
            entries.append(SpecEntry(name=label, value=_norm(v)))
    return entries


def _walk_jsonld(node) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    if isinstance(node, list):
        for item in node:
            entries.extend(_walk_jsonld(item))
        return entries
    if not isinstance(node, dict):
        return entries
    node_type = node.get("@type", "")
    if isinstance(node_type, list):
        node_type = " ".join(str(t) for t in node_type)
    if "Product" in str(node_type):
        entries.extend(_jsonld_product_entries(node))
    for val in node.values():
        if isinstance(val, (dict, list)):
            entries.extend(_walk_jsonld(val))
    return entries


def _jsonld_groups(soup: BeautifulSoup) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        entries.extend(_walk_jsonld(data))
    return entries


# ── Embedded JSON state (__NEXT_DATA__, application/json blobs) ─────────────

_NAME_KEYS = ("name", "label", "title", "key", "displayName", "attributeName", "featureName")
_VALUE_KEYS = ("value", "val", "displayValue", "attributeValue", "featureValue", "text")

_MAX_JSON_BYTES = 2_000_000
_MAX_JSON_DEPTH = 30

# Technical identifiers leaking from app state: camelCase/snake_case single
# tokens (dateAdded, product_id) are code keys, not human spec labels.
_CODE_NAME_RE = re.compile(r"^[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*$|^[a-z0-9]+(_[a-z0-9]+)+$")
_EPOCH_VALUE_RE = re.compile(r"^\d{10,}$")


def _state_pair_ok(name: str, value: str) -> bool:
    if _CODE_NAME_RE.match(name):
        return False
    if _EPOCH_VALUE_RE.match(value):
        return False
    low = name.lower()
    if low in ("id", "uid", "url", "slug", "sku", "ean", "type", "key", "code"):
        return False
    return True


def _spec_dict_pair(d: dict) -> tuple[str, str] | None:
    """If the dict looks like one spec entry, return (name, value)."""
    name = None
    for k in _NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            name = v.strip()
            break
    if not name:
        return None
    for k in _VALUE_KEYS:
        v = d.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            value = str(v).strip()
            if _valid_pair(_norm(name), _norm(value)):
                return _norm(name), _norm(value)
        # {"value": {"text"/"value": ...}} one level of nesting is common
        if isinstance(v, dict):
            for kk in _VALUE_KEYS:
                vv = v.get(kk)
                if isinstance(vv, (str, int, float)) and str(vv).strip():
                    value = str(vv).strip()
                    if _valid_pair(_norm(name), _norm(value)):
                        return _norm(name), _norm(value)
    return None


def _walk_state(node, out: list[SpecEntry], depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        return
    if isinstance(node, list):
        # An array of ≥3 spec-shaped dicts is treated as a spec list.
        pairs = []
        for item in node:
            if isinstance(item, dict):
                p = _spec_dict_pair(item)
                if p and _state_pair_ok(*p):
                    pairs.append(p)
        if len(pairs) >= 3:
            out.extend(SpecEntry(name=n, value=v) for n, v in pairs)
        for item in node:
            if isinstance(item, (dict, list)):
                _walk_state(item, out, depth + 1)
        return
    if isinstance(node, dict):
        for val in node.values():
            if isinstance(val, (dict, list)):
                _walk_state(val, out, depth + 1)


def _embedded_state_entries(soup: BeautifulSoup) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    for tag in soup.find_all("script"):
        stype = (tag.get("type") or "").lower()
        sid = (tag.get("id") or "").lower()
        if stype != "application/json" and "next_data" not in sid and "nuxt" not in sid:
            continue
        raw = tag.string or ""
        if not raw or len(raw) > _MAX_JSON_BYTES:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        _walk_state(data, entries)
    return entries


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
        # Single full-width <th> = sub-group separator
        if len(cells) == 1 and cells[0].name == "th":
            t = _norm(cells[0].get_text())
            if t:
                if entries:
                    out.append((current_name, entries))
                    entries = []
                current_name = t
            continue
        if len(cells) == 2:
            label = _norm(cells[0].get_text())
            value = _norm(cells[1].get_text())
            if (label and value and label != value and len(label) < 120
                    and not _facet_like(value)):
                entries.append(SpecEntry(name=label, value=value))
        elif len(cells) >= 3:
            # 3-column spec rows: either (category | qualifier | value) as on
            # support.brother.com, or (name | value | note). Pick the cell that
            # actually carries the figure.
            c0 = _norm(cells[0].get_text())
            c1 = _norm(cells[1].get_text())
            c2 = _norm(cells[2].get_text())
            if not (c0 and c1 and c2) or len(c0) >= 120:
                continue
            c1_digit = any(ch.isdigit() for ch in c1)
            c2_digit = any(ch.isdigit() for ch in c2)
            value = c2 if (c2_digit or not c1_digit) else c1
            if _facet_like(value):
                continue
            if value is c2:
                entries.append(SpecEntry(name=f"{c0} ({c1})", value=c2))
                entries.append(SpecEntry(name=c0, value=c2))
            else:
                entries.append(SpecEntry(name=c0, value=c1))

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


# ── UL/LI pair extraction ────────────────────────────────────────────────────

def _li_pair(li: Tag) -> SpecEntry | None:
    """Extract one spec from an <li>: 2-child positional, class-hinted, or colon text."""
    kids = [k for k in li.children if isinstance(k, Tag)]
    if len(kids) == 2:
        label = _norm(kids[0].get_text())
        value = _norm(kids[1].get_text())
        if _valid_pair(label, value):
            return SpecEntry(name=label, value=value)
    entry = _class_hinted_pair(li)
    if entry:
        return entry
    # "Label: value" plain-text line
    if not li.find(["ul", "ol", "table"]):
        m = _COLON_PAIR_RE.match(_norm(li.get_text()))
        if m:
            label, value = m.group(1).strip(), m.group(2).strip()
            if _valid_pair(label, value):
                return SpecEntry(name=label, value=value)
    return None


def _ul_li_pairs(ul: Tag) -> list[SpecEntry]:
    entries: list[SpecEntry] = []
    for li in ul.find_all("li", recursive=False):
        entry = _li_pair(li)
        if entry:
            entries.append(entry)
    return entries


# ── Class-hinted label/value rows ────────────────────────────────────────────

def _by_class(row: Tag, frags: tuple[str, ...], limit: int = 3) -> list[Tag]:
    """Top-most elements under `row` whose class matches; nested matches inside
    an already-matched element count as the same hit, not a second one."""
    out: list[Tag] = []
    for el in row.find_all(True):
        cls = _class_str(el)
        if any(f in cls for f in frags):
            if any(el is p or p in el.parents for p in out):
                continue
            out.append(el)
            if len(out) >= limit:
                break
    return out


def _class_hinted_pair(row: Tag) -> SpecEntry | None:
    """Pair a row's label/value by class hints (e.g. .spec-name / .spec-value).

    Requires exactly one label element and one value element: with several of
    either, `row` is a whole section and blind first-first pairing would glue
    one row's label to another row's value."""
    labels = _by_class(row, _LABEL_CLASS_FRAGS, limit=2)
    values = _by_class(row, _VALUE_CLASS_FRAGS, limit=2)
    if len(labels) != 1 or len(values) != 1:
        return None
    label_el, value_el = labels[0], values[0]
    if label_el is value_el:
        return None
    # The label element must not contain the value element or vice versa.
    if value_el in label_el.descendants or label_el in value_el.descendants:
        return None
    label = _norm(label_el.get_text())
    value = _norm(value_el.get_text())
    if _valid_pair(label, value):
        return SpecEntry(name=label, value=value)
    return None


# ── Generic div-pair extraction ──────────────────────────────────────────────

def _div_pairs(container: Tag) -> list[SpecEntry]:
    """Extract label/value pairs from a spec container's direct child rows."""
    entries: list[SpecEntry] = []
    for child in container.find_all(True, recursive=False):
        if not isinstance(child, Tag):
            continue
        kids = [k for k in child.children if isinstance(k, Tag)]
        if len(kids) == 2:
            label = _norm(kids[0].get_text())
            value = _norm(kids[1].get_text())
            if _valid_pair(label, value):
                entries.append(SpecEntry(name=label, value=value))
                continue
        entry = _class_hinted_pair(child)
        if entry:
            entries.append(entry)
    return entries


def _colon_line_pairs(container: Tag) -> list[SpecEntry]:
    """Extract "Label: value" text lines (li/p/div leaves) inside a spec container."""
    entries: list[SpecEntry] = []
    for el in container.find_all(["li", "p", "div", "span"]):
        if el.find(["ul", "ol", "table", "div", "p"]):
            continue  # not a leaf line
        m = _COLON_PAIR_RE.match(_norm(el.get_text()))
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if _valid_pair(label, value):
            entries.append(SpecEntry(name=label, value=value))
    return entries


# ── Merge helper ─────────────────────────────────────────────────────────────

def _merge(acc: dict[str, list[SpecEntry]], group: str, entries: list[SpecEntry]) -> None:
    if not entries:
        return
    seen = {e.name for e in acc.get(group, [])}
    added = acc.setdefault(group, [])
    for e in entries:
        if e.name not in seen:
            seen.add(e.name)
            added.append(e)


# ── Main entry point ─────────────────────────────────────────────────────────

def extract_all_specs(html: str) -> list[SpecGroup]:
    """Parse HTML and return all discovered spec groups."""
    soup = BeautifulSoup(html, "lxml")

    # Machine-readable payloads first (they live in <script>, read before decompose).
    jsonld_entries = _jsonld_groups(soup)
    state_entries = _embedded_state_entries(soup)

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
            if len(entries) < 3:
                colon = _colon_line_pairs(el)
                if len(colon) >= 3:
                    entries = colon
            if len(entries) >= 3:
                group = _nearest_heading(el) or current_heading
                _merge(acc, group, entries)

    # 3. Machine-readable fallbacks: merged last so visible-DOM values win, but
    # they fill everything a JS-rendered page hides from the static DOM.
    _merge(acc, _DEFAULT_GROUP, jsonld_entries)
    if len(state_entries) >= 3:
        _merge(acc, _DEFAULT_GROUP, state_entries)

    return _to_groups(acc)


def _to_groups(acc: dict[str, list[SpecEntry]]) -> list[SpecGroup]:
    return [SpecGroup(name=g, specs=es) for g, es in acc.items() if len(es) >= 2]


def merge_spec_groups(group_lists: list[list[SpecGroup]]) -> list[SpecGroup]:
    """Merge spec groups from several pages, deduplicating by (group, spec name).

    Earlier lists win on conflicts (pass the best page first). Lets us combine
    complementary coverage across sources instead of trusting a single page.
    """
    acc: dict[str, list[SpecEntry]] = {}
    for groups in group_lists:
        for group in groups:
            _merge(acc, group.name, group.specs)
    return [SpecGroup(name=g, specs=es) for g, es in acc.items() if es]
