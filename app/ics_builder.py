"""Builds a user's .ics feed from their Selections.

Championship VEVENTs are stamped in UTC (`Z`-suffixed DTSTART/DTEND, no VTIMEZONE).
A Timeslot's `time_gmt`/`day_of_week` are picked from the series' own
Series.session_times (see app/parsers/race_cadence.py) — iRacing runs sessions at
fixed GMT times that don't shift with DST, so this is a direct day+time -> UTC
combination per the actual calendar date of each week, not a timezone conversion.
Each session's duration is unknown from the schedule data, so every event defaults
to DEFAULT_RACE_DURATION.

Special events render as all-day (DATE, not DATE-TIME) events spanning their full
announced date range inclusive — per RFC 5445 an all-day DTEND is exclusive, so it's
set to one day past the last inclusive day.

A season's own weeks run 1-12 (see app/parsers/schedule_pdf.py); the week
immediately after (iRacing's "Week 13" build/transition week, tracks change daily
instead of weekly, no regular schedule) gets one synthetic reminder VEVENT per user
nudging them to come re-pick championships for the next season — see
_week13_reminder_event.
"""

from datetime import datetime, timedelta, timezone

from icalendar import Calendar, Event
from sqlmodel import Session

from app.models import Selection, SpecialEvent, User

DEFAULT_RACE_DURATION = timedelta(minutes=60)


def _date_for_weekday(range_start: datetime, range_end: datetime, day_of_week: int) -> datetime | None:
    """Find the date within [range_start, range_end] matching the given weekday
    (0=Monday..6=Sunday), so a Timeslot's day-of-week lands on the right calendar
    date even though weeks don't all start on the same weekday."""
    for offset in range((range_end.date() - range_start.date()).days + 1):
        candidate = range_start + timedelta(days=offset)
        if candidate.weekday() == day_of_week:
            return candidate
    return None


def _series_events(selection: Selection) -> list[Event]:
    events = []
    weeks = selection.series.weeks if selection.series else []
    for week in weeks:
        for slot in selection.timeslots:
            day = _date_for_weekday(week.date_start, week.date_end, slot.day_of_week)
            if day is None:
                continue
            dtstart = datetime.combine(day.date(), slot.time_gmt, tzinfo=timezone.utc)
            events.append(_build_timed_event(
                uid=f"series-{selection.series_id}-week{week.week_number}-slot{slot.id}@iracing-calendar",
                summary=f"{selection.series.name} — {week.track_name}",
                location=week.track_name,
                dtstart=dtstart,
                dtend=dtstart + DEFAULT_RACE_DURATION,
            ))
    return events


def _week13_reminder_event(user: User) -> Event | None:
    """Week 13 has no regular schedule (daily track changes, build/transition
    week), so instead of a real race there's one reminder per user to come re-pick
    championships for the next season — timed using the first subscribed
    championship's own timeslot, so it's not an arbitrary time.

    Uses that series' own last scheduled week, not Season.end_date — a handful of
    series (e.g. continuous multi-season ones like eNASCAR-style series) run far
    more than 12 weeks in the schedule data, which would otherwise skew the season's
    overall end_date well past when a normal 12-week series' Week 13 actually is."""
    series_selections = [s for s in user.selections if s.series_id is not None]
    if not series_selections:
        return None
    first_selection = series_selections[0]
    if not first_selection.timeslots or not first_selection.series or not first_selection.series.weeks:
        return None
    series = first_selection.series

    last_week_end = max(week.date_end for week in series.weeks)
    week13_start = last_week_end + timedelta(days=1)
    week13_end = week13_start + timedelta(days=6)
    slot = first_selection.timeslots[0]
    day = _date_for_weekday(week13_start, week13_end, slot.day_of_week)
    if day is None:
        return None

    dtstart = datetime.combine(day.date(), slot.time_gmt, tzinfo=timezone.utc)
    return _build_timed_event(
        uid=f"week13-reminder-series{series.id}@iracing-calendar",
        summary="Pick your iRacing championships for next season!",
        location="",
        dtstart=dtstart,
        dtend=dtstart + DEFAULT_RACE_DURATION,
    )


def _special_event_event(selection: Selection) -> Event | None:
    event: SpecialEvent | None = selection.special_event
    if event is None:
        return None
    event_component = Event()
    event_component.add("uid", f"special-{event.id}@iracing-calendar")
    event_component.add("summary", event.name)
    if event.track_name:
        event_component.add("location", event.track_name)
    event_component.add("dtstart", event.date_start.date())
    # All-day DTEND is exclusive per RFC 5545, so +1 day covers date_end inclusive.
    event_component.add("dtend", event.date_end.date() + timedelta(days=1))
    event_component.add("dtstamp", datetime.now(tz=timezone.utc))
    return event_component


def _build_timed_event(uid: str, summary: str, location: str, dtstart: datetime, dtend: datetime) -> Event:
    event = Event()
    event.add("uid", uid)
    event.add("summary", summary)
    if location:
        event.add("location", location)
    event.add("dtstart", dtstart)
    event.add("dtend", dtend)
    event.add("dtstamp", datetime.now(tz=timezone.utc))
    return event


def build_calendar(session: Session, user: User) -> Calendar:
    calendar = Calendar()
    calendar.add("prodid", "-//iRacing Calendar//iracing-calendar//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", f"{user.name} — iRacing")

    for selection in user.selections:
        if selection.series_id is not None:
            for event in _series_events(selection):
                calendar.add_component(event)
        elif selection.special_event_id is not None:
            event = _special_event_event(selection)
            if event is not None:
                calendar.add_component(event)

    reminder = _week13_reminder_event(user)
    if reminder is not None:
        calendar.add_component(reminder)

    return calendar
