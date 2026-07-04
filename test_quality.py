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
ua_labels = ["Швидкість віджиму", "Рівень шуму", "Підсистема пам'яті", "Програми прання"]
check("Оберти віджиму", find_synonym_label("Оберти віджиму", ua_labels), "Швидкість віджиму")
check("Гучність", find_synonym_label("Гучність", ua_labels), "Рівень шуму")
check("Слоти пам'яті (multi-group)", find_synonym_label("Слоти оперативної пам'яті", ua_labels), "Підсистема пам'яті")
check("Кількість програм ≠ список програм",
      find_synonym_label("Кількість програм прання", ["Програми прання"]), None)

print("\n=== multilingual units / ranges / model numbers ===")
r = coerce_number("1 400 об/хв", "rpm")
check("1 400 об/хв → rpm", (r.value, r.unit) if r else None, ("1400", "rpm"))
r = coerce_number("2,1 кВт", "W")
check("кВт → W", (r.value, r.unit) if r else None, ("2100", "W"))
r = coerce_number("350 мл", "l")
check("мл → l", (r.value, r.unit) if r else None, ("0.35", "l"))
r = coerce_number("20 - макс. 145 / 2 - макс. 14,5", "bar")
check("range takes upper bound", r.value if r else None, "145")
r = coerce_number("Up to 30 pages/minute (A4 size)", "ppm")
check("pages/minute → ppm", r.value if r else None, "30")
r = coerce_number("60 см", "mm")
check("см → mm", (r.value, r.unit) if r else None, ("600", "mm"))
check("model number ignored (integer)",
      coerce_integer("Пральна машина Bosch WAN28280UA на 8 кг"), "8")
check("prose too long → None", coerce_integer("x" * 201 + " 5"), None)

print("\n=== facet-like values (catalog filters) ===")
from app.extraction.all_specs import _facet_like
check("weight facet", _facet_like("10 кг 10.5 кг 11 кг 12 кг 2 кг 2.5 кг 3 кг 8 кг"), True)
check("bare numbers facet", _facet_like("1 10 11 12 13 14 15 16 17 18 19 2 20"), True)
check("memory row not facet", _facet_like(
    "Support for DDR4 4733(O.C.) / 4600(O.C.) / 4400(O.C.) / 3200 / 2933 / 2667 / 2400 MT/s memory modules"), False)
check("ports row not facet", _facet_like(
    "1 x PS/2 port 1 x DVI-D port 1 x DisplayPort 1 x HDMI port 4 x USB 3.2 Gen 1 ports"), False)

print("\n=== segment selection ===")
from app.extraction.value_cleaner import select_segment
check("spin noise segment", select_segment("55 прання | 73 віджимання", "Гучність під час віджиму"), "73 віджимання")
check("no qualifier → unchanged", select_segment("55 прання | 73 віджимання", "Шум"), "55 прання | 73 віджимання")

print("\n=== label similarity (token-aware) ===")
from app.extraction.label_similarity import similarity
check("unit suffix ≥0.85", similarity("Швидкість віджиму", "Швидкість віджиму, об/хв") >= 0.85, True)
check("qualifier noise ≥0.85", similarity("Spin speed", "Max. spin speed (rpm)") >= 0.85, True)
check("different attrs <0.78", similarity("Screen size", "Screen type") < 0.78, True)

print("\n--- done ---")
