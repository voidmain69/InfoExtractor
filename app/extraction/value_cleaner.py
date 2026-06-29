"""Post-process a reconciled value: repair text, trim verbose blobs, split unit."""
from __future__ import annotations

import re
import unicodedata

from app.extraction.text_repair import fix_text

# number + unit, e.g. "180 Hz", "1ms", "34\"", "300 cd/m2", "96GB", "2.1 V"
_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r'(TB|GB|MB|KB|GHz|MHz|Hz|ms|mm|cm|nm|kg|nits|cd/m2|px|bit|W|V|A|"|°|inch|inches)\b',
    re.I,
)

# A glued dimensions clause, e.g. "9.2 inch x 8.0 inch" or "23.4 cm x 20.3 cm".
_DIM_TAIL_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:inch|inches|in|cm|mm)\b", re.I,
)

# Sibling section markers: if the attribute asks about one side but the value
# blob also contains the opposite side, cut the value at the opposite side.
_SIBLINGS: list[tuple[str, str]] = [
    ("rear", "front"),
    ("front", "rear"),
    ("back", "front"),
    ("internal", "external"),
    ("external", "internal"),
]

_STOPWORDS = {"of", "the", "a", "type", "size"}

# Boilerplate/footnote tail that marks the end of the real value and the start of
# fine print or a glued-on next section. Everything from the marker on is dropped.
_BLOB_TAIL_RE = re.compile(
    r"\s*(?:"
    r"https?://"          # links to support/download pages
    r"|\bplease\s+refer\b"
    r"|\bplease\s+visit\b"
    r"|\bfor\s+more\s+information\b"
    r"|\bsupported\s+memory\s+types\b"
    r"|\*\s*\S"           # footnote asterisk ("...DIMM*  Supported...")
    r").*$",
    re.I | re.S,
)


def extract_unit(value: str) -> str | None:
    m = _UNIT_RE.search(value)
    if not m:
        return None
    unit = m.group(2)
    return '"' if unit == '"' else unit


def _strip_leading_symbols(s: str) -> str:
    """Drop leading emoji/bullets/symbols, e.g. '✔️ CURVED R1500' → 'CURVED R1500'."""
    i = 0
    while i < len(s):
        ch = s[i]
        if (ch.isspace() or ch == "️" or unicodedata.combining(ch)
                or unicodedata.category(ch)[0] == "S" or ch in "•‣◦·"):
            i += 1
        else:
            break
    return s[i:].strip()


def _strip_attr_echo(value: str, attribute: str) -> str:
    """Drop a trailing echo of the attribute label, e.g. value 'micro-ATX Form
    Factor' for attribute 'Form factor' → 'micro-ATX'."""
    attr_tokens = [t for t in re.findall(r"[a-z0-9]+", attribute.lower())
                   if t not in _STOPWORDS]
    if not attr_tokens:
        return value
    tokens = value.split()
    # Peel attribute-name words off the end (case-insensitive).
    while tokens and tokens[-1].lower().strip(".,;:-") in attr_tokens:
        tokens.pop()
    stripped = " ".join(tokens).strip(" ,;:-—–")
    return stripped or value


def clean_value(value: str, attribute: str) -> tuple[str, str | None]:
    """Return (possibly-trimmed value, unit-or-None)."""
    cleaned = fix_text(value).strip()

    # Cut a verbose-blob tail: keep only the first line, then drop fine print /
    # glued-on next sections (footnotes, "please refer to…", support links). A
    # spec table cell often concatenates the whole memory/IO section into one
    # value; this keeps the head and removes the boilerplate that follows.
    cleaned = cleaned.split("\n", 1)[0].strip()
    cleaned = _BLOB_TAIL_RE.sub("", cleaned).strip(" ,;:-—–")

    attr_lower = attribute.lower()
    val_lower = cleaned.lower()

    # Trim multi-section blobs: keep only the requested side.
    for side, other in _SIBLINGS:
        if side in attr_lower and side in val_lower and other in val_lower:
            other_idx = val_lower.find(other)
            side_idx = val_lower.find(side)
            if other_idx > side_idx and other_idx > 10:
                cleaned = cleaned[:other_idx].strip(" ,;-—–")
                break

    # Trim a glued dimensions tail when there's real content before it, e.g.
    # "micro-ATX Form Factor9.2 inch x 8.0 inch (...)" → "micro-ATX Form Factor".
    dim = _DIM_TAIL_RE.search(cleaned)
    if dim and dim.start() >= 3 and "x" in cleaned[dim.start():].lower():
        head = cleaned[:dim.start()].strip(" ,;:-—–(")
        if head:
            cleaned = head

    # Remove a trailing echo of the attribute label and leading symbols/emoji.
    cleaned = _strip_attr_echo(cleaned, attribute)
    cleaned = _strip_leading_symbols(cleaned)

    # Derive the unit from the FINAL value so a trimmed-away dimension tail can't
    # leak its unit (e.g. the "inch" from a stripped "9.2 inch x 8.0 inch").
    unit = extract_unit(cleaned)

    return cleaned, unit
