"""Deterministic parser: extracts season start dates from iracing.com/seasons (public,
no login). Used to know when a new season has started, so tools/build_cache.py can
trigger a schedule re-fetch instead of polling on a fixed guess.

Uses BeautifulSoup (a real HTML parser, not regex-on-text) because each date is split
across a `<strong>` tag boundary in the markup — e.g. `<p><strong>MARCH 10, 202</strong>6</p>`
renders as "MARCH 10, 2026" but naive tag-stripping mangles it.

Real bug found in iRacing's own page (verified live): the current/"LATEST" season's
button has the WRONG href (pointing at the previous season's slug) while its visible
text is correct ("Season 3"). So the season number is read from the link TEXT, not the
href, and paired with the nearest preceding year heading — this sidesteps the bug and
is more robust regardless.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup

SEASON_LINK_CLASS = "wp-block-button__link"
YEAR_HEADING_CLASS = "wp-block-heading"
DATE_CONTAINER_CLASS = "wp-block-cover__inner-container"

SEASON_TEXT_RE = re.compile(r"^Season\s+(\d+)$", re.IGNORECASE)
YEAR_TEXT_RE = re.compile(r"^(\d{4})$")
DATE_RE = re.compile(r"([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})")

MONTHS = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12,
}


@dataclass
class ParsedSeason:
    year: int
    quarter: int
    start_date: datetime


class SeasonsPageParseError(ValueError):
    pass


def _parse_date_text(text: str) -> datetime | None:
    match = DATE_RE.search(text.upper())
    if not match:
        return None
    month_name, day, year = match.groups()
    month = MONTHS.get(month_name)
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day))
    except ValueError:
        return None


def _find_date(link) -> datetime | None:
    container = link.find_parent("div", class_=DATE_CONTAINER_CLASS)
    if container is None:
        return None
    for p in container.find_all("p"):
        parsed = _parse_date_text(p.get_text())
        if parsed is not None:
            return parsed
    return None


def parse_seasons_page(html: str) -> list[ParsedSeason]:
    soup = BeautifulSoup(html, "html.parser")

    current_year: int | None = None
    seasons: list[ParsedSeason] = []
    for tag in soup.find_all(["h2", "a"]):
        if tag.name == "h2":
            if YEAR_HEADING_CLASS not in (tag.get("class") or []):
                continue
            match = YEAR_TEXT_RE.match(tag.get_text(strip=True))
            if match:
                current_year = int(match.group(1))
            continue

        if SEASON_LINK_CLASS not in (tag.get("class") or []) or current_year is None:
            continue
        match = SEASON_TEXT_RE.match(tag.get_text(strip=True))
        if not match:
            continue
        start_date = _find_date(tag)
        if start_date is None:
            continue
        seasons.append(ParsedSeason(year=current_year, quarter=int(match.group(1)), start_date=start_date))

    if not seasons:
        raise SeasonsPageParseError("No seasons found on page — check page layout")
    return seasons


def current_season(seasons: list[ParsedSeason], as_of: datetime) -> ParsedSeason | None:
    past_or_present = [s for s in seasons if s.start_date <= as_of]
    if not past_or_present:
        return None
    return max(past_or_present, key=lambda s: s.start_date)
