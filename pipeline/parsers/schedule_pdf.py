"""Deterministic parser: extracts series schedules from a publicly available PDF
(no iRacing account/login involved anywhere in this project).

Deliberately rule-based (pdfplumber table extraction + regex), not AI/LLM-based, so
results are reproducible and unit-testable against a fixture PDF
(tests/fixtures/sample_schedule_page.pdf, a single real page from the public mirror
used by pipeline/schedule_source.py).

Each series gets its own page/table, shaped like:
  [["Rookie Legends Cup by Simshop - 2025 Season 4\nLegends Ford '34 Coupe\n...", None, None, None],
   ["Week 1 (2025-09-16)",
    "Charlotte Motor Speedway - Legends Oval\n(2025-09-20 12:00 1x)",
    "70°F/21°C, Rain chance None, Rolling\nstart, Cautions disabled, Qual scrutiny\n- Permissive.",
    "40 laps"],
   ...]
Row 0 is the series header (name + car + rules text, not a week — it won't match
WEEK_RE and is used only for the series name). Column 0 of week rows is
"Week N (week-start date)"; column 1 is "Track name\n(race datetime)". Weeks run
Tuesday-to-Monday (iRacing's documented rollover day), so date_end is derived as
date_start + 6 days rather than read from the table.

The series name in the PDF includes the season suffix ("... - 2025 Season 4"); that's
stripped so shared/series-match.js can match the same series name across seasons.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import pdfplumber

from pipeline.licenses import LICENSE_ORDER

logger = logging.getLogger(__name__)

WEEK_RE = re.compile(r"^week\s+(\d+)\s*\((\d{4}-\d{2}-\d{2})\)", re.IGNORECASE)
SEASON_SUFFIX_RE = re.compile(r"\s*-\s*\d{4}\s+Season\s+\d+.*$", re.IGNORECASE)
# Not every cadence line contains the word "every" — day-specific ones like "Races
# Thur & Sat at 10, 19 GMT & Fri & Sun at 1,4 GMT" don't, so this must only anchor
# on "Races" itself (see pipeline/parsers/race_cadence.py for how the line is parsed).
CADENCE_LINE_RE = re.compile(r"^races\b.*$", re.IGNORECASE | re.MULTILINE)
DURATION_MINS_RE = re.compile(r"(\d+)\s*mins?\b", re.IGNORECASE)
# The header's license-range line, e.g. "Rookie (1.0) --> Pro/WC (4.0)" or
# "Class C 4.0 --> Pro/WC 4.0" (parens optional). Only the left-hand license/SR
# pair is captured — see _license_level for why that's the only part that matters.
LICENSE_LINE_RE = re.compile(
    r"^(rookie|class\s*[a-d]|pro\s*/\s*wc|pro\s*/\s*world\s*champion)\s*\(?\s*(\d+(?:\.\d+)?)\s*\)?",
    re.IGNORECASE | re.MULTILINE,
)
_LICENSE_NAME_TO_CODE = {
    "rookie": "R",
    "class a": "A",
    "class b": "B",
    "class c": "C",
    "class d": "D",
    "pro/wc": "P",
    "pro/world champion": "P",
}
# The PDF groups series under a top-level category ("OVAL", "SPORTS CAR", "FORMULA
# CAR", "DIRT OVAL", "DIRT ROAD", "UNRANKED") which is itself subdivided by license
# class, each subdivision headed by a line like "R Class Series (OVAL)". The
# category name only ever appears written out in full inside that parenthetical —
# the bare category heading above it (e.g. a standalone "OVAL" line) is redundant
# with it and sits outside every table's bbox, so this is matched via page.search()
# against the whole page rather than through a table cell.
CATEGORY_HEADER_RE = re.compile(r"[A-Za-z]\s+Class\s+Series\s*\(([^)]+)\)")


@dataclass
class ParsedWeek:
    week_number: int
    track_name: str
    date_start: datetime
    date_end: datetime
    duration_minutes: int | None = None


@dataclass
class ParsedSeries:
    name: str
    cadence_text: str
    weeks: list[ParsedWeek]
    link_url: str | None = None
    license_level: str = ""
    category: str = ""


class ScheduleParseError(ValueError):
    pass


def _track_name(track_cell: str) -> str:
    """First line of the track cell, before the embedded race-datetime line."""
    return track_cell.strip().splitlines()[0].strip()


def _series_name(header_cell: str) -> str:
    first_line = header_cell.strip().splitlines()[0].strip()
    return SEASON_SUFFIX_RE.sub("", first_line).strip()


def _cadence_text(header_cell: str) -> str:
    """The "Races ..." line from the series header block, e.g. "Races every even 2
    hours at :30 past" or the day-specific "Races Thur & Sat at 10, 19 GMT & Fri &
    Sun at 1,4 GMT" — parsed further by app/parsers/race_cadence.py. A handful of
    series append a separate qualifying cadence after a "|", e.g. "Races on every
    hour on the hour | Qualifying every hour at :30" — that part describes
    qualifying, not the race itself, so it's dropped rather than parsed as extra
    race session times."""
    match = CADENCE_LINE_RE.search(header_cell)
    if not match:
        return ""
    return match.group(0).split("|")[0].strip()


def _license_code(raw: str) -> str | None:
    normalized = re.sub(r"\s+", " ", raw.strip().lower())
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    return _LICENSE_NAME_TO_CODE.get(normalized)


def _license_level(header_cell: str) -> str:
    """The series' effective license tier (R/D/C/B/A/P), derived from the header's
    promotion-range line, e.g. "Rookie (1.0) --> Pro/WC (4.0)" or "Class C (4.0) -->
    Pro/WC (4.0)". Only the *lower* license/SR pair matters: an SR of 4.0 is
    iRacing's own auto-promotion threshold (>4.00 SR bumps a driver to the next
    license up), so a series gated at "Class C (4.0)" is in practice only reachable
    by drivers who are already at that boundary — i.e. effectively a Class B series,
    not Class C — so it's displayed one tier up from the literal text. A lower SR
    like "Rookie (1.0)" is just the bottom of that license's range and is displayed
    as-is."""
    match = LICENSE_LINE_RE.search(header_cell)
    if not match:
        return ""
    code = _license_code(match.group(1))
    if code is None:
        return ""
    sr = float(match.group(2))
    if sr >= 4.0:
        idx = LICENSE_ORDER.index(code)
        if idx + 1 < len(LICENSE_ORDER):
            code = LICENSE_ORDER[idx + 1]
    return code


def _duration_minutes(cell: str) -> int | None:
    """Some series state an explicit session length in the week row's rightmost
    cell, e.g. "120\\nmins" (others state a lap count instead, e.g. "69 laps", in
    which case there's nothing to extract and the caller falls back to estimating
    from session gaps — see worker/src/ics.js)."""
    match = DURATION_MINS_RE.search(cell.replace("\n", " "))
    return int(match.group(1)) if match else None


def _parse_row(row: list[str | None]) -> ParsedWeek | None:
    cells = [c.strip() if c else "" for c in row]
    if len(cells) < 2:
        return None
    week_match = WEEK_RE.match(cells[0])
    if not week_match:
        return None
    track_cell = cells[1]
    if not track_cell:
        return None
    date_start = datetime.strptime(week_match.group(2), "%Y-%m-%d")
    return ParsedWeek(
        week_number=int(week_match.group(1)),
        track_name=_track_name(track_cell),
        date_start=date_start,
        date_end=date_start + timedelta(days=6),
        duration_minutes=_duration_minutes(cells[3]) if len(cells) > 3 else None,
    )


def _is_continuation_table(table: list[list[str | None]]) -> bool:
    """A series' schedule can span a PDF page break; pdfplumber then reports the
    remainder as a separate table whose first row is already a week row (no series
    header row), which must be appended to the previous series rather than treated
    as a new one."""
    if not table or not table[0] or not table[0][0]:
        return False
    return bool(WEEK_RE.match(table[0][0].strip()))


def _parse_table(table: list[list[str | None]], link_url: str | None, category: str) -> ParsedSeries | None:
    if not table or not table[0] or not table[0][0]:
        return None
    weeks = [week for row in table if (week := _parse_row(row)) is not None]
    if not weeks:
        # A series header with no rows recognized as weeks (unexpected table
        # layout) would otherwise be dropped with no trace of it ever existing.
        logger.warning("no weeks parsed for series header %r; skipping", _series_name(table[0][0]))
        return None
    return ParsedSeries(
        name=_series_name(table[0][0]),
        cadence_text=_cadence_text(table[0][0]),
        weeks=weeks,
        link_url=link_url,
        license_level=_license_level(table[0][0]),
        category=category,
    )


# The "go-racing" URI link annotation iRacing embeds over each series' title line
# sits a small, fixed distance below the header table's top edge (~6pt, sampled
# from a real schedule PDF) rather than lining up exactly — hence the tolerance
# instead of an exact match.
LINK_ANNOT_TOLERANCE = 20.0


def _match_series_link(annots: list[dict], table_top: float, tolerance: float = LINK_ANNOT_TOLERANCE) -> str | None:
    """Picks the URI annotation (from a PDF page's link annotations, each a dict
    with at least "top" and "uri") positioned closest to a series header table's
    top edge, i.e. the annotation drawn over that series' title line."""
    candidates = [a for a in annots if abs(a["top"] - table_top) <= tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda a: abs(a["top"] - table_top))["uri"]


