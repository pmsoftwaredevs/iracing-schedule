from app.matcher import match_series
from app.models import Series


def _series(id_: int, name: str) -> Series:
    return Series(id=id_, season_id=1, name=name)


def test_exact_name_match():
    old = [_series(1, "GT3 Fixed")]
    new = [_series(10, "GT3 Fixed"), _series(11, "Formula Vee")]

    result = match_series(old, new)

    assert [(o.id, n.id) for o, n in result.matched] == [(1, 10)]
    assert result.unmatched == []


def test_case_and_punctuation_insensitive_match():
    old = [_series(1, "IMSA Continental Tire Series - Fixed")]
    new = [_series(10, "imsa continental tire series fixed")]

    result = match_series(old, new)

    assert [(o.id, n.id) for o, n in result.matched] == [(1, 10)]


def test_no_match_falls_into_unmatched():
    old = [_series(1, "Retired Series")]
    new = [_series(10, "GT3 Fixed")]

    result = match_series(old, new)

    assert result.matched == []
    assert [s.id for s in result.unmatched] == [1]


def test_ambiguous_normalized_match_is_left_unmatched():
    old = [_series(1, "GT-3 Fixed")]
    new = [_series(10, "GT3 Fixed"), _series(11, "GT 3 Fixed")]

    result = match_series(old, new)

    assert result.matched == []
    assert [s.id for s in result.unmatched] == [1]
