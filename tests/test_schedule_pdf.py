from datetime import datetime
from pathlib import Path

from pipeline.parsers.schedule_pdf import (
    CATEGORY_HEADER_RE,
    _cadence_text,
    _is_continuation_table,
    _license_level,
    _match_series_link,
    _parse_row,
    _series_name,
    _track_name,
    parse_schedule_pdf,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_schedule_page.pdf"


def test_parse_row_extracts_week_track_and_derives_end_date():
    row = [
        "Week 1 (2025-09-16)",
        "Charlotte Motor Speedway - Legends Oval\n(2025-09-20 12:00 1x)",
        "70°F/21°C, Rain chance None, Rolling\nstart, Cautions disabled, Qual scrutiny\n- Permissive.",
        "40 laps",
    ]

    parsed = _parse_row(row)

    assert parsed.week_number == 1
    assert parsed.track_name == "Charlotte Motor Speedway - Legends Oval"
    assert parsed.date_start == datetime(2025, 9, 16)
    assert parsed.date_end == datetime(2025, 9, 22)  # Tuesday-to-Monday, 6 days later
    assert parsed.duration_minutes is None  # "40 laps" states laps, not a duration


def test_parse_row_extracts_explicit_duration_in_minutes():
    row = [
        "Week 1 (2026-06-27)",
        "Watkins Glen International - Boot\n(2026-06-27 15:15 1x)",
        "76°F/24°C, Rain chance None, Grid by\nclass, Rolling start.",
        "120\nmins",
    ]

    parsed = _parse_row(row)

    assert parsed.duration_minutes == 120


def test_parse_row_returns_none_for_non_week_rows():
    assert _parse_row(["Not a week row", "Track", "Weather", "Laps"]) is None
    assert _parse_row(["", "", ""]) is None
    assert _parse_row([None, None]) is None


def test_track_name_strips_embedded_datetime_line():
    assert _track_name("Spa-Francorchamps\n(2026-01-06 19:00 1x)") == "Spa-Francorchamps"


def test_series_name_strips_season_suffix_and_extra_lines():
    header = "Rookie Legends Cup by Simshop - 2025 Season 4\nLegends Ford '34 Coupe\nRookie (1.0) --> Pro/WC (4.0)"
    assert _series_name(header) == "Rookie Legends Cup by Simshop"


def test_cadence_text_extracted_from_header_cell():
    header = (
        "NASCAR Class A Series - 2026 Season 3\nNASCAR Cup Series Next Gen Chevrolet Camaro ZL1\n"
        "Class B 4.0 --> Pro/WC 4.0\nRaces every even 2 hours at :30 past\n"
        "Min entries for official: 6 | Split at: 30 | Drops: 4"
    )
    assert _cadence_text(header) == "Races every even 2 hours at :30 past"


def test_cadence_text_empty_when_no_cadence_line_present():
    assert _cadence_text("Some Series - 2026 Season 3\nNo cadence info here") == ""


def test_cadence_text_drops_trailing_qualifying_clause():
    """A "| Qualifying ..." suffix describes qualifying, not the race itself —
    dropped so it doesn't get parsed as extra race session times."""
    header = (
        "Ring Meister by LVRY - 2026 Season 3\nSome car\nClass D 4.0\n"
        "Races on every hour on the hour | Qualifying every hour at :30\n"
        "Min entries for official: 6"
    )
    assert _cadence_text(header) == "Races on every hour on the hour"


def test_cadence_text_extracted_from_day_specific_header_cell():
    """Day-specific cadences (e.g. Formula A - Grand Prix Tour) don't contain the
    word "every" at all, unlike interval-based ones — the extraction regex must
    anchor on "Races" alone, not "Races every"."""
    header = (
        "Formula A - Grand Prix Tour - Fixed - 2026 Season\nMercedes-AMG W13 E Performance\n"
        "Class D 4.0 --> Pro/WC 4.0\nRaces Thur & Sat at 10, 19 GMT & Fri & Sun at 1,4 GMT\n"
        "Min entries for official: 6 | Split at: 24 | Drops: 4"
    )
    assert _cadence_text(header) == "Races Thur & Sat at 10, 19 GMT & Fri & Sun at 1,4 GMT"


def test_license_level_rookie_stays_rookie_with_no_lower_tier():
    header = "Rookie Legends Cup by Simshop - 2025 Season 4\nLegends Ford '34 Coupe\nRookie (1.0) --> Pro/WC (4.0)"
    assert _license_level(header) == "R"


def test_license_level_bumped_up_a_tier_when_from_sr_is_the_promotion_threshold():
    """A "Class C (4.0) --> Pro/WC (4.0)" line means a Class C driver at exactly the
    auto-promotion threshold can enter — i.e. this is effectively a Class B series,
    not Class C, so the displayed tier is bumped up one."""
    header = (
        "NASCAR Class A Series - 2026 Season 3\nNASCAR Cup Series Next Gen Chevrolet Camaro ZL1\n"
        "Class B 4.0 --> Pro/WC 4.0\nRaces every even 2 hours at :30 past"
    )
    assert _license_level(header) == "A"


def test_license_level_not_bumped_when_below_promotion_threshold():
    header = "Some Series - 2026 Season 3\nSome car\nClass C (2.0) --> Pro/WC (4.0)\nRaces every hour"
    assert _license_level(header) == "C"


def test_license_level_top_tier_has_nothing_to_bump_into():
    header = "Pro Series - 2026 Season 3\nSome car\nPro/WC (4.0) --> Pro/WC (4.0)\nRaces every hour"
    assert _license_level(header) == "P"


def test_license_level_without_arrow_still_parses_from_side():
    header = "Ring Meister by LVRY - 2026 Season 3\nSome car\nClass D 4.0\nRaces on every hour on the hour"
    assert _license_level(header) == "C"


def test_license_level_empty_when_no_license_line_present():
    assert _license_level("Some Series - 2026 Season 3\nNo license info here") == ""


def test_continuation_table_detected_and_not_treated_as_new_series():
    header_table = [
        ["GT3 Fixed - 2025 Season 4\nSome GT3 car", None, None, None],
        ["Week 1 (2025-09-16)", "Spa-Francorchamps\n(2025-09-20 19:00 1x)", "weather", "40 laps"],
    ]
    continuation_table = [
        ["Week 11 (2025-11-25)", "Watkins Glen\n(2025-11-29 19:00 1x)", "weather", "30 laps"],
    ]

    assert not _is_continuation_table(header_table)
    assert _is_continuation_table(continuation_table)


def test_category_header_re_extracts_category_from_class_series_line():
    match = CATEGORY_HEADER_RE.search("R Class Series (SPORTS CAR)")
    assert match is not None
    assert match.group(1) == "SPORTS CAR"


def test_category_header_re_does_not_match_bare_category_heading():
    """The standalone heading above the class-series line (e.g. a lone "OVAL"
    line) carries no parenthetical and must not match — only the "<Letter> Class
    Series (<CATEGORY>)" line is used as the category source."""
    assert CATEGORY_HEADER_RE.search("OVAL") is None


def test_parse_schedule_pdf_against_real_fixture_page():
    series_list = parse_schedule_pdf(str(FIXTURE))

    assert len(series_list) == 1
    series = series_list[0]
    assert series.name == "Rookie Legends Cup by Simshop"
    assert series.cadence_text == "Races every 30 minutes"
    assert series.license_level == "R"
    # The fixture is a single page lifted from mid-document, with no "<Letter>
    # Class Series (<CATEGORY>)" line on it to source a category from.
    assert series.category == ""
    assert len(series.weeks) == 12
    assert series.weeks[0].week_number == 1
    assert series.weeks[0].track_name == "Charlotte Motor Speedway - Legends Oval"
    assert series.weeks[0].date_start == datetime(2025, 9, 16)
    assert series.weeks[-1].week_number == 12
    # The fixture page carries no "go racing" link annotation.
    assert series.link_url is None


def test_match_series_link_picks_annot_closest_to_header_top():
    annots = [
        {"top": 40.0, "uri": "http://members-ng.iracing.com/.../1111/go-racing"},
        {"top": 167.95, "uri": "http://members-ng.iracing.com/.../6346/go-racing"},
    ]
    assert _match_series_link(annots, table_top=162.0) == "http://members-ng.iracing.com/.../6346/go-racing"


def test_match_series_link_returns_none_when_nothing_within_tolerance():
    annots = [{"top": 500.0, "uri": "http://members-ng.iracing.com/.../1111/go-racing"}]
    assert _match_series_link(annots, table_top=162.0) is None


def test_match_series_link_returns_none_with_no_annots():
    assert _match_series_link([], table_top=162.0) is None
