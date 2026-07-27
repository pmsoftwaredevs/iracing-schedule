"""Deterministic parser: turns a series' "Races ..." cadence line (from the
schedule PDF header, see app/parsers/schedule_pdf.py) into the full set of GMT
times-of-day sessions actually run at, per day of week. This is what constrains
the timeslot picker to a dropdown of real options instead of a free-form time
input — e.g. "Races every even 2 hours at :30 past" -> every day at 00:30, 02:30,
04:30, ..., 22:30 GMT; "Races Thur & Sat at 10, 19 GMT & Fri & Sun at 1,4 GMT" ->
Thu/Sat at 10:00 & 19:00, Fri/Sun at 01:00 & 04:00, no sessions Mon/Tue/Wed.

Verified against every distinct cadence phrasing found in the real 2026 S3 official
schedule PDF — 54 unique strings across 152 series (see tests/test_race_cadence.py)
— including day-specific patterns, "every other <day>" biweekly ones (e.g. IMSA
Michelin Pilot Challenge), and assorted wording variants: "hourly" as a synonym for
"every hour", the offset stated before the interval ("Races 45 past every 2
hours"), plural day names ("Saturdays"), "and"/no separator instead of "&", a
missing "at" between day and time, and a bare "Races at :15 and :45" with no
interval keyword at all (treated as implicitly hourly).

The biweekly wording doesn't need any special handling here: which *weeks* a
session actually falls in is already implicit in the schedule PDF's own week rows
(a biweekly series simply has no ScheduleWeek row for its off-weeks), so this
module only needs to capture which days and times sessions run on, not the week
cadence.
"""

import re
from dataclasses import dataclass
from datetime import time

_UNIT_RE = re.compile(
    r"every\s+(?:(?P<phase>even|odd)\s+)?"
    r"(?:(?P<count>\d+|thirty)\s+(?P<unit>hours?|minutes?)|(?P<bare_hour>hour)\b)"
    r"|(?P<hourly>hourly)\b",
    re.IGNORECASE,
)
# Offset marks (minutes-past-the-hour) — searched independently of _UNIT_RE
# anywhere in the text, since real phrasings put them either before or after the
# interval spec (e.g. "Races 45 past every 2 hours" vs "...every 2 hours at :45").
OFFSET_RE = re.compile(
    r"(?::(\d{2})|[&,]\s*(\d{1,2})\b|\band\s+:?(\d{1,2})\b|\b(\d{1,2})\s+past\b)"
)

_DAY_WORD_RE = re.compile(
    r"\b(mon(?:day)?s?|tue(?:s(?:day)?)?s?|wed(?:s|nesday)?s?|thu(?:r|rs|rsday)?s?|"
    r"fri(?:day)?s?|sat(?:urday)?s?|sun(?:day)?s?)\b",
    re.IGNORECASE,
)
_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_TIME_ITEM = r"\d{1,2}(?::\d{2})?\s*(?:GMT)?"
# Days and times within a clause can be joined by "&", ",", or "and" — e.g.
# "Wednesday and Saturdays at 14 and 19 GMT" joins two days AND two times this way.
_JOIN = r"(?:&|,|and)"
_CLAUSE_RE = re.compile(
    rf"(?P<days>{_DAY_WORD_RE.pattern}(?:\s*{_JOIN}\s*{_DAY_WORD_RE.pattern})*)"
    rf"\s+(?:at\s+)?(?P<times>{_TIME_ITEM}(?:\s*{_JOIN}\s*{_TIME_ITEM})*)",
    re.IGNORECASE,
)
_TIME_NUM_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?")
_COLON_OFFSET_RE = re.compile(r":(\d{2})\b")


@dataclass
class DayCadence:
    """Per-weekday GMT session start times (0=Monday..6=Sunday, matching
    Timeslot.day_of_week) — a day absent from the mapping has no sessions at all."""

    times_by_day: dict[int, list[time]]

    def flat_times(self) -> list[time]:
        """All distinct times across every day, sorted — used where the caller
        doesn't care which day a time belongs to (e.g. a rough duration estimate)."""
        return sorted({t for times in self.times_by_day.values() for t in times})


def _parse_day_specific(text: str) -> DayCadence | None:
    times_by_day: dict[int, set[time]] = {}
    for clause in _CLAUSE_RE.finditer(text):
        days = [_DAY_INDEX[m.group(1)[:3].lower()] for m in _DAY_WORD_RE.finditer(clause.group("days"))]
        times = [
            time(int(m.group(1)), int(m.group(2) or 0))
            for m in _TIME_NUM_RE.finditer(clause.group("times"))
        ]
        if not days or not times:
            continue
        for day in days:
            times_by_day.setdefault(day, set()).update(times)
    if not times_by_day:
        return None
    return DayCadence({day: sorted(times) for day, times in times_by_day.items()})


def _parse_interval(text: str) -> DayCadence | None:
    """Regular "every N hours/minutes" or "hourly" cadences that aren't
    day-specific — the same times apply every day of the week."""
    match = _UNIT_RE.search(text)
    if not match:
        return None
    if match.group("hourly") or match.group("bare_hour"):
        unit, count = "hours", 1
    else:
        unit = "hours" if match.group("unit").lower().startswith("hour") else "minutes"
        count_word = match.group("count")
        count = 30 if count_word.lower() == "thirty" else int(count_word)
    phase = match.group("phase")

    remainder = text.lower()
    offsets: set[int] = set()
    if "on the hour" in remainder or "top of the hour" in remainder or "on the 00" in remainder:
        offsets.add(0)
    if "half past" in remainder:
        offsets.add(30)
    for offset_match in OFFSET_RE.finditer(text):
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

    times = sorted(time(hour, minute) for hour in range(start_hour, 24, step) for minute in offsets)
    return DayCadence({day: times for day in range(7)})


def _parse_implicit_hourly(text: str) -> DayCadence | None:
    """A cadence line with explicit ":NN" offset marks but no interval keyword at
    all (e.g. "Races at :15 and :45", unlike "Races every 30 minutes at :15 and
    :45" elsewhere in the same PDF) is treated as implicitly hourly — the offsets
    just repeat every hour."""
    offsets = {int(m.group(1)) for m in _COLON_OFFSET_RE.finditer(text)}
    if not offsets:
        return None
    times = sorted(time(hour, minute) for hour in range(24) for minute in offsets)
    return DayCadence({day: times for day in range(7)})


def parse_cadence(text: str) -> DayCadence | None:
    return _parse_day_specific(text) or _parse_interval(text) or _parse_implicit_hourly(text)
