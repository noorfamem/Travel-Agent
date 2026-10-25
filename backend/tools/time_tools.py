"""Timezone-aware date/time helpers. Offsets are never hard-coded: everything comes from the IANA database."""
from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Display-only abbreviations for zones whose tz database abbreviation is numeric (offsets always come from the tz database).
ABBR = {"Asia/Kuala_Lumpur": "MYT", "Asia/Singapore": "SGT", "Asia/Dubai": "GST", "Asia/Bangkok": "ICT", "Asia/Ho_Chi_Minh": "ICT", "Asia/Jakarta": "WIB",
        "Asia/Makassar": "WITA", "Asia/Manila": "PHT", "Asia/Hong_Kong": "HKT", "Asia/Shanghai": "CST", "Asia/Tokyo": "JST", "Asia/Seoul": "KST",
        "Asia/Qatar": "AST", "Asia/Riyadh": "AST", "Asia/Colombo": "SLST", "Asia/Kathmandu": "NPT", "Asia/Dhaka": "BST", "Asia/Taipei": "CST"}


@lru_cache(maxsize=256)
def get_tz(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def localize(local_text: str, tz_name: str) -> dt.datetime | None:
    """'2026-11-15 22:30' (wall-clock time at an airport) + 'Asia/Kolkata' -> aware datetime. None if unparsable."""
    tz = get_tz(tz_name)
    if tz is None or not local_text:
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", local_text.strip())
    if not m:
        return None
    y, mo, d, h, mi = map(int, m.groups())
    try:
        return dt.datetime(y, mo, d, h, mi, tzinfo=tz)
    except ValueError:
        return None


def minutes_between(start: dt.datetime, end: dt.datetime) -> int:
    """Real elapsed minutes between two aware datetimes in ANY zones."""
    return int(round((end - start).total_seconds() / 60))


def day_offset(dep: dt.datetime, arr: dt.datetime) -> int:
    """Calendar-day difference between local departure date and local arrival date (the '+1 day' badge)."""
    return (arr.date() - dep.date()).days


def fmt_duration(minutes: int | None) -> str:
    if minutes is None:
        return "Information unavailable"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def tz_label(when: dt.datetime) -> str:
    key = getattr(when.tzinfo, "key", None)
    if key in ABBR:
        return ABBR[key]
    abbr = when.tzname() or ""
    if not abbr or abbr[0] in "+-" or abbr.startswith("UTC"):
        off = when.utcoffset() or dt.timedelta(0)
        total = int(off.total_seconds() // 60)
        return f"UTC{'+' if total >= 0 else '-'}{abs(total) // 60:02d}:{abs(total) % 60:02d}"
    return abbr


def fmt_local(when: dt.datetime | None) -> str:
    if when is None:
        return "Information unavailable"
    return f"{when.day} {when:%b %Y}, {when:%I:%M %p} {tz_label(when)}"


def parse_hhmm(text: str) -> dt.time | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(am|pm)?\s*$", text or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return dt.time(h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None
