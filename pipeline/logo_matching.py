"""Fuzzy-matches iRacing's zipped logo-pack filenames to championship/special-event
names parsed elsewhere. There's no stable id linking a logo file to a series or
event — iRacing's logo packs are just a folder of PNGs named close to, but not
exactly, the on-site display name (sponsor tags and "Fixed"/"Open" suffixes appear
in one side and not the other, plus the odd typo — e.g. "Watkis" for "Watkins").

Matching is therefore inherently best-effort: a token-overlap (Jaccard) score
between each candidate's normalized text(s) and each filename, assigned greedily
best-score-first across the whole set (so a strong match elsewhere can't be starved
by being considered after a weaker one), with anything below MIN_SCORE left
unmatched entirely — showing no logo beats showing the wrong one.
"""

import re

# Substrings that are pure noise wherever they appear — "iRacing"/"iRSE" ("iRacing
# Special Event") are glued onto nearly every filename with no word boundary
# (e.g. "iRSE-2026-iRacing-Runoffs.png"), so they're stripped before tokenizing
# rather than relying on a token-level stopword to catch every split variant.
_NOISE_SUBSTRING_RE = re.compile(r"iracing|irse", re.IGNORECASE)

# Whole tokens common to nearly every entry on both sides that add noise, not
# signal: generic filler plus filename-only sponsor/edition tags.
STOPWORDS = {
    "series", "by", "the", "presented", "season", "of", "logo",
    "vco", "pimax", "conspit", "hour", "hours", "hr", "hrs",
    "powered", "initial", "bop",
}

MIN_SCORE = 0.3

# "6h"/"24hr" (filename) vs "6"/"24" (a "6 Hour" name already stripped of "hour"
# by STOPWORDS) — same underlying number, just a unit glued onto the digits with no
# word boundary for STOPWORDS to strip on its own.
_UNIT_SUFFIX_RE = re.compile(r"^(\d{1,3})(?:h|hr|hrs)$")
_EXTENSION_RE = re.compile(r"\.(?:png|jpe?g)$", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    text = _EXTENSION_RE.sub("", text)
    text = _NOISE_SUBSTRING_RE.sub(" ", text)
    # Filenames sometimes squash words together in TitleCase (e.g. "MissionR",
    # "ProtoGT") where the display name has a space ("Mission R", "Proto-GT") —
    # split those boundaries before tokenizing so both sides produce the same
    # tokens. Single letters are kept (not dropped as split debris): "Class B" vs
    # "Class C" need that letter to tell two real championships apart.
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    tokens = set()
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        if raw in STOPWORDS:
            continue
        match = _UNIT_SUFFIX_RE.match(raw)
        tokens.add(match.group(1) if match else raw)
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_logos(candidates: dict[str, list[str]], filenames: list[str]) -> dict[str, str]:
    """`candidates` maps a stable key (slug, or championship name) to one or more
    display-text variants to match against (e.g. name + slug for special events).
    `filenames` are paths/names from the logo pack ZIP (only the basename is used).
    Returns key -> filename for confident matches only; unmatched keys are omitted."""
    file_tokens = {f: _tokens(f.rsplit("/", 1)[-1]) for f in filenames}
    candidate_tokens = {key: [_tokens(text) for text in texts] for key, texts in candidates.items()}

    scored = [
        (max(_jaccard(tokens, file_tokens[f]) for tokens in variants), key, f)
        for key, variants in candidate_tokens.items()
        for f in filenames
    ]
    scored.sort(key=lambda item: -item[0])

    matched_keys: set[str] = set()
    matched_files: set[str] = set()
    result: dict[str, str] = {}
    for score, key, f in scored:
        if score < MIN_SCORE or key in matched_keys or f in matched_files:
            continue
        matched_keys.add(key)
        matched_files.add(f)
        result[key] = f
    return result
