from pathlib import Path

from pipeline.parsers.logos_page import parse_logos_page

FIXTURE = Path(__file__).parent / "fixtures" / "logos_page_sample.html"


def test_finds_special_event_and_official_series_zip_links():
    special_events_zip_url, championship_zip_url = parse_logos_page(FIXTURE.read_text())

    assert special_events_zip_url == "https://s100.iracing.com/wp-content/uploads/2026/03/2026-SpecialEvent-Logos-031626.zip"
    assert championship_zip_url == "https://s100.iracing.com/wp-content/uploads/2026/03/Official_Series_Logos_2026_S2-031626.zip"


def test_ignores_unrelated_boxes():
    # Brandmarks and eSports boxes also have "Download" buttons but shouldn't match.
    special_events_zip_url, championship_zip_url = parse_logos_page(FIXTURE.read_text())

    assert "Brandmarks" not in (special_events_zip_url or "")
    assert "eSports" not in (championship_zip_url or "")


def test_missing_boxes_return_none_rather_than_raising():
    special_events_zip_url, championship_zip_url = parse_logos_page("<html><body>no boxes here</body></html>")

    assert special_events_zip_url is None
    assert championship_zip_url is None
