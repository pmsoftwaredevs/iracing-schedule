"""GitHub Actions entry point (`python -m tools.build_cache`): refreshes
docs/data/*.json from iRacing's own public pages/PDF (no account/login anywhere)
and prunes anything older than the current+previous season.

INDEX-STABILITY INVARIANT: a calendar code (see shared/calendar-code.js) addresses
a championship or special event by its ARRAY POSITION in the season's cache JSON,
not by any id — there is no database to look one up in. That means this script
must never reorder an already-published season's `championships`/`special_events`
arrays, or every previously-issued code for that season would silently start
pointing at the wrong entry. Every write here reconciles against whatever was
already committed for that season (see `_reconcile`): existing entries keep their
position (with their data refreshed from this run's parse, or left frozen if this
run's parse doesn't mention them anymore), and only brand-new entries get
appended at the end.

Season rollover: the schedule PDF URL (`Settings.schedule_pdf_url`) is evergreen —
there is no year/quarter in it, it always serves "whatever's current" — so a past
season's data can never be re-fetched. When a new season is detected, whatever was
"current" simply becomes "previous" (its file is left untouched forever) and the
new current season's cache is built fresh from live sources. Anything older than
current+previous is deleted; see shared/calendar-code.js's decodeCalendarCode for
how a code referencing a pruned season decodes to an empty calendar instead of an
error.
"""

import io
import json
import logging
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from pipeline.config import Settings, get_settings
from pipeline.logo_matching import match_logos
from pipeline.parsers.logos_page import parse_logos_page
from pipeline.parsers.race_cadence import parse_cadence
from pipeline.parsers.schedule_pdf import ParsedSeries, ParsedWeek, parse_schedule_pdf
from pipeline.parsers.seasons_page import current_season, parse_seasons_page
from pipeline.parsers.special_events_page import ParsedSpecialEvent, parse_special_events_page
from pipeline.schedule_source import fetch_schedule_pdf

logger = logging.getLogger(__name__)

# Every 30 minutes, every day — used when a cadence string doesn't match any known
# pattern at all (see pipeline/parsers/race_cadence.py), so the picker always has
# something to offer instead of an empty dropdown.
FALLBACK_SESSION_TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]


def season_code(year: int, quarter: int) -> str:
    """e.g. (2026, 3) -> "2026S3" — see shared/calendar-code.js's 6-char season prefix."""
    return f"{year:04d}S{quarter}"


def season_slug(year: int, quarter: int) -> str:
    """e.g. (2026, 3) -> "2026_s3" — the cache filename stem, per the user's stated
    naming convention."""
    return f"{year}_s{quarter}"


def _slug_from_code(code: str) -> str:
    return f"{code[:4]}_s{code[5:]}"


def _session_times_by_day(cadence_text: str) -> dict[str, list[str]]:
    parsed = parse_cadence(cadence_text) if cadence_text else None
    if parsed is None:
        if cadence_text:
            logger.warning("could not parse cadence %r, falling back to a default grid", cadence_text)
        return {str(day): list(FALLBACK_SESSION_TIMES) for day in range(7)}
    return {str(day): [t.strftime("%H:%M") for t in times] for day, times in parsed.times_by_day.items()}


def _session_time_options(session_times_by_day: dict[str, list[str]]) -> list[str]:
    """Flattened, sorted, de-duplicated across every day — the exact array a
    calendar code's time-slot index (see shared/calendar-code.js) points into.
    Computed once here so the picker UI and the Cloudflare Worker never have to
    independently flatten/sort and risk a subtle ordering mismatch."""
    return sorted({t for times in session_times_by_day.values() for t in times})


def _duration_from_gap_minutes(gap: timedelta) -> int:
    """Half of a gap between two sessions, rounded to the nearest 5 minutes — ports
    worker/src/ics.js's _duration_from_gap; kept here purely as a
    display-only estimate baked into the cache once, instead of computed
    per-request."""
    if gap >= timedelta(hours=24):
        return 60
    half_minutes = gap.total_seconds() / 2 / 60
    return round(half_minutes / 5) * 5


def _typical_session_duration_minutes(weeks: list[ParsedWeek], session_time_options: list[str]) -> int | None:
    explicit = next((week.duration_minutes for week in weeks if week.duration_minutes), None)
    if explicit is not None:
        return explicit
    if len(session_time_options) < 2:
        return None
    offsets = sorted(
        timedelta(hours=int(hh), minutes=int(mm)) for hh, mm in (t.split(":") for t in session_time_options)
    )
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    gaps.append(offsets[0] + timedelta(days=1) - offsets[-1])
    return _duration_from_gap_minutes(min(gaps))


def _week_to_dict(week: ParsedWeek) -> dict:
    return {
        "week_number": week.week_number,
        "track_name": week.track_name,
        "date_start": week.date_start.date().isoformat(),
        "date_end": week.date_end.date().isoformat(),
        "duration_minutes": week.duration_minutes,
    }


