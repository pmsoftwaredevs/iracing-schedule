from datetime import time

import pytest

from pipeline.parsers.race_cadence import parse_cadence

# Every distinct "Races every ..." phrasing found in the real official 2026 S3
# schedule PDF, mapped to the expected mark count (verified by hand for the
# representative cases, spot-checked for the rest). These are all "same time
# every day" cadences, so flat_times() covers them fully.
REAL_PATTERNS_AND_COUNTS = [
    ("Races every 2 hours at :00", 12),
    ("Races every 2 hours at :15 past", 12),
    ("Races every 2 hours at :30", 12),
    ("Races every 2 hours at :30 past", 12),
    ("Races every 2 hours at :45", 12),
    ("Races every 2 hours at :45 past", 12),
    ("Races every 2 hours at the :30", 12),
    ("Races every 2 hours on the hour", 12),
    ("Races every 30 minutes", 48),
    ("Races every 30 minutes at :00 & 30", 48),
    ("Races every 30 minutes at :00 & :30", 48),
    ("Races every 30 minutes at :15 & :45", 48),
    ("Races every 30 minutes at :15 and :45", 48),
    ("Races every 30 minutes at :15 and :45 past", 48),
    ("Races every even 2 hours at :30 past", 12),
    ("Races every even 2 hours on the hour", 12),
    ("Races every hour at :00", 24),
    ("Races every hour at :15", 24),
    ("Races every hour at :15 after", 24),
    ("Races every hour at :15 past", 24),
    ("Races every hour at :30", 24),
    ("Races every hour at :30 past", 24),
    ("Races every hour at :45", 24),
    ("Races every hour at :45 past", 24),
    ("Races every hour at half past", 24),
    ("Races every hour on the hour", 24),
    ("Races every odd 2 hours on the hour", 12),
    ("Races every thirty minutes on the hour and :30 past", 48),
    # "hourly" is a synonym for "every hour"; offsets can precede the interval
    # spec instead of following it; "on"/"at" before "every" is just filler.
    ("Races hourly at :00", 24),
    ("Races hourly at :15", 24),
    ("Races hourly at :30", 24),
    ("Races hourly at :45", 24),
    ("Races hourly at :45 past", 24),
    ("Races hourly at the top of the hour", 24),
    ("Races hourly on the 00", 24),
    ("Races on every hour on the hour", 24),
    ("Races on the hour every hour", 24),
    ("Races at every hour at :15", 24),
    ("Races 45 past every 2 hours", 12),
    ("Races at 15 past every 2 hours", 12),
]

