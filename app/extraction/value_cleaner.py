"""Post-process a reconciled value: repair text, trim verbose blobs, split unit."""
from __future__ import annotations

import re
import unicodedata

from app.extraction.text_repair import fix_text

# number + unit, e.g. "180 Hz", "1ms", "34\"", "300 cd/m2", "96GB", "1400 об/хв"
_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r'(TB|GB|MB|KB|GHz|MHz|kHz|Hz|ms|mm|cm|nm|kg|g|nits|cd/m2|px|bit|kW|W|V|A|"|°C|°|inch|inches|'
    r"rpm|dB|bar|ppm|dpi|ml|l|об/хв|об/мин|кг|г|л|мл|кВт|Вт|дБ|бар|мм|см|Гц|ГГц|МГц)\b",
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


# Multi-part value segments: "55 прання | 73 віджимання", "53 dB washing; 73 dB spin"
_SEG_SPLIT_RE = re.compile(r"\s*[|;•]\s*|\s{3,}|,\s+(?=[^\d\s])")
_WORD_RE = re.compile(r"[a-zа-яіїєґ]+", re.I)


def select_segment(value: str, attribute: str) -> str:
    """When a value carries several qualified figures, keep the segment whose
    qualifier matches the attribute ('… під час віджиму' → '73 віджимання').

    Only numeric coercion consumes this, so a stem-matched segment WITHOUT any
    digit is not a qualified figure — it's prose that happens to repeat the
    attribute word ('… Memory Architecture…' for 'Maximum memory'). Returning
    it would hide the actual figure sitting in a sibling segment ('Max. 96GB'),
    so such matches are skipped and the full value is kept as the fallback."""
    segments = [s for s in _SEG_SPLIT_RE.split(value) if s and s.strip()]
    if len(segments) < 2:
        return value
    # Stem crudely by prefix so inflected forms match (віджиму ~ віджимання).
    stems = {w.lower()[:5] for w in _WORD_RE.findall(attribute) if len(w) >= 5}
    if not stems:
        return value
    for seg in segments:
        low = seg.lower()
        if any(st in low for st in stems) and any(c.isdigit() for c in seg):
            return seg.strip()
    return value


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
