"""Curated brand → official manufacturer domains.

Deterministic first step for official-site resolution and source ranking: the
LLM/search resolution is a fallback for unknown brands, but for the brands the
PIM actually carries, a table is exact, instant, and works when the LLM host is
down. The first domain is the primary global site; the rest are regional or
line-of-business sites that also count as official."""
from __future__ import annotations

import re

_BRAND_DOMAINS: dict[str, list[str]] = {
    # home appliances
    "ariston": ["ariston.com", "hotpoint.eu"],
    "hotpoint": ["hotpoint.eu", "ariston.com"],
    "hotpoint-ariston": ["hotpoint.eu", "ariston.com"],
    "beko": ["beko.com", "beko.ua"],
    "bosch": ["bosch-home.com", "bosch-home.com.ua", "bosch.ua",
              "bosch-professional.com", "bosch-diy.com"],
    "siemens": ["siemens-home.bsh-group.com"],
    "electrolux": ["electrolux.ua", "electrolux.com"],
    "aeg": ["aeg.ua", "aeg.com"],
    "zanussi": ["zanussi.ua", "zanussi.com"],
    "whirlpool": ["whirlpool.ua", "whirlpool.com"],
    "indesit": ["indesit.ua", "indesit.com"],
    "candy": ["candy-home.com", "candy.ua"],
    "gorenje": ["gorenje.ua", "gorenje.com"],
    "samsung": ["samsung.com"],
    "lg": ["lg.com"],
    "miele": ["miele.ua", "miele.com"],
    "haier": ["haier.ua", "haier.com"],
    "hisense": ["hisense.ua", "hisense.com"],
    "vestfrost": ["vestfrost.ua", "vestfrost.com"],
    "atlant": ["atlant.ua"],
    "liebherr": ["liebherr.com"],
    # small appliances / personal care
    "philips": ["philips.ua", "philips.com", "usa.philips.com"],
    "braun": ["braunhousehold.com", "braun.com", "braun.ua"],
    "tefal": ["tefal.ua", "tefal.com"],
    "rowenta": ["rowenta.ua", "rowenta.com"],
    "moulinex": ["moulinex.ua", "moulinex.com"],
    "delonghi": ["delonghi.com", "delonghi.ua"],
    "de'longhi": ["delonghi.com", "delonghi.ua"],
    "kenwood": ["kenwoodworld.com"],
    "russell hobbs": ["russellhobbs.com"],
    "dyson": ["dyson.com", "dyson.com.ua"],
    "xiaomi": ["mi.com", "xiaomi.ua", "xiaomi.com"],
    "sencor": ["sencor.ua", "sencor.com"],
    "vitek": ["vitek.ua"],
    "scarlett": ["scarlett.ru", "scarlett-europe.com"],
    "zelmer": ["zelmer.ua", "zelmer.com"],
    "gaggia": ["gaggia.com"],
    "krups": ["krups.ua", "krups.com"],
    # cleaning / garden / tools
    "karcher": ["kaercher.com", "karcher.ua"],
    "kärcher": ["kaercher.com", "karcher.ua"],
    "makita": ["makita.ua", "makita.com"],
    "metabo": ["metabo.com", "metabo.ua"],
    "dewalt": ["dewalt.ua", "dewalt.com"],
    "einhell": ["einhell.ua", "einhell.com"],
    "gardena": ["gardena.com"],
    "husqvarna": ["husqvarna.com"],
    "stihl": ["stihl.ua", "stihl.com"],
    # printers / office
    "brother": ["brother.ua", "brother.com", "brother-usa.com", "brother.eu"],
    "canon": ["canon.ua", "canon.com", "usa.canon.com"],
    "epson": ["epson.ua", "epson.com"],
    "hp": ["hp.com"],
    "kyocera": ["kyoceradocumentsolutions.com"],
    "xerox": ["xerox.com", "xerox.ua"],
    "pantum": ["pantum.com", "pantum.ua"],
    "ricoh": ["ricoh.com"],
    # IT / components
    "asus": ["asus.com"],
    "gigabyte": ["gigabyte.com"],
    "msi": ["msi.com"],
    "asrock": ["asrock.com"],
    "intel": ["intel.com", "ark.intel.com"],
    "amd": ["amd.com"],
    "nvidia": ["nvidia.com"],
    "kingston": ["kingston.com"],
    "corsair": ["corsair.com"],
    "seagate": ["seagate.com"],
    "wd": ["westerndigital.com"],
    "western digital": ["westerndigital.com"],
    "logitech": ["logitech.com"],
    "acer": ["acer.com"],
    "dell": ["dell.com"],
    "lenovo": ["lenovo.com"],
    "apple": ["apple.com"],
    "2e": ["2e.ua", "2egaming.com", "2e-gaming.com"],
    "2e gaming": ["2egaming.com", "2e.ua"],
    "aoc": ["aoc.com"],
    "benq": ["benq.com"],
    "viewsonic": ["viewsonic.com"],
    "sony": ["sony.ua", "sony.com"],
    "panasonic": ["panasonic.com"],
    "tcl": ["tcl.com"],
    "realme": ["realme.com"],
    "oppo": ["oppo.com"],
    "oneplus": ["oneplus.com"],
    "google": ["store.google.com"],
    "motorola": ["motorola.com"],
    "nokia": ["nokia.com"],
    "huawei": ["huawei.com", "consumer.huawei.com"],
    "honor": ["honor.com"],
}

_NORM_RE = re.compile(r"[^\w\s'-]", re.UNICODE)

# Cyrillic brand spellings seen in feeds → latin key.
_TRANSLIT = {
    "філіпс": "philips", "филипс": "philips",
    "бош": "bosch", "самсунг": "samsung",
    "керхер": "karcher", "кархер": "karcher",
    "тефаль": "tefal", "браун": "braun",
    "аристон": "ariston", "беко": "beko",
}


def _norm_brand(brand: str) -> str:
    s = _NORM_RE.sub(" ", brand.lower()).strip()
    s = re.sub(r"\s+", " ", s)
    return _TRANSLIT.get(s, s)


def official_domains(brand: str | None) -> list[str]:
    """All official domains for a brand ('' when unknown), primary first."""
    if not brand:
        return []
    key = _norm_brand(brand)
    if key in _BRAND_DOMAINS:
        return _BRAND_DOMAINS[key]
    # "2E GAMING" → try the first word too.
    first = key.split(" ")[0] if " " in key else None
    if first and first in _BRAND_DOMAINS:
        return _BRAND_DOMAINS[first]
    return []


def primary_domain(brand: str | None) -> str | None:
    domains = official_domains(brand)
    return domains[0] if domains else None
