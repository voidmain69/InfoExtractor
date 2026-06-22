"""Curated synonym groups for spec attribute labels.

A deterministic pre-pass before the LLM semantic matcher: small models miss
obvious equivalences ("Response time" vs "Reaction time") inconsistently, and
many retail pages use machine-translated labels ("Update frequency" for refresh
rate, "Type of matrix" for panel type). Resolving the well-known ones from a
table is reliable and free; the LLM only handles what isn't covered here."""
from __future__ import annotations

import re

# Each group lists equivalent labels (any language) for one characteristic.
_GROUPS: list[set[str]] = [
    {"refresh rate", "update frequency", "refresh frequency", "frame rate",
     "частота оновлення", "частота розгортки", "частота обновления"},
    {"response time", "reaction time", "gray to gray", "gtg", "grey to grey",
     "час відгуку", "время отклика"},
    {"panel type", "type of matrix", "matrix type", "matrix", "panel",
     "display type", "тип матриці", "тип матрицы"},
    {"curvature", "curved screen", "curve", "curvature radius", "screen curvature",
     "кривизна", "радіус кривизни"},
    {"resolution", "recommended resolution", "native resolution", "max resolution",
     "maximum resolution", "screen resolution", "роздільна здатність", "разрешение"},
    {"brightness", "bright", "luminance", "max brightness", "яскравість", "яркость"},
    {"contrast", "contrast ratio", "static contrast", "dynamic contrast",
     "контраст", "контрастність"},
    {"aspect ratio", "support for parties", "співвідношення сторін", "соотношение сторон"},
    {"viewing angle", "viewing angles", "vertical viewing angle",
     "horizontal viewing angle", "кути огляду", "углы обзора"},
    {"screen size", "screen diagonal", "diagonal", "display size", "panel size",
     "діагональ", "диагональ"},
    {"memory slots", "dimm slots", "ram slots", "memory dimm", "number of dimm",
     "memory channels"},
    {"max memory", "maximum memory", "memory capacity", "max. memory",
     "supported memory", "максимальна пам'ять"},
    {"form factor", "форм-фактор", "форм фактор"},
    {"weight", "gross weight", "net weight", "the weight", "вага", "маса", "вес"},
    {"power consumption", "power", "energy consumption", "споживання",
     "энергопотребление", "потужність"},
    {"color depth", "colour depth", "bit depth", "глибина кольору"},
    {"connectors", "ports", "inputs", "interfaces", "роз'єми", "разъемы"},
]

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _norm(label: str) -> str:
    s = _PUNCT_RE.sub(" ", label.lower())
    return _WS_RE.sub(" ", s).strip()


# Precompute a phrase → group-index map for O(1) exact lookups.
_PHRASE_TO_GROUP: dict[str, int] = {}
for _i, _grp in enumerate(_GROUPS):
    for _phrase in _grp:
        _PHRASE_TO_GROUP[_norm(_phrase)] = _i


def _group_of(phrase: str) -> int | None:
    norm = _norm(phrase)
    if norm in _PHRASE_TO_GROUP:
        return _PHRASE_TO_GROUP[norm]
    # Token-subset tolerance: "vertical viewing angle" → viewing-angle group.
    tokens = set(norm.split())
    for known, idx in _PHRASE_TO_GROUP.items():
        kt = set(known.split())
        if kt and (kt <= tokens or tokens <= kt):
            return idx
    return None


def find_synonym_label(attribute: str, labels: list[str]) -> str | None:
    """Return the page label that is a known synonym of `attribute`, or None."""
    target = _group_of(attribute)
    if target is None:
        return None
    for label in labels:
        if _group_of(label) == target:
            return label
    return None
