"""Safe recording names from the small, user-facing template vocabulary."""
from __future__ import annotations

import math
import re
from datetime import datetime
from string import Formatter
from urllib.parse import urlsplit


DEFAULT_RECORDING_TEMPLATE = "{name}-{date}-{duration}"
ATTRIBUTES = frozenset(("name", "date", "date_iso", "time", "time_end", "duration", "длительность"))
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", re.IGNORECASE)


def _valid_stem(value: str) -> bool:
    return bool(value and value.strip(" .") and not value.endswith((".", " "))
                and not _FORBIDDEN.search(value) and not _RESERVED.fullmatch(value)
                and len(value.encode("utf-16-le", errors="replace")) // 2 <= 220)


def meeting_identifier(url: str) -> str:
    parsed = urlsplit(url if "://" in url else "https://" + url)
    match = re.fullmatch(r"/j/([A-Za-z0-9_-]+)/?", parsed.path)
    identifier = match.group(1) if match and parsed.hostname == "telemost.yandex.ru" else "meeting"
    return identifier if _valid_stem(identifier) else "meeting-" + identifier


def recording_stem(template: str, *, name: str, url: str, started_at: datetime,
                   ended_at: datetime, duration_seconds: float | None) -> dict:
    identifier = meeting_identifier(url)
    normalized_name = re.sub(r"\s+", "_", name.strip())
    name_fallback = bool(_FORBIDDEN.search(name)) or not _valid_stem(normalized_name)
    values = {
        "name": identifier if name_fallback else normalized_name,
        "date": started_at.strftime("%d%m%y"), "date_iso": started_at.strftime("%Y-%m-%d"),
        "time": started_at.strftime("%H-%M"), "time_end": ended_at.strftime("%H-%M"),
        "duration": str(math.floor(duration_seconds / 60)) if duration_seconds is not None else None,
    }
    values["длительность"] = values["duration"]
    try:
        fragments = []
        for literal, field, spec, conversion in Formatter().parse(template):
            fragments.append(literal)
            if field is not None:
                if field not in ATTRIBUTES or spec or conversion or values[field] is None:
                    raise ValueError("Unsupported filename attribute")
                fragments.append(values[field])
        stem = "".join(fragments).strip()
        if stem.lower().endswith(".mkv"):
            stem = stem[:-4]
        if not _valid_stem(stem) or "{" in stem or "}" in stem:
            raise ValueError("Invalid filename")
        return {"stem": stem, "fallback": name_fallback}
    except (ValueError, KeyError, TypeError):
        return {"stem": identifier, "fallback": True}
