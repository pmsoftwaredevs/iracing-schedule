from datetime import datetime
from pathlib import Path

from app.parsers.seasons_page import current_season, parse_seasons_page

FIXTURE = Path(__file__).parent / "fixtures" / "seasons_page_sample.html"


def test_parse_seasons_page_matches_known_2026_dates():
    seasons = parse_seasons_page(FIXTURE.read_text())
    by_key = {(s.year, s.quarter): s.start_date for s in seasons}

    assert by_key[(2026, 1)] == datetime(2025, 12, 9)
    assert by_key[(2026, 2)] == datetime(2026, 3, 10)
    assert by_key[(2026, 3)] == datetime(2026, 6, 9)


def test_parse_seasons_page_matches_known_2025_dates():
    seasons = parse_seasons_page(FIXTURE.read_text())
    by_key = {(s.year, s.quarter): s.start_date for s in seasons}

    assert by_key[(2025, 1)] == datetime(2024, 12, 10)
    assert by_key[(2025, 2)] == datetime(2025, 3, 11)
    assert by_key[(2025, 3)] == datetime(2025, 6, 10)
    assert by_key[(2025, 4)] == datetime(2025, 9, 9)


def test_current_season_button_has_broken_href_but_parses_correctly_from_text():
    """Regression test: iRacing's own page has the current/"LATEST" season's button
    pointing at the WRONG href (previous season's slug) while the visible text is
    correct. The parser must not silently pick up the wrong quarter number."""
    seasons = parse_seasons_page(FIXTURE.read_text())
    by_key = {(s.year, s.quarter): s.start_date for s in seasons}

    assert (2026, 3) in by_key
    quarters_2026 = sorted(s.quarter for s in seasons if s.year == 2026)
    assert quarters_2026 == [1, 2, 3]


def test_current_season_picks_max_start_date_not_exceeding_as_of():
    seasons = parse_seasons_page(FIXTURE.read_text())

    result = current_season(seasons, as_of=datetime(2026, 7, 23))

    assert (result.year, result.quarter) == (2026, 3)
    assert result.start_date == datetime(2026, 6, 9)


def test_current_season_before_any_known_start_returns_none():
    seasons = parse_seasons_page(FIXTURE.read_text())

    result = current_season(seasons, as_of=datetime(2019, 1, 1))

    assert result is None


def test_current_season_between_two_seasons_picks_earlier_one():
    seasons = parse_seasons_page(FIXTURE.read_text())

    result = current_season(seasons, as_of=datetime(2026, 1, 1))

    assert (result.year, result.quarter) == (2026, 1)
