from datetime import time

import pytest

from app.parsers.race_cadence import parse_cadence

# Every distinct "Races every ..." phrasing found in the real official 2026 S3
# schedule PDF, mapped to the expected mark count (verified by hand for the
# representative cases, spot-checked for the rest).
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
]

IRREGULAR_PATTERNS = [
    "Races every other Saturday at 2, 7, 18 GMT and Sunday at 14 GMT",
    "Races every other Saturday at 4 & 15 GMT and Sunday at 0 GMT, 20 GMT",
    "Races every other Saturday at 7, 18 GMT and Sunday at 14 GMT",
]


@pytest.mark.parametrize("text,expected_count", REAL_PATTERNS_AND_COUNTS)
def test_parses_all_known_regular_cadence_phrasings(text, expected_count):
    result = parse_cadence(text)
    assert result is not None
    assert len(result) == expected_count
    assert result == sorted(result)
    assert len(set(result)) == len(result)  # no duplicate marks


@pytest.mark.parametrize("text", IRREGULAR_PATTERNS)
def test_irregular_day_specific_cadences_return_none_rather_than_guess(text):
    assert parse_cadence(text) is None


def test_nascar_class_a_series_example_matches_worked_case():
    """The exact case from the schedule PDF: NASCAR Class A Series (2026 S3, page
    60) is "Races every even 2 hours at :30 past" — user should only be able to
    pick 00:30, 02:30, 04:30 GMT, etc."""
    result = parse_cadence("Races every even 2 hours at :30 past")
    assert result == [
        time(0, 30), time(2, 30), time(4, 30), time(6, 30), time(8, 30), time(10, 30),
        time(12, 30), time(14, 30), time(16, 30), time(18, 30), time(20, 30), time(22, 30),
    ]


def test_plain_thirty_minute_cadence_defaults_to_on_the_hour_and_half_past():
    result = parse_cadence("Races every 30 minutes")
    assert result[0] == time(0, 0)
    assert result[1] == time(0, 30)
    assert result[-1] == time(23, 30)


def test_unrecognized_text_returns_none():
    assert parse_cadence("No cadence information available") is None
