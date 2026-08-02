from datetime import datetime
from pathlib import Path

from pipeline.parsers.special_events_page import parse_special_events_page

FIXTURE = Path(__file__).parent / "fixtures" / "special_events_page_sample.html"


def test_parses_real_page_and_finds_all_dated_events():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    # 33 sections exist on the real page; one ("Dale Jr Charity Event") has no
    # announced date yet ("Date : TBD") and is correctly excluded, not guessed.
    assert len(events) == 32
    assert "Dale Jr Charity Event" not in by_name


def test_cross_month_event_parses_correctly():
    """Firecracker 400 spans June 30 - July 6 — a genuine cross-month event that a
    single-month day-range model can't represent at all."""
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    firecracker = by_name["Firecracker 400"]
    assert firecracker.date_start == datetime(2026, 6, 30)
    assert firecracker.date_end == datetime(2026, 7, 6)
    assert firecracker.car_class == "1987 NASCAR Cup"


def test_same_month_event_parses_correctly():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    bathurst = by_name["Bathurst 12"]
    assert bathurst.date_start == datetime(2026, 2, 20)
    assert bathurst.date_end == datetime(2026, 2, 22)
    assert bathurst.track_name == "Mount Panorama Circuit"


def test_abbreviated_month_name_parses_correctly():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    ff1600 = by_name["iRacing FF1600 Festival"]
    assert ff1600.date_start == datetime(2026, 10, 30)
    assert ff1600.date_end == datetime(2026, 10, 31)


def test_track_link_is_optional():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    # Daytona 500's body text doesn't happen to hyperlink the track name.
    assert by_name["Daytona 500"].track_name is None


def test_more_info_link_extracted_and_domain_checked():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    bathurst = by_name["Bathurst 12"]
    assert bathurst.link_url == "https://forums.iracing.com/discussion/92109/bathurst-12-hour-presented-by-rss/p1/"


def test_more_info_link_is_optional():
    events = parse_special_events_page(FIXTURE.read_text())
    by_name = {e.name: e for e in events}

    # Firecracker 400's section has no "MORE INFO" button in the fixture.
    assert by_name["Firecracker 400"].link_url is None
