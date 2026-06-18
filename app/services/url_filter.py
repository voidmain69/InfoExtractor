from __future__ import annotations

import re
from urllib.parse import urlparse


def url_matches_domain(url: str, domain: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        host = re.sub(r"^www\.", "", host.lower())
        return host == domain or host.endswith("." + domain)
    except Exception:
        return False