def _championship_to_dict(parsed: ParsedSeries) -> dict:
    session_times_by_day = _session_times_by_day(parsed.cadence_text)
    session_time_options = _session_time_options(session_times_by_day)
    return {
        "name": parsed.name,
        "category": parsed.category,
        "license_level": parsed.license_level,
        "cadence_text": parsed.cadence_text,
        "link_url": parsed.link_url,
        "session_times_by_day": session_times_by_day,
        "session_time_options": session_time_options,
        "typical_session_duration_minutes": _typical_session_duration_minutes(parsed.weeks, session_time_options),
        "weeks": [_week_to_dict(w) for w in parsed.weeks],
    }


def _special_event_to_dict(parsed: ParsedSpecialEvent) -> dict:
    return {
        "slug": parsed.slug,
        "name": parsed.name,
        "date_start": parsed.date_start.date().isoformat(),
        "date_end": parsed.date_end.date().isoformat(),
        "track_name": parsed.track_name,
        "track_path": parsed.track_path,
        "car_class": parsed.car_class,
        "link_url": parsed.link_url,
    }


def _reconcile(existing: list[dict], fresh: list[dict], key: str) -> list[dict]:
    """The index-stability invariant, concretely: `existing` entries keep their
    position no matter what; an entry whose `key` still appears in `fresh` gets its
    data refreshed, one whose `key` has vanished from this run's parse keeps its
    last-known (frozen) data rather than being dropped — removing it would shift
    every later entry's index. Brand-new keys are appended at the end, never
    inserted."""
    fresh_by_key = {item[key]: item for item in fresh}
    reconciled = []
    seen: set[str] = set()
    for old in existing:
        k = old[key]
        if k in seen:
            continue
        reconciled.append(fresh_by_key.get(k, old))
        seen.add(k)
    for item in fresh:
        if item[key] not in seen:
            reconciled.append(item)
            seen.add(item[key])
    return reconciled


def _slugify(text: str) -> str:
    """Filesystem-safe key for a championship's logo filename — unlike special
    events, championships have no slug of their own, so one is derived from the
    name."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "logo"


def _fetch_zip(url: str) -> zipfile.ZipFile:
    response = httpx.get(url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(response.content))


def _extract_matched_logos(
    zf: zipfile.ZipFile,
    matches: dict[str, str],
    out_dir: Path,
    key_to_filename,
) -> dict[str, str]:
    """Writes each matched ZIP member to `out_dir` and returns key -> URL relative
    to docs/ (e.g. "logos/events/foo.png"). Also deletes any file left in `out_dir`
    from a previous run whose key isn't matched this run, so a renamed/removed
    championship or event doesn't leave a stale logo behind forever."""
    out_dir.mkdir(parents=True, exist_ok=True)
    urls: dict[str, str] = {}
    keep_names: set[str] = set()
    for key, member in matches.items():
        ext = Path(member).suffix.lower() or ".png"
        filename = f"{key_to_filename(key)}{ext}"
        (out_dir / filename).write_bytes(zf.read(member))
        urls[key] = f"logos/{out_dir.name}/{filename}"
        keep_names.add(filename)
    for existing in out_dir.iterdir():
        if existing.name not in keep_names:
            existing.unlink()
    return urls


_FIXED_SUFFIX_RE = re.compile(r"\s*-?\s*fixed\s*$", re.IGNORECASE)


def _base_series_name(name: str) -> str:
    """Strips a trailing "Fixed" marker so a series's Fixed and regular variants
    group under one key — e.g. "CARS Tour Late Model Stocks" and "CARS Tour Late
    Model Stocks - Fixed" both normalize to "cars tour late model stocks"."""
    return _FIXED_SUFFIX_RE.sub("", name).strip().lower()


def _share_logos_across_fixed_variants(championships: list[dict]) -> None:
    """A championship's Fixed and regular variants are the same underlying series
    with the same artwork — iRacing's logo pack usually ships only one file per
    series, not one per variant, so fuzzy matching (pipeline/logo_matching.py)
    naturally finds a confident match for only one of them. Reuses that same
    already-extracted file for any sibling left without one, rather than leaving
    it with no logo at all."""
    groups: dict[str, list[dict]] = {}
    for championship in championships:
        groups.setdefault(_base_series_name(championship["name"]), []).append(championship)
    for group in groups.values():
        if len(group) < 2:
            continue
        shared_logo_url = next((c["logo_url"] for c in group if c["logo_url"]), None)
        if shared_logo_url is None:
            continue
        for championship in group:
            if not championship["logo_url"]:
                championship["logo_url"] = shared_logo_url


