"""Unit tests for the extraction/normalization quality improvements."""
import sys
sys.path.insert(0, ".")

from app.extraction.text_repair import fix_text
from app.extraction.coerce import coerce_integer, coerce_number, coerce_boolean, snap_enum
from app.extraction.value_cleaner import clean_value
from app.services.synonyms import find_synonym_label

PASS, FAIL = "PASS", "FAIL"

def check(label, got, expected):
    ok = got == expected
    print(f"  {PASS if ok else FAIL}: {label}: got={got!r} expected={expected!r}")

print("=== text_repair (mojibake) ===")
check("inch sign", fix_text("34â€³"), '34"' ) if False else None
# Build mojibake from real UTF-8 bytes read as cp1252:
moji = '″'.encode('utf-8').decode('latin-1')          # ″ -> mojibake
check("repair ″", fix_text("34" + moji), "34″")
moji2 = '×'.encode('utf-8').decode('latin-1')         # × -> mojibake
check("repair ×", fix_text("3,440 " + moji2 + " 1,440"), "3,440 × 1,440")
moji3 = '®'.encode('utf-8').decode('latin-1')         # ® -> mojibake
check("repair ®", fix_text("Intel " + moji3), "Intel ®")
check("clean passthrough", fix_text("180 Hz"), "180 Hz")

print("\n=== coerce_integer ===")
check("2 x DIMM slots", coerce_integer("2 x DIMM slots, Max. 96GB, DDR5"), "2")
check("plain", coerce_integer("4"), "4")
check("none", coerce_integer("no number here"), None)

print("\n=== coerce_number ===")
r = coerce_number("2 x DIMM, Max. 96GB, DDR5 5600", "GB")
check("96GB from blob", (r.value, r.unit) if r else None, ("96", "GB"))
r = coerce_number("180 Hz", "Hz")
check("180 Hz", (r.value, r.unit) if r else None, ("180", "Hz"))
r = coerce_number("1 ms", "ms")
check("1 ms", (r.value, r.unit) if r else None, ("1", "ms"))
r = coerce_number("1000 MHz", "GHz")
check("MHz->GHz", (r.value, r.unit) if r else None, ("1", "GHz"))

print("\n=== snap_enum ===")
check("VA matrix -> VA", snap_enum("VA matrix", ["IPS","VA","TN","OLED"]), ("VA", True))
check("exact VA", snap_enum("VA", ["IPS","VA","TN","OLED"]), ("VA", True))
check("micro-ATX", snap_enum("micro-ATX Form Factor", ["ATX","micro-ATX","mini-ITX"]), ("micro-ATX", True))
check("180 in list", snap_enum("180", ["60","120","144","165","180"]), ("180", True))

print("\n=== clean_value (blob trim) ===")
v, u = clean_value("micro-ATX Form Factor9.2 inch x 8.0 inch ( 23.4 cm x 20.3 cm )", "Form factor")
check("form factor value", v, "micro-ATX")
check("form factor unit (no leak)", u, None)
v, u = clean_value("✔️ CURVED R1500", "Curvature")
check("strip leading emoji", v, "CURVED R1500")

print("\n=== synonyms ===")
labels = ["Type of matrix", "Update frequency", "Reaction time", "Curved screen", "Bright"]
check("Refresh rate", find_synonym_label("Refresh rate", labels), "Update frequency")
check("Response time", find_synonym_label("Response time", labels), "Reaction time")
check("Panel type", find_synonym_label("Panel type", labels), "Type of matrix")
check("Curvature", find_synonym_label("Curvature", labels), "Curved screen")
check("Unknown attr", find_synonym_label("Random thing", labels), None)

print("\n--- done ---")
