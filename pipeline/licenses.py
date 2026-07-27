"""iRacing's license ladder — shared between the schedule PDF parser (deriving each
series' effective license tier, pipeline/parsers/schedule_pdf.py) and the picker
UI (rendering a badge + filter per tier, docs/app.js), so the ladder order and
colors live in exactly one place conceptually (duplicated as a small JS constant
in docs/app.js, since the browser can't import Python).

Colors are iRacing's own per-license colors, not arbitrary picks.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseTier:
    code: str  # short form shown on-screen: R/D/C/B/A/P
    name: str
    color: str  # hex background
    text_color: str  # hex text color readable on top of `color`


# Ladder order, lowest to highest — also what app/parsers/schedule_pdf.py walks
# when bumping a series up a tier (see its _license_level docstring).
LICENSE_TIERS: list[LicenseTier] = [
    LicenseTier("R", "Rookie", "#E1251B", "#ffffff"),
    LicenseTier("D", "Class D", "#FF6600", "#ffffff"),
    LicenseTier("C", "Class C", "#FFCC00", "#1a1d1a"),
    LicenseTier("B", "Class B", "#217C06", "#ffffff"),
    LicenseTier("A", "Class A", "#006EFF", "#ffffff"),
    LicenseTier("P", "Pro/World Champion", "#000000", "#ffffff"),
]

LICENSE_ORDER: list[str] = [tier.code for tier in LICENSE_TIERS]
LICENSE_BY_CODE: dict[str, LicenseTier] = {tier.code: tier for tier in LICENSE_TIERS}
