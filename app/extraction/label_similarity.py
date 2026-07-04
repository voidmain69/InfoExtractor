"""Similarity between a requested attribute name and a page's spec label.

Pure char-level SequenceMatcher under-scores the common real-world cases:
labels with unit suffixes ("Load capacity, kg"), reordered words ("Speed of
spin" vs "Spin speed"), or qualifier noise ("Max. spin speed (rpm)"). Token
overlap handles those; the char ratio still covers typos and inflections."""
from __future__ import annotations

import difflib
import re

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_PAREN = re.compile(r"\([^)]*\)")

# Trailing unit hints commonly glued onto labels: "Capacity (kg)", "Weight, g".
_UNIT_TOKENS = frozenset({
    "kg", "g", "mm", "cm", "m", "l", "ml", "w", "kw", "v", "hz", "khz", "mhz",
    "ghz", "gb", "mb", "tb", "ms", "s", "min", "h", "db", "bar", "rpm", "ppm",
    "dpi", "inch", "inches", "кг", "г", "мм", "см", "м", "л", "мл", "вт", "квт",
    "в", "гц", "дб", "бар", "дюйм", "дюймів",
})

# Filler words that don't identify the characteristic.
_STOP_TOKENS = frozenset({
    "of", "the", "a", "an", "for", "in", "max", "maximum", "min", "minimum",
    "макс", "максимальна", "максимальний", "максимальная", "максимальный",
    "мін", "мин",
})


def _norm(text: str) -> str:
    s = text.lower().replace("’", "'").replace("`", "'").replace("ʼ", "'")
    s = _PAREN.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if t not in _STOP_TOKENS}


def _core_tokens(tokens: set[str]) -> set[str]:
    core = {t for t in tokens if t not in _UNIT_TOKENS}
    return core or tokens


def similarity(a: str, b: str) -> float:
    """0..1 similarity between two spec labels, tolerant of unit suffixes,
    word order, and qualifier noise."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ratio = difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()

    # Full containment of the shorter phrase.
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if short in long_:
        ratio = max(ratio, 0.80)

    ta, tb = _core_tokens(_tokens(na)), _core_tokens(_tokens(nb))
    if ta and tb:
        if ta == tb:
            ratio = max(ratio, 0.95)
        elif ta <= tb or tb <= ta:
            # Every content word of one label appears in the other
            # ("spin speed" ⊆ "max spin speed rpm").
            ratio = max(ratio, 0.85)
        else:
            inter = len(ta & tb)
            if inter:
                jacc = inter / len(ta | tb)
                ratio = max(ratio, 0.6 + 0.35 * jacc)

    return ratio
