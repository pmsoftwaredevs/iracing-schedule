"""Matches a user's prior-season Series selections against a new season's Series list.

Series persist across seasons under stable-ish names (e.g. "GT3 Fixed"), so matching
is done by exact name first, then a normalized/loose comparison as a fallback. Special
event selections are not carried over automatically since events are the same by
year, not season, and are matched by name against SpecialEvent.
"""

import re
from dataclasses import dataclass, field

from app.models import Series


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@dataclass
class MatchResult:
    matched: list[tuple[Series, Series]] = field(default_factory=list)  # (old, new)
    unmatched: list[Series] = field(default_factory=list)  # old series with no match


def match_series(old_series: list[Series], new_series: list[Series]) -> MatchResult:
    result = MatchResult()

    by_exact_name = {s.name: s for s in new_series}
    by_normalized_name: dict[str, list[Series]] = {}
    for s in new_series:
        by_normalized_name.setdefault(_normalize(s.name), []).append(s)

    for old in old_series:
        new = by_exact_name.get(old.name)
        if new is None:
            candidates = by_normalized_name.get(_normalize(old.name), [])
            new = candidates[0] if len(candidates) == 1 else None

        if new is not None:
            result.matched.append((old, new))
        else:
            result.unmatched.append(old)

    return result