def _add_logos(settings: Settings, fresh_championships: list[dict], fresh_events: list[dict]) -> None:
    """Best-effort: matches this run's fresh championships/events against iRacing's
    logo-pack ZIPs (pipeline/parsers/logos_page.py, pipeline/logo_matching.py) and
    sets each dict's `logo_url` in place. Unlike the schedule/events data itself,
    logos are supplementary — a logos-page layout change that leaves a box
    unfound is logged and skipped rather than failing the whole build. A
    network-level fetch failure still propagates, same as every other fetch here."""
    logos_response = httpx.get(settings.logos_page_url, timeout=30.0, follow_redirects=True)
    logos_response.raise_for_status()
    special_events_zip_url, championship_zip_url = parse_logos_page(logos_response.text)

    for event in fresh_events:
        event["logo_url"] = None
    if special_events_zip_url:
        zf = _fetch_zip(special_events_zip_url)
        candidates = {e["slug"]: [e["name"], e["slug"].replace("-", " ")] for e in fresh_events}
        matches = match_logos(candidates, zf.namelist())
        urls = _extract_matched_logos(zf, matches, Path(settings.logos_dir) / "events", lambda slug: slug)
        for event in fresh_events:
            event["logo_url"] = urls.get(event["slug"])
    else:
        logger.warning("could not find the special-event logo pack link on %s", settings.logos_page_url)

    for championship in fresh_championships:
        championship["logo_url"] = None
    if championship_zip_url:
        zf = _fetch_zip(championship_zip_url)
        candidates = {c["name"]: [c["name"]] for c in fresh_championships}
        matches = match_logos(candidates, zf.namelist())
        urls = _extract_matched_logos(zf, matches, Path(settings.logos_dir) / "championships", _slugify)
        for championship in fresh_championships:
            championship["logo_url"] = urls.get(championship["name"])
    else:
        logger.warning("could not find the official-series logo pack link on %s", settings.logos_page_url)

    _share_logos_across_fixed_variants(fresh_championships)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def build_cache(
    settings: Settings | None = None,
    data_dir: Path | None = None,
    schedule_cache_dir: Path | None = None,
) -> None:
    settings = settings or get_settings()
    data_dir = data_dir if data_dir is not None else Path(settings.data_dir)
    schedule_cache_dir = schedule_cache_dir if schedule_cache_dir is not None else Path(settings.schedule_pdf_cache_dir)

    seasons_response = httpx.get(settings.seasons_page_url, timeout=30.0, follow_redirects=True)
    seasons_response.raise_for_status()
    seasons = parse_seasons_page(seasons_response.text)
    current = current_season(seasons, datetime.now(UTC).replace(tzinfo=None))
    if current is None:
        raise RuntimeError(f"could not determine current season from {settings.seasons_page_url}")

    manifest_path = data_dir / "manifest.json"
    manifest = _load_json(manifest_path) or {"current": None, "previous": None}

    new_current_code = season_code(current.year, current.quarter)
    new_current_slug = season_slug(current.year, current.quarter)
    is_rollover = manifest["current"] != new_current_code
    new_previous_code = manifest["current"] if is_rollover else manifest["previous"]

    keep_slugs = {_slug_from_code(code) for code in (new_current_code, new_previous_code) if code}
    for existing_file in sorted(data_dir.glob("*_s*.json")):
        if existing_file.stem not in keep_slugs:
            logger.info("pruning old season cache %s", existing_file)
            existing_file.unlink()

    existing_current = None if is_rollover else _load_json(data_dir / f"{new_current_slug}.json")

    pdf_path = fetch_schedule_pdf(settings, current.year, current.quarter, cache_dir=schedule_cache_dir)
    parsed_series = parse_schedule_pdf(str(pdf_path))
    fresh_championships = [_championship_to_dict(s) for s in parsed_series]

    events_response = httpx.get(settings.special_events_page_url, timeout=30.0, follow_redirects=True)
    events_response.raise_for_status()
    parsed_events = parse_special_events_page(events_response.text)
    fresh_events = [_special_event_to_dict(e) for e in parsed_events]

    _add_logos(settings, fresh_championships, fresh_events)

    existing_championships = existing_current["championships"] if existing_current else []
    championships = _reconcile(existing_championships, fresh_championships, key="name")
    logger.info("parsed %d championships for %s", len(championships), new_current_code)

    existing_events = existing_current["special_events"] if existing_current else []
    special_events = _reconcile(existing_events, fresh_events, key="slug")
    logger.info("parsed %d special events for %s", len(special_events), new_current_code)

    # season_start_date comes from iracing.com/seasons, not aggregated from the
    # PDF's per-series week data — a handful of series run continuously across
    # multiple seasons with far more than 12 weeks listed, which would otherwise
    # skew a PDF-derived min/max start/end date by months.
    season_end = current.start_date + timedelta(weeks=12) - timedelta(days=1)
    current_payload = {
        "season": {
            "year": current.year,
            "quarter": current.quarter,
            "code": new_current_code,
            "start_date": current.start_date.date().isoformat(),
            "end_date": season_end.date().isoformat(),
        },
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "championships": championships,
        "special_events": special_events,
    }
    if existing_current and {**existing_current, "generated_at": None} == {**current_payload, "generated_at": None}:
        # Nothing actually changed this run — keep the old timestamp so the file
        # is byte-for-byte identical and the workflow's commit step (which diffs
        # docs/data/**) doesn't produce a no-op "Refresh season cache" commit.
        current_payload["generated_at"] = existing_current["generated_at"]
    _write_json(data_dir / f"{new_current_slug}.json", current_payload)
    _write_json(manifest_path, {"current": new_current_code, "previous": new_previous_code})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    build_cache()


if __name__ == "__main__":
    main()
