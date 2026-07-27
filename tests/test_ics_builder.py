from datetime import date, datetime, time, timedelta, timezone

from app.ics_builder import build_calendar
from app.models import ScheduleWeek, Season, Selection, Series, SpecialEvent, Timeslot, User


def test_series_selection_produces_one_event_per_week_per_slot(session):
    user = User(name="Alex", email="alex@example.com")
    session.add(user)
    session.commit()

    series = Series(season_id=1, name="GT3 Fixed", category="Sports Car")
    session.add(series)
    session.commit()

    # Week 1 runs Tue 2026-01-06 .. Mon 2026-01-12 (matches iRacing's Tuesday rollover)
    week = ScheduleWeek(
        series_id=series.id,
        week_number=1,
        track_name="Spa-Francorchamps",
        date_start=datetime(2026, 1, 6),
        date_end=datetime(2026, 1, 12),
    )
    session.add(week)
    session.commit()

    selection = Selection(user_id=user.id, series_id=series.id)
    session.add(selection)
    session.commit()

    # Two sessions a week: Wednesday 19:00 GMT and Saturday 14:30 GMT
    session.add(Timeslot(selection_id=selection.id, day_of_week=2, time_gmt=time(19, 0)))
    session.add(Timeslot(selection_id=selection.id, day_of_week=5, time_gmt=time(14, 30)))
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    # Excludes the week-13 "pick next season" reminder — covered separately below.
    events = [e for e in calendar.walk("VEVENT") if "next season" not in str(e["summary"])]

    assert len(events) == 2
    starts = sorted(e["dtstart"].dt for e in events)
    assert starts[0] == datetime(2026, 1, 7, 19, 0, tzinfo=timezone.utc)
    assert starts[1] == datetime(2026, 1, 10, 14, 30, tzinfo=timezone.utc)
    assert all(e["dtstart"].dt.tzinfo == timezone.utc for e in events)
    summaries = {str(e["summary"]) for e in events}
    assert summaries == {"GT3 Fixed — Spa-Francorchamps"}


def test_special_event_single_day_still_covers_that_day(session):
    user = User(name="Sam", email="sam@example.com")
    session.add(user)
    session.commit()

    event = SpecialEvent(
        year=2026, name="Roar Before the 24",
        date_start=datetime(2026, 1, 9), date_end=datetime(2026, 1, 10),
    )
    session.add(event)
    session.commit()

    selection = Selection(user_id=user.id, special_event_id=event.id)
    session.add(selection)
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    events = list(calendar.walk("VEVENT"))

    assert len(events) == 1
    assert events[0]["dtstart"].dt == date(2026, 1, 9)
    assert events[0]["dtend"].dt == date(2026, 1, 11)  # exclusive end, covers Jan 9-10 inclusive


def test_special_event_multi_day_spans_full_range(session):
    user = User(name="Sam", email="sam@example.com")
    session.add(user)
    session.commit()

    event = SpecialEvent(
        year=2026, name="Firecracker 400",
        date_start=datetime(2026, 6, 30), date_end=datetime(2026, 7, 6),
    )
    session.add(event)
    session.commit()

    selection = Selection(user_id=user.id, special_event_id=event.id)
    session.add(selection)
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    events = list(calendar.walk("VEVENT"))

    assert len(events) == 1
    assert events[0]["dtstart"].dt == date(2026, 6, 30)
    assert events[0]["dtend"].dt == date(2026, 7, 7)  # exclusive end, covers Jun 30 - Jul 6 inclusive
    assert events[0]["summary"] == "Firecracker 400"


def _make_season_and_series(session, name, last_week_end_date):
    season = Season(
        year=2026, quarter=3,
        start_date=datetime(2026, 6, 16), end_date=last_week_end_date,
    )
    session.add(season)
    session.commit()

    series = Series(season_id=season.id, name=name)
    session.add(series)
    session.commit()
    session.add(ScheduleWeek(
        series_id=series.id, week_number=12, track_name="Some Track",
        date_start=last_week_end_date - timedelta(days=6), date_end=last_week_end_date,
    ))
    session.commit()
    return season, series


