"""Curated synonym groups for spec attribute labels.

A deterministic pre-pass before the LLM semantic matcher: small models miss
obvious equivalences ("Response time" vs "Reaction time") inconsistently, and
many retail pages use machine-translated labels ("Update frequency" for refresh
rate, "Type of matrix" for panel type). Resolving the well-known ones from a
table is reliable and free; the LLM only handles what isn't covered here.

Groups span the PIM's product domains: displays, PC components, large & small
home appliances, garden/cleaning tools, printers — in en/uk/ru, because the
taxonomy names arrive in any of the three and the source pages likewise."""
from __future__ import annotations

import re

# Each group lists equivalent labels (any language) for one characteristic.
_GROUPS: list[set[str]] = [
    # ── displays / monitors ─────────────────────────────────────────────
    {"refresh rate", "update frequency", "refresh frequency", "frame rate",
     "vertical frequency", "max refresh rate",
     "частота оновлення", "частота оновлення екрана", "частота розгортки",
     "частота обновления", "частота кадрів"},
    {"response time", "reaction time", "gray to gray", "gtg", "grey to grey",
     "response time gtg", "час відгуку", "время отклика", "швидкість відгуку"},
    {"panel type", "type of matrix", "matrix type", "matrix", "panel",
     "display type", "screen type", "тип матриці", "тип матрицы", "тип екрану",
     "тип дисплея", "матриця", "матрица"},
    {"curvature", "curved screen", "curve", "curvature radius", "screen curvature",
     "кривизна", "радіус кривизни", "вигнутий екран", "изогнутый экран"},
    {"resolution", "recommended resolution", "native resolution", "max resolution",
     "maximum resolution", "screen resolution", "display resolution",
     "роздільна здатність", "роздільна здатність екрана", "разрешение",
     "разрешение экрана"},
    {"brightness", "bright", "luminance", "max brightness", "peak brightness",
     "яскравість", "яркость"},
    {"contrast", "contrast ratio", "static contrast", "dynamic contrast",
     "контраст", "контрастність", "контрастность"},
    {"aspect ratio", "support for parties", "співвідношення сторін",
     "соотношение сторон", "формат екрана"},
    {"viewing angle", "viewing angles", "vertical viewing angle",
     "horizontal viewing angle", "кути огляду", "углы обзора", "кут огляду"},
    {"screen size", "screen diagonal", "diagonal", "display size", "panel size",
     "display diagonal", "діагональ", "діагональ екрана", "диагональ",
     "диагональ экрана", "розмір екрана"},
    {"color gamut", "colour gamut", "колірне охоплення", "цветовой охват"},
    {"color depth", "colour depth", "bit depth", "number of colors",
     "глибина кольору", "кількість кольорів"},

    # ── PC / components ─────────────────────────────────────────────────
    # NOTE: motherboard pages often label the whole RAM section just "Memory";
    # the shared phrase links it to both the slots and the capacity attribute
    # (multi-group phrases are supported).
    {"memory slots", "dimm slots", "ram slots", "memory dimm", "number of dimm",
     "memory channels", "слоти пам'яті", "слоты памяти", "memory",
     "memory subsystem", "підсистема пам'яті", "подсистема памяти",
     "оперативна пам'ять", "оперативная память"},
    {"max memory", "maximum memory", "memory capacity", "max. memory",
     "supported memory", "total memory", "максимальна пам'ять",
     "максимальний обсяг пам'яті", "максимальный объем памяти",
     "обсяг пам'яті", "объем памяти", "memory",
     "memory subsystem", "підсистема пам'яті", "подсистема памяти",
     "оперативна пам'ять", "оперативная память"},
    {"memory type", "supported memory type", "ram type", "тип пам'яті",
     "тип памяти"},
    {"form factor", "форм-фактор", "форм фактор", "формфактор"},
    {"socket", "cpu socket", "processor socket", "сокет", "socket type",
     "роз'єм процесора"},
    {"chipset", "чіпсет", "чипсет"},
    {"tdp", "thermal design power", "теплопакет"},

    # ── connectivity / ports ────────────────────────────────────────────
    {"connectors", "ports", "inputs", "interfaces", "connectivity",
     "connection", "interface", "роз'єми", "разъемы", "інтерфейси",
     "интерфейсы", "підключення", "подключение"},
    {"usb ports", "usb", "number of usb ports", "порти usb", "порты usb"},
    {"hdmi", "hdmi ports", "hdmi inputs", "порти hdmi"},
    {"wifi", "wi-fi", "wireless", "wireless lan", "wlan", "бездротова мережа",
     "беспроводная связь"},
    {"bluetooth", "блютуз"},

    # ── physical ────────────────────────────────────────────────────────
    {"weight", "gross weight", "net weight", "the weight", "product weight",
     "вага", "маса", "вес", "вага нетто", "вес нетто"},
    {"dimensions", "size", "dimensions (wxhxd)", "product dimensions",
     "габарити", "розміри", "габаритні розміри", "габариты", "размеры"},
    {"width", "ширина"},
    {"height", "висота", "высота"},
    {"depth", "глибина", "глубина"},
    {"color", "colour", "body color", "колір", "цвет", "колір корпусу",
     "цвет корпуса"},
    {"material", "body material", "матеріал", "материал", "матеріал корпусу"},
    {"cord length", "cable length", "power cord length", "довжина шнура",
     "довжина кабелю", "длина шнура", "длина кабеля"},

    # ── power / electrical ──────────────────────────────────────────────
    {"power consumption", "power", "energy consumption", "rated power",
     "power rating", "wattage", "input power", "connected load",
     "споживання", "споживана потужність", "энергопотребление",
     "потужність", "мощность", "номінальна потужність",
     "номинальная мощность", "споживання енергії"},
    {"voltage", "supply voltage", "rated voltage", "напруга", "напряжение",
     "живлення", "питание"},
    {"energy class", "energy efficiency class", "energy rating",
     "energy efficiency", "клас енергоспоживання", "клас енергоефективності",
     "класс энергопотребления", "класс энергоэффективности"},
    {"battery capacity", "battery", "ємність акумулятора", "акумулятор",
     "емкость аккумулятора", "батарея"},
    {"battery life", "runtime", "operating time", "час роботи",
     "час автономної роботи", "время работы"},

    # ── noise ───────────────────────────────────────────────────────────
    {"noise level", "noise", "sound level", "sound power", "acoustic noise",
     "noise emission", "рівень шуму", "шум", "уровень шума",
     "гучність", "гучність роботи"},

    # ── washing machines / dryers ───────────────────────────────────────
    {"spin speed", "max spin speed", "maximum spin speed", "spin rate",
     "spin-drying speed", "spinning speed", "обороти віджиму",
     "швидкість віджиму", "швидкість обертання", "оберти віджиму",
     "скорость отжима", "обороты отжима", "віджим"},
    {"load capacity", "washing capacity", "wash load", "max load",
     "capacity (kg)", "drum capacity", "laundry capacity", "load", "maximum load",
     "завантаження", "максимальне завантаження", "обсяг завантаження",
     "загрузка", "максимальная загрузка", "вместимость",
     "завантаження білизни", "загрузка белья"},
    # NOTE: deliberately no bare "programs"/"програми" — pages use that label
    # for the program-name LIST, which must not answer a count attribute.
    {"number of programs", "wash programs", "number of programmes",
     "кількість програм", "количество программ"},
    {"drum volume", "об'єм барабана", "объем барабана"},
    {"spin class", "spin-drying efficiency class", "клас віджиму",
     "класс отжима", "клас ефективності віджиму"},
    {"washing class", "wash performance class", "клас прання", "класс стирки"},
    {"installation type", "installation", "type of installation", "built-in",
     "тип установки", "встановлення", "тип встановлення", "установка"},

    # ── refrigerators / freezers ────────────────────────────────────────
    {"total volume", "total capacity", "gross volume", "net volume",
     "useful volume", "capacity", "volume", "загальний об'єм",
     "корисний об'єм", "об'єм", "общий объем", "полезный объем", "объем",
     "місткість"},
    {"freezer volume", "freezer capacity", "об'єм морозильної камери",
     "объем морозильной камеры"},
    {"refrigerator volume", "fridge volume", "об'єм холодильної камери",
     "объем холодильной камеры"},
    {"defrost type", "defrosting", "no frost", "тип розморожування",
     "розморожування", "тип разморозки", "разморозка"},
    {"climate class", "кліматичний клас", "климатический класс"},
    {"compressor type", "compressor", "тип компресора", "тип компрессора"},

    # ── kitchen / small appliances ──────────────────────────────────────
    {"bowl volume", "bowl capacity", "jug capacity", "jar capacity",
     "об'єм чаші", "объем чаши", "місткість чаші"},
    {"water tank", "water tank capacity", "tank capacity", "reservoir capacity",
     "water container", "об'єм резервуара", "об'єм резервуара для води",
     "резервуар для води", "объем резервуара", "емкость для воды",
     "об'єм бака", "объем бака"},
    {"pump pressure", "pressure", "bar pressure", "working pressure",
     "max pressure", "тиск", "тиск помпи", "робочий тиск", "давление",
     "давление помпы", "рабочее давление", "макс. тиск"},
    {"coffee type", "coffee used", "тип кави", "тип кофе",
     "використовувана кава"},
    {"grinder", "coffee grinder", "built-in grinder", "кавомолка",
     "вбудована кавомолка", "кофемолка"},
    {"steam boost", "steam shot", "turbo steam", "паровий удар",
     "паровой удар", "подача пари", "додаткова подача пари"},
    {"continuous steam", "steam output", "steam rate", "постійна пара",
     "постійна подача пари", "постоянный пар", "подача пара"},
    {"soleplate", "soleplate type", "soleplate material", "підошва",
     "матеріал підошви", "подошва", "материал подошвы"},
    {"number of speeds", "speed settings", "speeds", "кількість швидкостей",
     "количество скоростей", "швидкості"},
    {"blade material", "матеріал ножів", "матеріал лез", "материал ножей"},

    # ── vacuum / cleaning / garden ──────────────────────────────────────
    {"suction power", "suction", "потужність всмоктування",
     "мощность всасывания", "сила всмоктування"},
    {"dust container", "dust capacity", "dustbin capacity", "bag capacity",
     "об'єм пилозбірника", "пилозбірник", "объем пылесборника"},
    {"max water flow", "water flow", "flow rate", "продуктивність",
     "витрата води", "подача води", "производительность", "расход воды"},
    {"max pressure washer", "operating pressure", "макс. давление"},
    {"hose length", "довжина шланга", "длина шланга"},
    {"area performance", "cleaning area", "area", "площа прибирання",
     "площадь уборки", "оброблювана площа"},

    # ── water heaters / climate ─────────────────────────────────────────
    {"tank volume", "boiler volume", "water heater capacity",
     "об'єм бойлера", "об'єм водонагрівача", "объем водонагревателя"},
    {"heating time", "час нагріву", "час нагрівання", "время нагрева"},
    {"max temperature", "maximum temperature", "temperature range",
     "макс. температура", "максимальна температура",
     "максимальная температура", "діапазон температур"},
    {"inner tank coating", "tank material", "внутрішнє покриття бака",
     "покриття бака", "материал бака", "покрытие бака"},

    # ── printers / office ───────────────────────────────────────────────
    {"print speed", "printing speed", "print speed black", "mono print speed",
     "швидкість друку", "скорость печати", "швидкість чорно-білого друку"},
    {"print resolution", "printing resolution", "resolution (print)",
     "роздільна здатність друку", "разрешение печати"},
    {"print technology", "printing technology", "printing method",
     "технологія друку", "технология печати", "тип друку", "тип печати"},
    {"duplex", "duplex printing", "two-sided printing", "double-sided printing",
     "automatic duplex", "2-sided printing", "двосторонній друк",
     "двобічний друк", "двусторонняя печать",
     "автоматичний двосторонній друк"},
    {"paper capacity", "paper tray capacity", "input tray", "tray capacity",
     "paper input", "standard tray", "лоток для паперу", "ємність лотка",
     "емкость лотка", "місткість лотка", "вхідний лоток", "лоток подачі"},
    {"monthly duty cycle", "duty cycle", "місячне навантаження",
     "рекомендований місячний ресурс", "месячная нагрузка"},

    # ── audio / cameras / misc ──────────────────────────────────────────
    {"warranty", "warranty period", "guarantee", "гарантія", "гарантийный срок",
     "гарантія виробника"},
    {"country of origin", "made in", "країна виробник", "країна виробництва",
     "страна производитель"},
    {"speaker power", "audio output", "output power", "потужність динаміків",
     "вихідна потужність", "мощность динамиков"},
    {"camera resolution", "megapixels", "роздільна здатність камери",
     "мегапікселі", "разрешение камеры"},
]

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Tokens too generic to identify a group on their own via token-subset matching
# ("type", "max", "capacity" appear inside many unrelated labels).
_WEAK_TOKENS = frozenset({
    "type", "max", "maximum", "min", "number", "of", "the", "for",
    "тип", "макс", "максимальна", "максимальный", "кількість", "количество",
})


