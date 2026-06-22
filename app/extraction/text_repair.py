"""Repair mojibake and normalize whitespace/punctuation in extracted text.

Pages are frequently served (or proxied) such that UTF-8 bytes end up decoded
as cp1252/latin-1, producing classic mojibake: "34â³" for 34″, "IntelÂ®" for
Intel®, "3,440 Ã 1,440" for 3,440 × 1,440, "âš ï¸" for an emoji. Left unfixed
this pollutes every downstream value, match, and LLM prompt.

We prefer ftfy (handles multi-round and edge cases); if it isn't importable we
fall back to the standard latin-1↔utf-8 round-trip, guarded so it only applies
when it actually reduces mojibake."""
from __future__ import annotations

import re
import unicodedata

try:  # ftfy is the robust path; the manual fallback covers its absence.
    from ftfy import fix_text as _ftfy_fix
except Exception:  # pragma: no cover - exercised only when ftfy is missing
    _ftfy_fix = None

# Byte signatures that almost always indicate UTF-8-as-cp1252 mojibake.
_MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â\x80", "Ð", "Ñ", "Å", "ð\x9f")
_WS_RE = re.compile(r"[ \t ​]+")
_MULTINL_RE = re.compile(r"\s*\n\s*")


def _weirdness(s: str) -> int:
    """Count characters that signal leftover mojibake — lower is cleaner."""
    return sum(s.count(m) for m in _MOJIBAKE_MARKERS)


def _manual_fix(s: str) -> str:
    if not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    try:
        repaired = s.encode("latin-1", "ignore").decode("utf-8", "ignore")
    except Exception:
        return s
    # Keep the repair only if it genuinely reduced mojibake and kept content.
    if repaired and _weirdness(repaired) < _weirdness(s):
        return repaired
    return s


def fix_text(s: str | None) -> str:
    """Repair mojibake and normalize unicode/whitespace. Safe on clean input."""
    if not s:
        return s or ""
    out = _ftfy_fix(s) if _ftfy_fix is not None else _manual_fix(s)
    # Canonical composition so "e + combining accent" etc. compare/printer-clean.
    out = unicodedata.normalize("NFC", out)
    out = _WS_RE.sub(" ", out)
    out = _MULTINL_RE.sub("\n", out)
    return out.strip()
