from pipeline.logo_matching import match_logos


def test_exact_name_match():
    result = match_logos(
        {"knoxville-nationals": ["Knoxville Nationals"]},
        ["iRSE-2026-Knoxville-Nationals.png"],
    )
    assert result == {"knoxville-nationals": "iRSE-2026-Knoxville-Nationals.png"}


def test_below_threshold_is_left_unmatched():
    result = match_logos(
        {"chili-bowl": ["Chili Bowl"]},
        ["iRSE-2026-Generic.png"],
    )
    assert result == {}


def test_camel_case_filename_matches_spaced_name():
    # "Mission R Challenge" vs a filename that squashes "MissionR" together —
    # naive tokenizing would treat "missionr" and "mission"/"r" as unrelated.
    result = match_logos(
        {"Mission R Challenge": ["Mission R Challenge"]},
        ["Sports Car/MissionR Challenge.png", "Sports Car/Prototype Challenge.png"],
    )
    assert result == {"Mission R Challenge": "Sports Car/MissionR Challenge.png"}


def test_global_greedy_resolves_collisions_correctly():
    # "iRacing ROAR" and "SCCA Runoffs" both share the word "iracing"/"runoffs" with
    # the wrong file if matched independently and greedily per-candidate; matching
    # must be resolved globally so the best pairing overall wins, not whichever
    # candidate happened to be scored first.
    result = match_logos(
        {
            "iracing-roar": ["iRacing ROAR"],
            "scca-runoffs": ["SCCA Runoffs"],
        },
        ["iRSE-2026-ROAR.png", "iRSE-2026-iRacing-Runoffs.png"],
    )
    assert result == {
        "iracing-roar": "iRSE-2026-ROAR.png",
        "scca-runoffs": "iRSE-2026-iRacing-Runoffs.png",
    }


def test_typo_in_filename_still_finds_unique_best_match():
    result = match_logos(
        {"watkins-glen-6hr": ["Watkins Glen 6 Hour"]},
        ["iRSE-2026-Watkis-Glen-6H-VCO.png"],
    )
    assert result == {"watkins-glen-6hr": "iRSE-2026-Watkis-Glen-6H-VCO.png"}


def test_matches_against_any_variant_slug_or_name():
    result = match_logos(
        {"thruxton-4hrs": ["4 Hours at Thruxton", "thruxton 4hrs"]},
        ["iRSE-2026-4-Hours-at-Thruxton.png"],
    )
    assert result == {"thruxton-4hrs": "iRSE-2026-4-Hours-at-Thruxton.png"}


def test_each_filename_used_at_most_once():
    result = match_logos(
        {
            "a": ["Daytona 24"],
            "b": ["Daytona 500"],
        },
        ["iRSE-2026-Daytona-24-VCO.png"],
    )
    assert len(result) == 1
    assert set(result.values()) <= {"iRSE-2026-Daytona-24-VCO.png"}


def test_no_candidates_or_no_filenames_returns_empty():
    assert match_logos({}, ["a.png"]) == {}
    assert match_logos({"x": ["X"]}, []) == {}


def test_single_letter_class_designators_are_not_dropped():
    # A naive "drop single-char tokens" rule would make "Class B" and "Class C"
    # indistinguishable and let them swap onto each other's logo.
    result = match_logos(
        {
            "NASCAR Class B Series": ["NASCAR Class B Series"],
            "NASCAR Class C Series": ["NASCAR Class C Series"],
        },
        [
            "NASCAR Class B - Xfinity Series.png",
            "NASCAR Class C - Truck Series.png",
        ],
    )
    assert result == {
        "NASCAR Class B Series": "NASCAR Class B - Xfinity Series.png",
        "NASCAR Class C Series": "NASCAR Class C - Truck Series.png",
    }


def test_iracing_and_irse_noise_stripped_without_eating_real_words():
    # "iRacing"/"iRSE" are glued onto nearly every filename with no word boundary
    # and must not leak "racing"/"rse" tokens that could coincidentally match
    # something else once stripped.
    result = match_logos(
        {"iracing-roar": ["iRacing ROAR"]},
        ["iRSE-2026-ROAR.png", "iRSE-2026-iRacing-Runoffs.png"],
    )
    assert result == {"iracing-roar": "iRSE-2026-ROAR.png"}
