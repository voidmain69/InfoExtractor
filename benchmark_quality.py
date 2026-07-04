"""End-to-end extraction quality benchmark against a running getAttrService.

11 real products, 11 vendors, mixed categories. Attribute names are asked the
way a PIM taxonomy really asks them — Ukrainian/English mix, inexact wording,
and units that differ from what source pages print (см↔mm, кВт↔W, мл↔l,
об/хв↔rpm) — so the score measures the whole chain: search → fetch →
extraction → label matching → unit normalization.

Usage:
    python benchmark_quality.py [--base http://localhost:8000] [--only bosch]
    python benchmark_quality.py --json results.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

import httpx

# ── expected-value checkers ──────────────────────────────────────────────────

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _nums(text: str) -> list[float]:
    return [float(m.replace(",", ".")) for m in _NUM_RE.findall(text or "")]


def check_number(value: str | None, expect: float, tol: float = 0.02,
                 accept: tuple[float, ...] = ()) -> bool:
    """First number in the value must equal expect (or any accept) within tol."""
    if not value:
        return False
    nums = _nums(value)
    if not nums:
        return False
    targets = (expect,) + tuple(accept)
    return any(abs(nums[0] - t) <= abs(t) * tol + 1e-9 for t in targets)


def check_contains(value: str | None, needles: tuple[str, ...]) -> bool:
    """Case-insensitive: any needle must appear in the value."""
    if not value:
        return False
    low = value.lower().replace("‑", "-")
    return any(n.lower() in low for n in needles)


def check_bool(value: str | None, expect: bool) -> bool:
    if not value:
        return False
    low = value.strip().lower()
    truthy = low in ("true", "yes", "так", "да", "є", "1") or "так" in low or "yes" in low
    return truthy == expect


# ── benchmark definition ─────────────────────────────────────────────────────
# Each attribute: name (deliberately fuzzy / localized), request payload fields,
# and a `check` closure that judges the returned value.

BENCHMARK: list[dict] = [
    {
        "key": "bosch",
        "product": {"name": "WAN28280UA", "brand": "Bosch", "category": "washing machine"},
        "attributes": [
            {"name": "Швидкість віджиму", "type": "number", "unit": "rpm",
             "check": lambda v, u: check_number(v, 1400)},
            {"name": "Обсяг завантаження білизни", "type": "number", "unit": "kg",
             "check": lambda v, u: check_number(v, 8)},
            {"name": "Ширина", "type": "number", "unit": "cm",
             "check": lambda v, u: check_number(v, 59.8, accept=(60,))},
            {"name": "Кількість програм прання", "type": "integer",
             "check": lambda v, u: check_number(v, 15)},
            {"name": "Гучність під час віджиму", "type": "number", "unit": "dB",
             "check": lambda v, u: check_number(v, 73)},
        ],
    },
    {
        "key": "beko",
        "product": {"name": "WUE6511XWW", "brand": "Beko", "category": "washing machine"},
        "attributes": [
            {"name": "Оберти віджиму", "type": "number", "unit": "rpm",
             "check": lambda v, u: check_number(v, 1000)},
            {"name": "Максимальне завантаження", "type": "number", "unit": "kg",
             "check": lambda v, u: check_number(v, 6)},
            {"name": "Ширина", "type": "number", "unit": "mm",
             "check": lambda v, u: check_number(v, 600)},
            {"name": "Кількість програм", "type": "integer",
             "check": lambda v, u: check_number(v, 15)},
        ],
    },
    {
        "key": "lg",
        "product": {"name": "27GP850-B", "brand": "LG", "category": "monitor"},
        "attributes": [
            {"name": "Діагональ екрану", "type": "number", "unit": "inch",
             "check": lambda v, u: check_number(v, 27)},
            {"name": "Роздільна здатність", "type": "string",
             "check": lambda v, u: check_contains(v, ("2560",))},
            {"name": "Частота оновлення екрану", "type": "number", "unit": "Hz",
             "check": lambda v, u: check_number(v, 165, accept=(180,))},
            {"name": "Тип матриці", "type": "enum",
             "allowed_values": ["IPS", "VA", "TN", "OLED"],
             "check": lambda v, u: check_contains(v, ("IPS",))},
            {"name": "Час відгуку", "type": "number", "unit": "ms",
             "check": lambda v, u: check_number(v, 1)},
        ],
    },
    {
        "key": "2e",
        "product": {"name": "G3424B", "brand": "2E GAMING", "category": "monitor",
                    "article": "2E-G3424B-01.UA"},
        "attributes": [
            {"name": "Діагональ", "type": "number", "unit": "inch",
             "check": lambda v, u: check_number(v, 34)},
            {"name": "Матриця", "type": "enum",
             "allowed_values": ["IPS", "VA", "TN", "OLED"],
             "check": lambda v, u: check_contains(v, ("VA",))},
            {"name": "Частота оновлення", "type": "number", "unit": "Hz",
             "check": lambda v, u: check_number(v, 180, accept=(165,))},
            {"name": "Кривизна екрану", "type": "string",
             "check": lambda v, u: check_contains(v, ("1500",))},
            {"name": "Роздільна здатність", "type": "string",
             "check": lambda v, u: check_contains(v, ("3440",))},
        ],
    },
    {
        "key": "gigabyte",
        "product": {"name": "B550M DS3H", "brand": "Gigabyte", "category": "motherboard"},
        "attributes": [
            {"name": "Сокет процесора", "type": "string",
             "check": lambda v, u: check_contains(v, ("AM4",))},
            {"name": "Слоти оперативної пам'яті", "type": "integer",
             "check": lambda v, u: check_number(v, 4)},
            {"name": "Максимальний обсяг пам'яті", "type": "number", "unit": "GB",
             "check": lambda v, u: check_number(v, 128)},
            {"name": "Форм-фактор", "type": "string",
             "check": lambda v, u: check_contains(v, ("micro atx", "micro-atx", "matx", "μatx", "uatx"))},
        ],
    },
    {
        "key": "karcher",
        "product": {"name": "K 5 Power Control", "brand": "Karcher",
                    "category": "pressure washer"},
        "attributes": [
            {"name": "Максимальний тиск", "type": "number", "unit": "bar",
             "check": lambda v, u: check_number(v, 145)},
            {"name": "Продуктивність (витрата води)", "type": "number", "unit": "l/h",
             "check": lambda v, u: check_number(v, 500)},
            {"name": "Споживана потужність", "type": "number", "unit": "W",
             "check": lambda v, u: check_number(v, 2100)},
        ],
    },
    {
        "key": "braun",
        "product": {"name": "MQ 5235", "brand": "Braun", "category": "hand blender"},
        "attributes": [
            {"name": "Потужність", "type": "number", "unit": "W",
             "check": lambda v, u: check_number(v, 1000)},
            {"name": "Кількість швидкостей", "type": "integer",
             "check": lambda v, u: check_number(v, 21)},
            {"name": "Довжина шнура живлення", "type": "number", "unit": "m",
             "check": lambda v, u: check_number(v, 1.2)},
        ],
    },
    {
        "key": "philips",
        "product": {"name": "EP2231/40", "brand": "Philips", "category": "coffee machine"},
        "attributes": [
            {"name": "Тиск помпи", "type": "number", "unit": "bar",
             "check": lambda v, u: check_number(v, 15)},
            {"name": "Об'єм резервуара для води", "type": "number", "unit": "l",
             "check": lambda v, u: check_number(v, 1.8)},
        ],
    },
    {
        "key": "brother",
        "product": {"name": "HL-L2352DW", "brand": "Brother", "category": "laser printer"},
        "attributes": [
            {"name": "Швидкість друку", "type": "number", "unit": "ppm",
             "check": lambda v, u: check_number(v, 30)},
            {"name": "Двосторонній друк", "type": "boolean",
             "check": lambda v, u: check_bool(v, True)},
            {"name": "Роздільна здатність друку", "type": "string",
             "check": lambda v, u: check_contains(v, ("2400", "1200"))},
            {"name": "Ємність лотка для паперу", "type": "integer",
             "check": lambda v, u: check_number(v, 250)},
        ],
    },
    {
        "key": "tefal",
        "product": {"name": "FV9845", "brand": "Tefal", "category": "steam iron"},
        "attributes": [
            {"name": "Потужність праски", "type": "number", "unit": "W",
             "check": lambda v, u: check_number(v, 3200)},
            {"name": "Об'єм резервуара для води", "type": "number", "unit": "l",
             "check": lambda v, u: check_number(v, 0.35, accept=(350,))},
        ],
    },
    {
        "key": "ariston",
        "product": {"name": "VLS EVO 50", "brand": "Ariston", "category": "water heater"},
        "attributes": [
            {"name": "Об'єм бака", "type": "number", "unit": "l",
             "check": lambda v, u: check_number(v, 50, accept=(45,))},
            # UA retail lists the EU SKU at 2.5 kW while the global VELIS EVO
            # datasheet says 1.5 kW — either extraction is correct.
            {"name": "Потужність нагріву", "type": "number", "unit": "W",
             "check": lambda v, u: check_number(v, 1500, accept=(1.5, 2000, 2500, 2.5))},
        ],
    },
]


# ── runner ───────────────────────────────────────────────────────────────────

def run(base: str, only: str | None, json_out: str | None, timeout: float) -> int:
    total = correct = found = 0
    rows: list[dict] = []
    t_start = time.time()

    for case in BENCHMARK:
        if only and case["key"] != only.lower():
            continue
        product = case["product"]
        payload_attrs = [
            {k: v for k, v in a.items() if k in ("name", "type", "unit", "allowed_values")}
            for a in case["attributes"]
        ]
        label = f'{product.get("brand", "")} {product["name"]}'
        print(f"\n=== {label} ===")
        t0 = time.time()
        try:
            resp = httpx.post(
                f"{base}/attributes",
                json={"product": product, "attributes": payload_attrs},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"  REQUEST FAILED: {exc}")
            for a in case["attributes"]:
                total += 1
                rows.append({"product": label, "attribute": a["name"], "ok": False,
                             "value": None, "status": "request_error"})
            continue
        elapsed = time.time() - t0

        results = {r["name"]: r for r in data.get("results", [])}
        for a in case["attributes"]:
            r = results.get(a["name"], {})
            value, unit = r.get("value"), r.get("unit")
            status = r.get("status", "missing")
            ok = bool(a["check"](value, unit)) if status == "found" or value else False
            total += 1
            correct += ok
            found += 1 if value else 0
            mark = "PASS" if ok else ("MISS" if not value else "FAIL")
            src = (r.get("source_url") or "")[:70]
            print(f"  {mark}: {a['name']!r} -> {value!r} {unit or ''} "
                  f"(conf={r.get('confidence')}, {src})")
            if mark == "FAIL":
                print(f"        raw: {(r.get('raw_value') or '')[:140]!r}")
            rows.append({"product": label, "attribute": a["name"], "ok": ok,
                         "value": value, "unit": unit, "status": status,
                         "raw_value": r.get("raw_value"),
                         "confidence": r.get("confidence"),
                         "source_url": r.get("source_url")})
        print(f"  [{elapsed:.1f}s]")

    print("\n" + "=" * 60)
    pct = 100.0 * correct / total if total else 0.0
    fpct = 100.0 * found / total if total else 0.0
    print(f"SCORE: {correct}/{total} correct = {pct:.1f}%   "
          f"(found any value: {found}/{total} = {fpct:.1f}%)   "
          f"total time {time.time() - t_start:.0f}s")

    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({"score_pct": pct, "correct": correct, "total": total,
                       "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"details -> {json_out}")

    return 0 if pct >= 80 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--only", default=None, help="run a single product key")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()
    sys.exit(run(args.base, args.only, args.json_out, args.timeout))
