"""Background jobs.

1. Season watcher — checks iracing.com/seasons for the current season
   (app/parsers/seasons_page.py); if it's not in the DB yet, triggers a schedule
   fetch. Runs every ~2 months via cron AND once at app startup (see
   app/main.py's lifespan) so a fresh deployment has data immediately.
2. Season schedule fetch (on season start) — downloads the official schedule PDF
   (app/schedule_source.py), parses it deterministically (app/parsers/schedule_pdf.py),
   upserts Series/ScheduleWeek, then matches the previous season's Series against the
   new ones (app/matcher.py) and emails affected users a rollover recap
   (app/email.py).
3. Special events fetch (every ~2 months, or at startup if none exist yet) —
   parses the real iracing.com/special-events HTML page directly
   (app/parsers/special_events_page.py — dates, track links, and car-class labels
   are all real markup there, no OCR needed) and upserts SpecialEvent rows.

No iRacing account/login is used anywhere in this pipeline — every fetch is a plain
HTTPS GET against a public URL.
"""

import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.config import Settings
from app.db import engine
from app.email import send_rollover_email
from app.matcher import match_series
from app.models import (
    Season,
    ScheduleWeek,
    Selection,
    Series,
    SpecialEvent,
    SpecialEventSource,
    User,
)
from app.parsers.race_cadence import parse_cadence
from app.parsers.schedule_pdf import parse_schedule_pdf
from app.parsers.seasons_page import current_season, parse_seasons_page
from app.parsers.special_events_page import parse_special_events_page
from app.schedule_source import fetch_schedule_pdf

logger = logging.getLogger(__name__)

SCHEDULE_CACHE_DIR = Path("cache/schedules")

# Used when a series' cadence text doesn't match any known pattern at all — every
# 30 minutes, every day of the week, is a reasonably fine-grained default so the
# timeslot picker always has something to offer rather than being empty.
FALLBACK_SESSION_TIMES = [time(hour, minute) for hour in range(24) for minute in (0, 30)]


def _session_times_for(cadence_text: str) -> dict[str, list[str]]:
    parsed = parse_cadence(cadence_text) if cadence_text else None
    if parsed is None:
        if cadence_text:
            logger.warning("could not parse cadence %r, falling back to a default grid", cadence_text)
        times_by_day = {day: FALLBACK_SESSION_TIMES for day in range(7)}
    else:
        times_by_day = parsed.times_by_day
    return {str(day): [t.strftime("%H:%M") for t in times] for day, times in times_by_day.items()}