def _norm(label: str) -> str:
    s = _PUNCT_RE.sub(" ", label.lower())
    return _WS_RE.sub(" ", s).strip()


# Precompute a phrase → group-index map for O(1) exact lookups. A phrase may
# belong to several groups (e.g. "Memory" covers both slots and capacity).
_PHRASE_TO_GROUPS: dict[str, set[int]] = {}
for _i, _grp in enumerate(_GROUPS):
    for _phrase in _grp:
        _PHRASE_TO_GROUPS.setdefault(_norm(_phrase), set()).add(_i)


def _groups_of(phrase: str) -> set[int]:
    norm = _norm(phrase)
    exact = _PHRASE_TO_GROUPS.get(norm)
    if exact:
        return exact
    # Token-subset tolerance: "vertical viewing angle" → viewing-angle group.
    # Weak generic tokens alone must not link ("Type" ⊄ every "type of …").
    tokens = set(norm.split())
    out: set[int] = set()
    for known, idxs in _PHRASE_TO_GROUPS.items():
        kt = set(known.split())
        if not kt:
            continue
        smaller = kt if kt <= tokens else (tokens if tokens <= kt else None)
        if smaller is None:
            continue
        if smaller - _WEAK_TOKENS:
            out |= idxs
    return out


def find_synonym_label(attribute: str, labels: list[str]) -> str | None:
    """Return the page label that is a known synonym of `attribute`, or None."""
    targets = _groups_of(attribute)
    if not targets:
        return None
    for label in labels:
        if _groups_of(label) & targets:
            return label
    return None
