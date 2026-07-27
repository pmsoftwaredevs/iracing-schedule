"""Deterministic parser: turns a series' "Races every ..." cadence line (from the
schedule PDF header, see app/parsers/schedule_pdf.py) into the full list of GMT
times-of-day sessions actually run at. This is what constrains the timeslot picker
to a dropdown of real options instead of a free-form time input — e.g. "Races every
even 2 hours at :30 past" -> 00:30, 02:30, 04:30, ..., 22:30 GMT.

Verified against every distinct cadence phrasing found in the real 2026 S3 official
schedule PDF (31 patterns; see tests/test_race_cadence.py). A handful of irregular,
day-specific patterns ("Races every other Saturday at 2, 7, 18 GMT and Sunday at 14
GMT") don't fit a simple daily-repeat model at all and correctly return None rather
than a guessed result — callers should fall back to a coarse default in that case.
"""

import re
from datetime import time

INTERVAL_RE = re.compile(
    r"races every "
    r"(?:(even|odd)\s+)?"
    r"(?:(\d+|thirty)\s+(hours?|minutes?)|(hour)\b)",
    re.IGNORECASE,
)
OFFSET_RE = re.compile(r"(?::(\d{2})|[&,]\s*(\d{1,2})\b|\band\s+:?(\d{1,2})\b)")


def parse_cadence(text: str) -> list[time] | None:
    match = INTERVAL_RE.search(text)
    if not match:
        return None
    phase, count_word, unit_word, bare_hour = match.groups()
    if bare_hour:
        unit, count = "hours", 1
    else:
        unit = "hours" if unit_word.lower().startswith("hour") else "minutes"
        count = 30 if count_word.lower() == "thirty" else int(count_word)

    remainder = text[match.end():].lower()
    offsets: set[int] = set()
    if "on the hour" in remainder:
        offsets.add(0)
    if "half past" in remainder:
        offsets.add(30)
    for offset_match in OFFSET_RE.finditer(remainder):
        offsets.add(int(next(g for g in offset_match.groups() if g is not None)))

    if unit == "hours":
        if not offsets:
            offsets = {0}
        start_hour = 1 if phase == "odd" else 0
        step = count
    else:
        if not offsets:
            offsets = set(range(0, 60, count))
        start_hour = 0
        step = 1

    return sorted(time(hour, minute) for hour in range(start_hour, 24, step) for minute in offsets)