def check_upcoming_seasons(settings: Settings) -> None:
    response = httpx.get(settings.seasons_page_url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    seasons = parse_seasons_page(response.text)

    current = current_season(seasons, datetime.now(tz=timezone.utc).replace(tzinfo=None))
    if current is None:
        logger.warning("could not determine current season from %s", settings.seasons_page_url)
        return

    with Session(engine) as session:
        exists = session.exec(
            select(Season).where(Season.year == current.year, Season.quarter == current.quarter)
        ).first()

    if exists is not None:
        logger.info("current season %s S%s already ingested", current.year, current.quarter)
        return

    logger.info("new season detected: %s S%s, fetching schedule", current.year, current.quarter)
    fetch_season_schedule(settings, current.year, current.quarter, current.start_date)


def bootstrap_if_needed(settings: Settings) -> None:
    """Called once at app startup so a fresh deployment has real data immediately
    instead of waiting for the next cron tick (up to ~2 months)."""
    check_upcoming_seasons(settings)

    with Session(engine) as session:
        has_events = session.exec(select(SpecialEvent)).first() is not None
    if not has_events:
        logger.info("no special events yet, fetching")
        fetch_special_events(settings)


def fetch_season_schedule(settings: Settings, year: int, quarter: int, season_start_date: datetime) -> None:
    pdf_path = fetch_schedule_pdf(settings, year, quarter, cache_dir=SCHEDULE_CACHE_DIR)
    parsed_series = parse_schedule_pdf(str(pdf_path))
    logger.info("parsed %d series from schedule PDF for %s S%s", len(parsed_series), year, quarter)

    with Session(engine) as session:
        previous_season = session.exec(select(Season).order_by(Season.start_date.desc())).first()

        # season_start_date comes from iracing.com/seasons (app/parsers/seasons_page.py),
        # not aggregated from the PDF's per-series week data — a handful of series run
        # continuously across multiple seasons with far more than 12 weeks listed, which
        # would otherwise skew a PDF-derived min/max start/end date by months.
        season = Season(
            year=year,
            quarter=quarter,
            start_date=season_start_date,
            end_date=season_start_date + timedelta(weeks=12) - timedelta(days=1),
        )
        session.add(season)
        session.flush()

        new_series: list[Series] = []
        for parsed in parsed_series:
            series = Series(
                season_id=season.id,
                name=parsed.name,
                cadence_text=parsed.cadence_text,
                link_url=parsed.link_url,
                session_times_by_day=_session_times_for(parsed.cadence_text),
            )
            session.add(series)
            session.flush()
            for week in parsed.weeks:
                session.add(
                    ScheduleWeek(
                        series_id=series.id,
                        week_number=week.week_number,
                        track_name=week.track_name,
                        date_start=week.date_start,
                        date_end=week.date_end,
                        duration_minutes=week.duration_minutes,
                    )
                )
            new_series.append(series)
        session.commit()

        if previous_season is not None:
            _rollover_selections(session, settings, previous_season, new_series)


def _rollover_selections(session: Session, settings: Settings, previous_season: Season, new_series: list[Series]) -> None:
    old_series = session.exec(select(Series).where(Series.season_id == previous_season.id)).all()
    if not old_series:
        return

    match_result = match_series(old_series, new_series)
    old_to_new = {old.id: new for old, new in match_result.matched}
    old_series_by_id = {s.id: s for s in old_series}

    affected_users: dict[int, list[str]] = {}
    old_series_ids = list(old_series_by_id.keys())
    selections = session.exec(select(Selection).where(Selection.series_id.in_(old_series_ids))).all()

    for selection in selections:
        old_name = old_series_by_id[selection.series_id].name
        matched_new_series = old_to_new.get(selection.series_id)
        if matched_new_series is not None:
            selection.series_id = matched_new_series.id
            session.add(selection)
            affected_users.setdefault(selection.user_id, []).append(f"Matched: {old_name}")
        else:
            affected_users.setdefault(selection.user_id, []).append(
                f"Not found this season, please re-pick: {old_name}"
            )
    session.commit()

    for user_id, lines in affected_users.items():
        user = session.get(User, user_id)
        if user is None:
            continue
        manage_url = f"{settings.base_url}/u/{user.token}"
        send_rollover_email(settings, user.email, user.name, manage_url, "\n".join(lines))


def fetch_special_events(settings: Settings) -> None:
    response = httpx.get(settings.special_events_page_url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    parsed_events = parse_special_events_page(response.text)
    logger.info("parsed %d special events from %s", len(parsed_events), settings.special_events_page_url)

    with Session(engine) as session:
        existing_by_name = {e.name: e for e in session.exec(select(SpecialEvent)).all()}
        for parsed in parsed_events:
            existing = existing_by_name.get(parsed.name)
            if existing is not None:
                existing.date_start = parsed.date_start
                existing.date_end = parsed.date_end
                existing.track_name = parsed.track_name
                existing.track_path = parsed.track_path
                existing.car_class = parsed.car_class
                session.add(existing)
            else:
                session.add(
                    SpecialEvent(
                        year=parsed.date_start.year,
                        name=parsed.name,
                        date_start=parsed.date_start,
                        date_end=parsed.date_end,
                        track_name=parsed.track_name,
                        track_path=parsed.track_path,
                        car_class=parsed.car_class,
                        source=SpecialEventSource.PAGE,
                    )
                )
        session.commit()


def build_scheduler(settings: Settings) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        check_upcoming_seasons,
        trigger=CronTrigger(day="1", month="*/2", hour=0),
        args=[settings],
        id="season-watcher",
        replace_existing=True,
    )
    scheduler.add_job(
        fetch_special_events,
        trigger=CronTrigger(day="2", month="*/2", hour=1),
        args=[settings],
        id="special-events-refresh",
        replace_existing=True,
    )
    return scheduler
