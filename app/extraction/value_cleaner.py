"""Post-process a reconciled value: trim verbose blobs and split out the unit."""
from __future__ import annotations

import re

# number + unit, e.g. "180 Hz", "1ms", "34\"", "300 cd/m2", "96GB", "2.1 V"
_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r'(TB|GB|MB|KB|GHz|MHz|Hz|ms|mm|cm|nm|kg|nits|cd/m2|px|bit|W|V|A|"|°|inch|inches)\b',
    re.I,
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


def extract_unit(value: str) -> str | None:
    m = _UNIT_RE.search(value)
    if not m:
        return None
    unit = m.group(2)
    return '"' if unit == '"' else unit


def clean_value(value: str, attribute: str) -> tuple[str, str | None]:
    """Return (possibly-trimmed value, unit-or-None)."""
    unit = extract_unit(value)
    cleaned = value.strip()

    attr_lower = attribute.lower()
    val_lower = cleaned.lower()

    # Trim multi-section blobs: keep only the requested side.
    for side, other in _SIBLINGS:
        if side in attr_lower and side in val_lower and other in val_lower:
            other_idx = val_lower.find(other)
            side_idx = val_lower.find(side)
            # Only cut if the other side comes *after* the requested side and
            # there is enough content before it to be a real value.
            if other_idx > side_idx and other_idx > 10:
                cleaned = cleaned[:other_idx].strip(" ,;-—–")
                break

    return cleaned, unit
