"""Deterministic type coercion for extracted values.

Runs before the LLM normalizer: small local models are unreliable at pulling a
clean number out of a blob ("2 x DIMM slots" → 2) or snapping an enum
("VA matrix" → VA). Doing it deterministically is both more accurate and far
faster; the LLM is kept only for cases this can't confidently resolve."""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

# Unit conversion graph: unit -> (dimension, factor-to-base). Values in the same
# dimension are inter-convertible by ratio of factors.
_UNITS: dict[str, tuple[str, float]] = {
    # data (decimal, as used in storage/memory specs)
    "b": ("data", 1), "kb": ("data", 1e3), "mb": ("data", 1e6),
    "gb": ("data", 1e9), "tb": ("data", 1e12),
    # frequency
    "hz": ("freq", 1), "khz": ("freq", 1e3), "mhz": ("freq", 1e6), "ghz": ("freq", 1e9),
    # time
    "ns": ("time", 1e-9), "us": ("time", 1e-6), "µs": ("time", 1e-6),
    "ms": ("time", 1e-3), "s": ("time", 1),
    # length (nm/µm added — used by lithography/optics specs)
    "nm": ("len", 1e-9), "um": ("len", 1e-6), "µm": ("len", 1e-6),
    "mm": ("len", 1e-3), "cm": ("len", 1e-2), "m": ("len", 1),
    "inch": ("len", 0.0254), "in": ("len", 0.0254), '"': ("len", 0.0254),
    # mass
    "g": ("mass", 1), "kg": ("mass", 1e3),
    # power / electrical
    "w": ("power", 1), "kw": ("power", 1e3),
    "mv": ("volt", 1e-3), "v": ("volt", 1),
    "ma": ("current", 1e-3), "a": ("current", 1),
    # energy / charge (battery specs)
    "wh": ("energy", 1), "kwh": ("energy", 1e3),
    "mah": ("charge", 1), "ah": ("charge", 1e3),
    # byte-rate vs bit-rate are DISTINCT dimensions — never auto-convert between
    # them (they differ by a factor of 8); anchoring still picks the right figure.
    "b/s": ("byterate", 1), "kb/s": ("byterate", 1e3),
    "mb/s": ("byterate", 1e6), "gb/s": ("byterate", 1e9),
    "bps": ("bitrate", 1), "kbps": ("bitrate", 1e3),
    "mbps": ("bitrate", 1e6), "gbps": ("bitrate", 1e9),
    "bit/s": ("bitrate", 1), "kbit/s": ("bitrate", 1e3),
    "mbit/s": ("bitrate", 1e6), "gbit/s": ("bitrate", 1e9),
    # luminance (1 nit = 1 cd/m²)
    "nit": ("lum", 1), "nits": ("lum", 1), "cd/m2": ("lum", 1), "cd/m²": ("lum", 1),
}

_UNIT_ALIASES = {
    "inches": "inch", "hertz": "hz", "grams": "g", "kilograms": "kg",
    "amps": "a", "milliamps": "ma", "watts": "w",
}

_TRUE_TOKENS = {"yes", "true", "✓", "present", "supported", "available", "+", "є", "так", "да"}
_FALSE_TOKENS = {"no", "false", "✗", "—", "absent", "unsupported", "n/a", "none", "немає", "нет", "ні"}


def _canon_unit(u: str) -> str:
    u = u.strip().lower()
    return _UNIT_ALIASES.get(u, u)


def _to_float(num: str) -> float:
    return float(num.replace(",", "."))


def _fmt(value: float) -> str:
    """Render a float without a trailing .0 for whole numbers."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


@dataclass
class NumberResult:
    value: str
    unit: str | None
    confidence: float


def coerce_integer(raw: str) -> str | None:
    """First standalone integer in the text, e.g. '2 x DIMM slots' → '2'."""
    m = re.search(r"-?\d+", raw)
    if not m:
        return None
    return str(int(m.group()))


def coerce_number(raw: str, target_unit: str | None) -> NumberResult | None:
    """Extract a number (optionally next to a unit) and convert to target_unit.

    When a target unit is given, prefer a number adjacent to a same-dimension
    unit so 'Max. 96GB, DDR5 5600/...' yields 96 (the GB figure), not 5600."""
    tgt = _canon_unit(target_unit) if target_unit else None
    tgt_info = _UNITS.get(tgt) if tgt else None

    # Collect (number, unit) pairs in order. The unit group allows a "/sub" tail
    # and trailing digits/superscripts so compound units survive: "MB/s",
    # "Gbit/s", "cd/m2", "m²". Without this, the "/" truncated the unit (e.g.
    # "MB/s" → "MB") and same-dimension anchoring picked the wrong number.
    pairs: list[tuple[float, str | None]] = []
    for m in re.finditer(
        r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Zµ\"]+(?:/[a-zA-Zµ\"]+)?[0-9²³]*)?", raw
    ):
        num_s, unit_s = m.group(1), m.group(2)
        try:
            num = _to_float(num_s)
        except ValueError:
            continue
        unit = _canon_unit(unit_s) if unit_s else None
        if unit and unit not in _UNITS:
            unit = None
        pairs.append((num, unit))

    if not pairs:
        return None

    # With a target unit, find the first same-dimension figure and convert it.
    if tgt_info is not None:
        dim, tgt_factor = tgt_info
        for num, unit in pairs:
            if unit and _UNITS[unit][0] == dim:
                converted = num * _UNITS[unit][1] / tgt_factor
                return NumberResult(_fmt(converted), target_unit, 0.92)
        # No matching unit on the page — assume the raw figure is already in the
        # requested unit (common when the page omits the unit in a labelled row).
        num, _ = pairs[0]
        return NumberResult(_fmt(num), target_unit, 0.6)

    # No target unit: take the first number, keep its unit if recognised.
    num, unit = pairs[0]
    return NumberResult(_fmt(num), unit if unit else None, 0.85)


def coerce_boolean(raw: str) -> str | None:
    low = raw.strip().lower()
    if not low:
        return None
    first = low.split()[0].strip(".,;:")
    if first in _TRUE_TOKENS or low in _TRUE_TOKENS:
        return "true"
    if first in _FALSE_TOKENS or low in _FALSE_TOKENS:
        return "false"
    return None


def snap_enum(raw: str, allowed: list[str]) -> tuple[str, bool]:
    """Map a raw value onto one of `allowed`. Returns (value, matched)."""
    low = raw.lower()
    # Exact (case-insensitive) wins.
    for opt in allowed:
        if low.strip() == opt.lower():
            return opt, True
    # Whole-word containment; try longest options first so 'OLED' beats a stray
    # 'LED', 'micro-ATX' beats 'ATX'.
    for opt in sorted(allowed, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(opt.lower()) + r"(?![a-z0-9])", low):
            return opt, True
    return raw, False
