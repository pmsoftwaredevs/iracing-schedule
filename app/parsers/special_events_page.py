"""Deterministic parser: extracts special events directly from the real
iracing.com/special-events HTML page (public, no login) — not the "Download
Schedule" PNG graphic. Dates, track links, and car-class labels are all real markup
here, so this is far simpler and more reliable than OCR-ing the PNG.

Each event is a `<section id="{slug}">` containing an `<h2>` name, a `<p>` with a
date range (formats seen: "February 20-22, 2026", "June 30 – July 6, 2026"
(cross-month, en-dash), "January 9-10, 2026" (single day = no second day), or
"Date : TBD" for an event without an announced date yet — skipped rather than
guessed). Track name comes from the first `<a href="/tracks/...">` link in the
section if present (not every event links one). Car class/era comes from the first
`<details><summary>` if present (e.g. "1987 NASCAR Cup" for the Firecracker 400).
"""

import re
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

DATE_RE = re.compile(
    r"([A-Za-z]+)\.?\s+(\d{1,2})"                    # month1 day1
    r"(?:\s*[-–]\s*(?:([A-Za-z]+)\.?\s+)?(\d{1,2}))?"  # optional -/– [month2] day2
    r",\s*(\d{4})",                                  # , year
    re.IGNORECASE,
)

SLUG_RE = re.compile(r"^[a-z0-9-]+$")
TRACK_LINK_RE = re.compile(r"^/tracks/")


@dataclass
class ParsedSpecialEvent:
    slug: str
    name: str
    date_start: datetime
    date_end: datetime
    track_name: str | None
    track_path: str | None
    car_class: str | None


class SpecialEventsPageParseError(ValueError):
    pass


def _parse_date_range(text: str) -> tuple[datetime, datetime] | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    month1_name, day1, month2_name, day2, year_text = match.groups()
    month1 = MONTHS.get(month1_name.lower())
    if month1 is None:
        return None
    year = int(year_text)
    try:
        date_start = datetime(year, month1, int(day1))
    except ValueError:
        return None

    if day2 is None:
        return date_start, date_start

    month2 = MONTHS.get(month2_name.lower()) if month2_name else month1
    if month2 is None:
        return None
    end_year = year if month2 >= month1 else year + 1  # e.g. Dec 30 - Jan 2
    try:
        date_end = datetime(end_year, month2, int(day2))
    except ValueError:
        return None
    return date_start, date_end


def _parse_section(section) -> ParsedSpecialEvent | None:
    slug = section.get("id")
    if not slug or not SLUG_RE.match(slug):
        return None
    heading = section.find("h2")
    if heading is None:
        return None
    name = heading.get_text(strip=True)
    if not name:
        return None

    dates = None
    for p in section.find_all("p", limit=6):
        dates = _parse_date_range(p.get_text(strip=True))
        if dates is not None:
            break
    if dates is None:
        return None  # e.g. "Date : TBD" — no usable date yet, don't guess

    track_link = section.find("a", href=TRACK_LINK_RE)
    track_name = track_link.get_text(strip=True) if track_link else None
    track_path = track_link["href"] if track_link else None

    summary = section.find("summary")
    car_class = summary.get_text(strip=True) if summary else None

    return ParsedSpecialEvent(
        slug=slug,
        name=name,
        date_start=dates[0],
        date_end=dates[1],
        track_name=track_name,
        track_path=track_path,
        car_class=car_class,
    )


def parse_special_events_page(html: str) -> list[ParsedSpecialEvent]:
    soup = BeautifulSoup(html, "html.parser")
    events = [
        parsed
        for section in soup.find_all("section")
        if (parsed := _parse_section(section)) is not None
    ]
    if not events:
        raise SpecialEventsPageParseError("No special events found — check page layout")
    return events