def test_week13_reminder_generated_after_last_week_using_first_selection_timeslot(session):
    user = User(name="Alex", email="alex@example.com")
    session.add(user)
    session.commit()

    # Season's last week (week 12) ends Monday 2026-08-31, so week 13 runs
    # Tue 2026-09-01 .. Mon 2026-09-07.
    _season, series = _make_season_and_series(session, "GT3 Fixed", datetime(2026, 8, 31))

    selection = Selection(user_id=user.id, series_id=series.id)
    session.add(selection)
    session.commit()
    session.add(Timeslot(selection_id=selection.id, day_of_week=2, time_gmt=time(19, 0)))  # Wednesday
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    reminders = [e for e in calendar.walk("VEVENT") if "next season" in str(e["summary"])]

    assert len(reminders) == 1
    assert reminders[0]["dtstart"].dt == datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc)


def test_week13_reminder_only_one_when_multiple_championships_selected(session):
    user = User(name="Alex", email="alex@example.com")
    session.add(user)
    session.commit()

    season, first_series = _make_season_and_series(session, "GT3 Fixed", datetime(2026, 8, 31))
    second_series = Series(season_id=season.id, name="Formula Vee")
    session.add(second_series)
    session.commit()

    first_selection = Selection(user_id=user.id, series_id=first_series.id)
    second_selection = Selection(user_id=user.id, series_id=second_series.id)
    session.add(first_selection)
    session.add(second_selection)
    session.commit()
    session.add(Timeslot(selection_id=first_selection.id, day_of_week=2, time_gmt=time(19, 0)))
    session.add(Timeslot(selection_id=second_selection.id, day_of_week=5, time_gmt=time(10, 0)))
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    reminders = [e for e in calendar.walk("VEVENT") if "next season" in str(e["summary"])]

    assert len(reminders) == 1
    # Timed using the FIRST selection (Wednesday 19:00), not the second (Saturday 10:00).
    assert reminders[0]["dtstart"].dt == datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc)


def test_no_week13_reminder_when_no_championships_selected(session):
    user = User(name="Sam", email="sam@example.com")
    session.add(user)
    session.commit()

    event = SpecialEvent(
        year=2026, name="Daytona 24",
        date_start=datetime(2026, 1, 24), date_end=datetime(2026, 1, 25),
    )
    session.add(event)
    session.commit()
    selection = Selection(user_id=user.id, special_event_id=event.id)
    session.add(selection)
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    reminders = [e for e in calendar.walk("VEVENT") if "next season" in str(e["summary"])]

    assert reminders == []


def test_no_week13_reminder_when_selection_has_no_timeslots(session):
    user = User(name="Alex", email="alex@example.com")
    session.add(user)
    session.commit()

    _season, series = _make_season_and_series(session, "GT3 Fixed", datetime(2026, 8, 31))
    selection = Selection(user_id=user.id, series_id=series.id)
    session.add(selection)
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    reminders = [e for e in calendar.walk("VEVENT") if "next season" in str(e["summary"])]

    assert reminders == []


def test_special_event_with_track_name_sets_location(session):
    user = User(name="Sam", email="sam@example.com")
    session.add(user)
    session.commit()

    event = SpecialEvent(
        year=2026, name="Bathurst 12",
        date_start=datetime(2026, 2, 20), date_end=datetime(2026, 2, 22),
        track_name="Mount Panorama Circuit",
    )
    session.add(event)
    session.commit()

    selection = Selection(user_id=user.id, special_event_id=event.id)
    session.add(selection)
    session.commit()
    session.refresh(user)

    calendar = build_calendar(session, user)
    events = list(calendar.walk("VEVENT"))

    assert events[0]["location"] == "Mount Panorama Circuit"
