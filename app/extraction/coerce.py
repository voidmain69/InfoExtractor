"""Deterministic type coercion for extracted values.

Runs before the LLM normalizer: small local models are unreliable at pulling a
clean number out of a blob ("2 x DIMM slots" → 2) or snapping an enum
("VA matrix" → VA). Doing it deterministically is both more accurate and far
faster; the LLM is kept only for cases this can't confidently resolve."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Unit conversion graph: unit -> (dimension, factor-to-base). Values in the same
# dimension are inter-convertible by ratio of factors. Dimensionless-but-typed
# units (rpm, dB, ppm, dpi …) carry factor 1 so "1400 об/хв" still resolves to
# the requested "rpm" without any arithmetic.
_UNITS: dict[str, tuple[str, float]] = {
    # data (decimal, as used in storage/memory specs)
    "b": ("data", 1), "kb": ("data", 1e3), "mb": ("data", 1e6),
    "gb": ("data", 1e9), "tb": ("data", 1e12),
    # frequency
    "hz": ("freq", 1), "khz": ("freq", 1e3), "mhz": ("freq", 1e6), "ghz": ("freq", 1e9),
    # time
    "ns": ("time", 1e-9), "us": ("time", 1e-6), "µs": ("time", 1e-6),
    "ms": ("time", 1e-3), "s": ("time", 1), "min": ("time", 60), "h": ("time", 3600),
    # length (nm/µm added — used by lithography/optics specs)
    "nm": ("len", 1e-9), "um": ("len", 1e-6), "µm": ("len", 1e-6),
    "mm": ("len", 1e-3), "cm": ("len", 1e-2), "m": ("len", 1),
    "inch": ("len", 0.0254), "in": ("len", 0.0254), '"': ("len", 0.0254),
    "″": ("len", 0.0254),
    # mass
    "mg": ("mass", 1e-3), "g": ("mass", 1), "kg": ("mass", 1e3), "t": ("mass", 1e6),
    # volume
    "ml": ("vol", 1e-3), "cl": ("vol", 1e-2), "l": ("vol", 1),
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
    # rotation
    "rpm": ("rot", 1),
    # sound level
    "db": ("sound", 1), "dba": ("sound", 1),
    # pressure
    "bar": ("press", 1), "kpa": ("press", 0.01), "mpa": ("press", 10),
    "psi": ("press", 0.0689476),
    # temperature (no offset conversions — same-unit passthrough only)
    "°c": ("temp", 1), "°f": ("tempf", 1),
    # print/scan
    "ppm": ("ppm", 1), "dpi": ("dpi", 1),
    # flow
    "l/min": ("flow", 60.0), "l/h": ("flow", 1.0),
    "g/min": ("gflow", 1.0),
    # luminance (1 nit = 1 cd/m²)
    "nits": ("lum", 1), "cd/m2": ("lum", 1), "cd/m²": ("lum", 1),
    # pixels
    "mp": ("mpix", 1),
}

# Spelling / language aliases → canonical unit key (all lowercase).
_UNIT_ALIASES: dict[str, str] = {
    "inches": "inch", "дюйм": "inch", "дюйми": "inch", "дюймів": "inch",
    "дюймов": "inch", "дюйма": "inch",
    "hertz": "hz", "гц": "hz", "кгц": "khz", "мгц": "mhz", "ггц": "ghz",
    "grams": "g", "kilograms": "kg", "г": "g", "гр": "g", "кг": "kg", "мг": "mg",
    "мм": "mm", "см": "cm", "м": "m",
    "л": "l", "мл": "ml", "литр": "l", "литра": "l", "литров": "l",
    "літр": "l", "літра": "l", "літрів": "l", "liter": "l", "liters": "l",
    "litre": "l", "litres": "l",
    "вт": "w", "квт": "kw", "watt": "w", "watts": "w", "kwatt": "kw",
    "в": "v", "вольт": "v", "amps": "a", "milliamps": "ma",
    "об/хв": "rpm", "об/мин": "rpm", "r/min": "rpm", "1/min": "rpm",
    "об/min": "rpm", "rev/min": "rpm", "оборотов/мин": "rpm", "обертів/хв": "rpm",
    "дб": "db", "дба": "db", "db(a)": "db",
    "бар": "bar", "кпа": "kpa", "мпа": "mpa",
    "гб": "gb", "мб": "mb", "тб": "tb", "кб": "kb",
    "л/хв": "l/min", "л/мин": "l/min", "л/год": "l/h", "л/ч": "l/h", "l/hr": "l/h",
    "г/хв": "g/min", "г/мин": "g/min",
    "стор/хв": "ppm", "стр/мин": "ppm", "pages/min": "ppm", "ipm": "ppm",
    "pages/minute": "ppm", "сторінок/хв": "ppm", "аркушів/хв": "ppm",
    "страниц/мин": "ppm",
    "мач": "mah", "ма·год": "mah",
    "втгод": "wh", "вт·год": "wh",
    "с": "s", "сек": "s", "хв": "min", "мин": "min", "год": "h", "ч": "h",
    "мс": "ms",
    "°с": "°c", "℃": "°c", "градусів": "°c", "градусов": "°c",
    "nit": "nits", "кд/м2": "cd/m2", "кд/м²": "cd/m2",
}

_TRUE_TOKENS = {"yes", "true", "✓", "present", "supported", "available", "+", "є", "так", "да", "есть"}
_FALSE_TOKENS = {"no", "false", "✗", "—", "-", "absent", "unsupported", "n/a", "none",
                 "немає", "нет", "ні", "відсутній", "відсутня", "отсутствует"}

# number token with optional compound unit (latin/cyrillic, °, ″, quotes, slash
# compounds like "об/хв" or "cd/m2", dotted abbreviations like "стор./хв")
_PAIR_RE = re.compile(
    r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Zа-яіїєґА-ЯІЇЄҐµ°″℃\"](?:[a-zA-Zа-яіїєґА-ЯІЇЄҐµ°″\"²/·.()0-9]*[a-zA-Zа-яіїєґА-ЯІЇЄҐ0-9²″\")])?)?"
)

_THOUSANDS_SPACE_RE = re.compile(r"(?<=\d)[\s   ](?=\d{3}(?:\D|$))")
_THOUSANDS_COMMA_RE = re.compile(r"(?<=\d),(?=\d{3}(?:[^\d,]|$))")


def _canon_unit(u: str) -> str:
    u = u.strip().lower().strip(".,;:()")
    u = u.replace("·", "").replace(" ", "").replace(".", "")
    return _UNIT_ALIASES.get(u, u)


# "20 - 145 bar" / "20 - макс. 145" — a spec range; the headline figure is the
# upper bound.
_RANGE_LOW_RE = re.compile(
    r"(?<![\d.,])-?\d+(?:[.,]\d+)?\s*[-–—…]\s*(?:макс\.?|max\.?|до)?\s*(?=\d)", re.I
)

# A unit hint embedded in the LABEL: "Потужність (кВт)", "Weight, kg",
# "Тиск, бар". Pages routinely keep the value cell as a bare number then.
_LABEL_UNIT_RE = re.compile(
    r"[,(/]\s*([a-zA-Zа-яіїєґА-ЯІЇЄҐµ°″\"][a-zA-Zа-яіїєґА-ЯІЇЄҐµ°″\"²/·.0-9]{0,10})\s*\)?\s*$"
)


def label_unit(label: str) -> str | None:
    """Extract a trailing unit hint from a spec label, if any."""
    m = _LABEL_UNIT_RE.search(label.strip())
    if not m:
        return None
    unit = _canon_unit(m.group(1))
    return unit if unit in _UNITS else None


def has_unit(value: str) -> bool:
    """True if any number in the value already sits next to a known unit."""
    return any(u for _, u in _number_unit_pairs(value))


def _preclean(raw: str) -> str:
    """Collapse thousands separators so '1 400 об/хв' / '1,400 rpm' parse as
    1400, and drop the lower bound of ranges so '20 - 145 bar' reads 145."""
    s = _THOUSANDS_SPACE_RE.sub("", raw)
    s = _THOUSANDS_COMMA_RE.sub("", s)
    s = _RANGE_LOW_RE.sub("", s)
    return s


def _to_float(num: str) -> float:
    return float(num.replace(",", "."))


def _fmt(value: float) -> str:
    """Render a float without a trailing .0 for whole numbers."""
    if value == int(value):
        return str(int(value))
    return f"{round(value, 6):g}"


@dataclass
class NumberResult:
    value: str
    unit: str | None
    confidence: float


def _number_unit_pairs(raw: str) -> list[tuple[float, str | None]]:
    """All (number, canonical-unit-or-None) pairs in reading order.

    Digits embedded in alphanumeric tokens (model numbers like WAN28280UA,
    HL-L2352DW) are NOT spec figures: a number glued to an unrecognized
    letter-suffix, or glued to preceding letters, is skipped entirely."""
    pairs: list[tuple[float, str | None]] = []
    text = _preclean(raw)
    for m in _PAIR_RE.finditer(text):
        num_s, unit_s = m.group(1), m.group(2)
        # Preceded directly by a letter → inside a model token.
        start = m.start(1)
        if start > 0 and (text[start - 1].isalpha() or text[start - 1] in "-_/"):
            continue
        try:
            num = _to_float(num_s)
        except ValueError:
            continue
        unit = _canon_unit(unit_s) if unit_s else None
        if unit and unit not in _UNITS:
            attached = unit_s is not None and m.start(2) == m.end(1)
            if attached and len(unit) >= 2:
                continue  # "28280UA" — model-number tail, not a unit
            unit = None
        pairs.append((num, unit))
    return pairs


def coerce_integer(raw: str) -> str | None:
    """First standalone integer in the text, e.g. '2 x DIMM slots' → '2'.

    Long prose (a marketing paragraph fuzzily matched to a count attribute) is
    rejected outright — any number found there is a model number or a spec of
    something else, and a wrong count is worse than not_found."""
    if len(raw) > 200:
        return None
    pairs = _number_unit_pairs(raw)
    if not pairs:
        return None
    return str(int(pairs[0][0]))


def coerce_number(raw: str, target_unit: str | None) -> NumberResult | None:
    """Extract a number (optionally next to a unit) and convert to target_unit.

    When a target unit is given, prefer a number adjacent to a same-dimension
    unit so 'Max. 96GB, DDR5 5600/...' yields 96 (the GB figure), not 5600.
    For a range next to the matching unit ('400 - 1400 об/хв') the unit-adjacent
    figure wins, which is the spec's headline number."""
    tgt = _canon_unit(target_unit) if target_unit else None
    tgt_info = _UNITS.get(tgt) if tgt else None

    pairs = _number_unit_pairs(raw)
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


_NEGATION_RE = re.compile(
    r"\b(no|not|none|without|немає|нема|ні|відсутн\w*|нет|отсутств\w*|бе[зс])\b", re.I
)


def coerce_boolean(raw: str) -> str | None:
    low = raw.strip().lower()
    if not low:
        return None
    first = low.split()[0].strip(".,;:")
    if first in _TRUE_TOKENS or low in _TRUE_TOKENS:
        return "true"
    if first in _FALSE_TOKENS or low in _FALSE_TOKENS:
        return "false"
    if _NEGATION_RE.search(low):
        return "false"
    # A descriptive value naming the capability ("Automatic 2-sided Printing",
    # "Так, з дисплеєм") is an affirmative answer on spec pages.
    if len(low) <= 80 and any(c.isalpha() for c in low):
        return "true"
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
        if re.search(r"(?<![a-zа-яіїєґ0-9])" + re.escape(opt.lower()) + r"(?![a-zа-яіїєґ0-9])", low):
            return opt, True
    return raw, False