def _category_markers(page) -> list[tuple[float, str]]:
    """(top, category) for every "<Letter> Class Series (<CATEGORY>)" line on the
    page, sorted top-to-bottom, so callers can tell which series header table (by
    its own top) each one precedes."""
    return sorted(
        (match["top"], match["groups"][0])
        for match in page.search(CATEGORY_HEADER_RE, return_chars=False)
    )


def parse_schedule_pdf(path: str) -> list[ParsedSeries]:
    series_list: list[ParsedSeries] = []
    current_category = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            annots = [
                {"top": a["top"], "uri": a["uri"]} for a in (page.annots or []) if a.get("uri")
            ]
            markers = _category_markers(page)
            marker_idx = 0
            tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
            for pdf_table in tables:
                table_top = pdf_table.bbox[1]
                # Category markers sit outside every table's bbox, so any marker
                # above this table's top edge takes effect before it's parsed.
                while marker_idx < len(markers) and markers[marker_idx][0] <= table_top:
                    current_category = markers[marker_idx][1]
                    marker_idx += 1
                table = pdf_table.extract()
                if _is_continuation_table(table):
                    weeks = [week for row in table if (week := _parse_row(row)) is not None]
                    if weeks and series_list:
                        series_list[-1].weeks.extend(weeks)
                    continue
                link_url = _match_series_link(annots, table_top)
                parsed = _parse_table(table, link_url, current_category)
                if parsed is not None:
                    series_list.append(parsed)
            # A marker with no table after it on this page (e.g. a class section
            # whose first series starts on the next page) must still carry over.
            if marker_idx < len(markers):
                current_category = markers[-1][1]
    if not series_list:
        raise ScheduleParseError(f"No series schedules found in {path!r} — check table layout")
    return series_list