# Day-specific phrasings ("every other <day>" biweekly ones included — the
# biweekly-ness itself doesn't need parsing here, see pipeline/parsers/race_cadence.py's
# module docstring) mapped to the expected {day_of_week: [times]} result.
DAY_SPECIFIC_PATTERNS = [
    (
        "Races Thur & Sat at 10, 19 GMT & Fri & Sun at 1,4 GMT",
        {
            3: [time(10, 0), time(19, 0)],
            5: [time(10, 0), time(19, 0)],
            4: [time(1, 0), time(4, 0)],
            6: [time(1, 0), time(4, 0)],
        },
    ),
    (
        "Races Fri & Sun at 10, 19 GMT & Sat & Mon at 1, 4 GMT",
        {
            4: [time(10, 0), time(19, 0)],
            6: [time(10, 0), time(19, 0)],
            5: [time(1, 0), time(4, 0)],
            0: [time(1, 0), time(4, 0)],
        },
    ),
    (
        "Races every other Saturday at 2, 7, 18 GMT and Sunday at 14 GMT",
        {5: [time(2, 0), time(7, 0), time(18, 0)], 6: [time(14, 0)]},
    ),
    (
        "Races every other Saturday at 4 & 15 GMT and Sunday at 0 GMT, 20 GMT",
        {5: [time(4, 0), time(15, 0)], 6: [time(0, 0), time(20, 0)]},
    ),
    (
        "Races every other Saturday at 7, 18 GMT and Sunday at 14 GMT",
        {5: [time(7, 0), time(18, 0)], 6: [time(14, 0)]},
    ),
    # Comma-separated clauses, each self-contained with its own "at".
    (
        "Races Friday at 19 GMT, Saturday at 7 GMT, Sunday at 18 GMT",
        {4: [time(19, 0)], 5: [time(7, 0)], 6: [time(18, 0)]},
    ),
    # "and" joins clauses here (not "&"), each with its own "at".
    (
        "Races Thursday at 10 & 18 GMT and Friday at 00 & 3 GMT",
        {3: [time(10, 0), time(18, 0)], 4: [time(0, 0), time(3, 0)]},
    ),
    # No "at" at all — the time list follows the day name directly.
    (
        "Races Weds 2 GMT, Sat 8 GMT, Sun 19 GMT, & Mon at 18 GMT",
        {2: [time(2, 0)], 5: [time(8, 0)], 6: [time(19, 0)], 0: [time(18, 0)]},
    ),
    # Plural day names, "and" joining times within a clause, no "at".
    (
        "Races Saturdays 9 and 19 GMT and Sundays 17 GMT",
        {5: [time(9, 0), time(19, 0)], 6: [time(17, 0)]},
    ),
    # "and" joins two days sharing one time list, plus a leading "on".
    (
        "Races on Wednesday and Saturdays at 14 and 19 GMT",
        {2: [time(14, 0), time(19, 0)], 5: [time(14, 0), time(19, 0)]},
    ),
]


@pytest.mark.parametrize("text,expected_count", REAL_PATTERNS_AND_COUNTS)
def test_parses_all_known_regular_cadence_phrasings(text, expected_count):
    result = parse_cadence(text)
    assert result is not None
    flat = result.flat_times()
    assert len(flat) == expected_count
    assert flat == sorted(flat)
    assert len(set(flat)) == len(flat)  # no duplicate marks
    # Same times apply every day of the week for these non-day-specific cadences.
    assert set(result.times_by_day.keys()) == set(range(7))
    assert all(times == flat for times in result.times_by_day.values())


@pytest.mark.parametrize("text,expected_times_by_day", DAY_SPECIFIC_PATTERNS)
def test_parses_day_specific_cadence_phrasings(text, expected_times_by_day):
    result = parse_cadence(text)
    assert result is not None
    assert result.times_by_day == expected_times_by_day


def test_nascar_class_a_series_example_matches_worked_case():
    """The exact case from the schedule PDF: NASCAR Class A Series (2026 S3, page
    60) is "Races every even 2 hours at :30 past" — user should only be able to
    pick 00:30, 02:30, 04:30, etc. GMT, on any day of the week."""
    result = parse_cadence("Races every even 2 hours at :30 past")
    assert result.flat_times() == [
        time(0, 30), time(2, 30), time(4, 30), time(6, 30), time(8, 30), time(10, 30),
        time(12, 30), time(14, 30), time(16, 30), time(18, 30), time(20, 30), time(22, 30),
    ]


def test_plain_thirty_minute_cadence_defaults_to_on_the_hour_and_half_past():
    result = parse_cadence("Races every 30 minutes")
    flat = result.flat_times()
    assert flat[0] == time(0, 0)
    assert flat[1] == time(0, 30)
    assert flat[-1] == time(23, 30)


def test_unrecognized_text_returns_none():
    assert parse_cadence("No cadence information available") is None


def test_bare_offsets_with_no_interval_keyword_default_to_hourly():
    """"Races at :15 and :45" (FIA Cross Car Championship) has no "every"/"hourly"
    keyword at all — treated as implicitly hourly, same net result as the
    explicit "Races every 30 minutes at :15 and :45" pattern elsewhere in the PDF."""
    result = parse_cadence("Races at :15 and :45")
    assert result is not None
    flat = result.flat_times()
    assert len(flat) == 48
    assert flat[0] == time(0, 15)
    assert flat[1] == time(0, 45)
    assert flat[-1] == time(23, 45)
