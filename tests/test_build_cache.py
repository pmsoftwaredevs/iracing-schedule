import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from pipeline.config import Settings
from pipeline.parsers.schedule_pdf import ParsedSeries, ParsedWeek
from pipeline.parsers.seasons_page import ParsedSeason
from pipeline.parsers.special_events_page import ParsedSpecialEvent
from tools import build_cache


class _FakeResponse:
    def __init__(self, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        pass


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _week(n: int, track: str, start: str) -> ParsedWeek:
    date_start = datetime.fromisoformat(start)
    return ParsedWeek(week_number=n, track_name=track, date_start=date_start, date_end=date_start)


def _series(name: str, weeks: list[ParsedWeek] | None = None) -> ParsedSeries:
    return ParsedSeries(name=name, cadence_text="Races every even 2 hours at :30 past", weeks=weeks or [])


def _event(slug: str, name: str) -> ParsedSpecialEvent:
    return ParsedSpecialEvent(
        slug=slug,
        name=name,
        date_start=datetime(2026, 6, 30),
        date_end=datetime(2026, 7, 6),
        track_name=None,
        track_path=None,
        car_class=None,
        link_url=None,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        seasons_page_url="https://example.invalid/seasons",
        schedule_pdf_url="https://example.invalid/schedule.pdf",
        special_events_page_url="https://example.invalid/special-events",
    )


def _patch_common(monkeypatch, *, season: ParsedSeason, series: list[ParsedSeries], events: list[ParsedSpecialEvent]):
    monkeypatch.setattr(build_cache.httpx, "get", lambda *a, **k: _FakeResponse())
    monkeypatch.setattr(build_cache, "parse_seasons_page", lambda html: [season])
    monkeypatch.setattr(build_cache, "current_season", lambda seasons, as_of: season)
    monkeypatch.setattr(build_cache, "fetch_schedule_pdf", lambda *a, **k: "fake.pdf")
    monkeypatch.setattr(build_cache, "parse_schedule_pdf", lambda path: series)
    monkeypatch.setattr(build_cache, "parse_special_events_page", lambda html: events)


def test_reconcile_preserves_order_and_appends_new():
    existing = [{"name": "A", "v": 1}, {"name": "B", "v": 1}]
    fresh = [{"name": "B", "v": 2}, {"name": "A", "v": 2}, {"name": "C", "v": 2}]
    result = build_cache._reconcile(existing, fresh, key="name")
    assert [r["name"] for r in result] == ["A", "B", "C"]
    # data refreshed from the fresh parse, position kept from existing
    assert result[0]["v"] == 2
    assert result[1]["v"] == 2


def test_reconcile_freezes_entry_missing_from_fresh_instead_of_dropping():
    existing = [{"name": "A", "v": 1}, {"name": "B", "v": 1}]
    fresh = [{"name": "A", "v": 2}]  # B no longer parses this run
    result = build_cache._reconcile(existing, fresh, key="name")
    assert [r["name"] for r in result] == ["A", "B"]
    assert result[1]["v"] == 1  # frozen, not dropped — index stability


def test_season_code_and_slug_helpers():
    assert build_cache.season_code(2026, 3) == "2026S3"
    assert build_cache.season_slug(2026, 3) == "2026_s3"
    assert build_cache._slug_from_code("2026S3") == "2026_s3"


def test_build_cache_first_run_writes_current_only(tmp_path, settings, monkeypatch):
    season = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    series = [_series("GT3 Fixed", [_week(1, "Charlotte", "2026-07-07")])]
    events = [_event("firecracker-400", "Firecracker 400")]
    _patch_common(monkeypatch, season=season, series=series, events=events)

    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest == {"current": "2026S3", "previous": None}

    current = json.loads((tmp_path / "2026_s3.json").read_text())
    assert current["season"]["code"] == "2026S3"
    assert [c["name"] for c in current["championships"]] == ["GT3 Fixed"]
    assert [e["slug"] for e in current["special_events"]] == ["firecracker-400"]
    assert current["championships"][0]["session_time_options"]


def test_build_cache_rerun_same_season_preserves_index_and_appends_new(tmp_path, settings, monkeypatch):
    season = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    first_series = [_series("GT3 Fixed"), _series("Formula B")]
    _patch_common(monkeypatch, season=season, series=first_series, events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    # Second run: same season, one new series appears. Existing order must be preserved.
    second_series = [_series("Formula B"), _series("New Series"), _series("GT3 Fixed")]
    _patch_common(monkeypatch, season=season, series=second_series, events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    current = json.loads((tmp_path / "2026_s3.json").read_text())
    assert [c["name"] for c in current["championships"]] == ["GT3 Fixed", "Formula B", "New Series"]


def test_build_cache_rerun_with_no_real_changes_keeps_generated_at(tmp_path, settings, monkeypatch):
    season = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    series = [_series("GT3 Fixed")]
    _patch_common(monkeypatch, season=season, series=series, events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")
    first_generated_at = json.loads((tmp_path / "2026_s3.json").read_text())["generated_at"]

    # Re-run with an identical parse — only the timestamp would differ.
    _patch_common(monkeypatch, season=season, series=series, events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    current = json.loads((tmp_path / "2026_s3.json").read_text())
    assert current["generated_at"] == first_generated_at


def test_build_cache_rerun_with_real_changes_updates_generated_at(tmp_path, settings, monkeypatch):
    season = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    _patch_common(monkeypatch, season=season, series=[_series("GT3 Fixed")], events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")
    first_generated_at = json.loads((tmp_path / "2026_s3.json").read_text())["generated_at"]

    class _LaterDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + timedelta(days=1)

    _patch_common(monkeypatch, season=season, series=[_series("GT3 Fixed"), _series("Formula B")], events=[])
    monkeypatch.setattr(build_cache, "datetime", _LaterDatetime)
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    current = json.loads((tmp_path / "2026_s3.json").read_text())
    assert current["generated_at"] != first_generated_at


def test_build_cache_rollover_freezes_previous_and_prunes_older(tmp_path, settings, monkeypatch):
    season_s2 = ParsedSeason(year=2026, quarter=2, start_date=datetime(2026, 4, 7))
    _patch_common(monkeypatch, season=season_s2, series=[_series("Old Series")], events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    season_s3 = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    _patch_common(monkeypatch, season=season_s3, series=[_series("New Series")], events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    # A third rollover should prune season S2's now-stale file entirely.
    season_s4 = ParsedSeason(year=2026, quarter=4, start_date=datetime(2026, 10, 6))
    _patch_common(monkeypatch, season=season_s4, series=[_series("Newest Series")], events=[])
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest == {"current": "2026S4", "previous": "2026S3"}
    assert not (tmp_path / "2026_s2.json").exists()
    assert (tmp_path / "2026_s3.json").exists()
    assert (tmp_path / "2026_s4.json").exists()

    # The S3 file is frozen exactly as it was when it was current — never rebuilt.
    frozen_s3 = json.loads((tmp_path / "2026_s3.json").read_text())
    assert [c["name"] for c in frozen_s3["championships"]] == ["New Series"]


def test_build_cache_fetches_matches_and_extracts_logos(tmp_path, monkeypatch):
    season = ParsedSeason(year=2026, quarter=3, start_date=datetime(2026, 7, 7))
    series = [_series("GT3 Fixed", [_week(1, "Charlotte", "2026-07-07")])]
    events = [_event("firecracker-400", "Firecracker 400")]

    logos_html = """
    <h2 class="wp-block-heading">2026 Special Event Logos</h2>
    <div class="wp-block-cover__inner-container">
      <h2 class="wp-block-heading">2026 Special Event Logos</h2>
      <a class="wp-block-button__link" href="https://example.invalid/events.zip">Download</a>
    </div>
    <div class="wp-block-cover__inner-container">
      <h2 class="wp-block-heading">Official Series - Season 3 2026</h2>
      <a class="wp-block-button__link" href="https://example.invalid/championships.zip">Download</a>
    </div>
    """
    events_zip = _zip_bytes({"iRSE-2026-Firecracker-400.png": b"EVENTPNG"})
    championships_zip = _zip_bytes({"Sports Car/GT3 Fixed.png": b"CHAMPPNG"})

    def fake_get(url, *a, **k):
        if url == "https://example.invalid/seasons":
            return _FakeResponse()
        if url == "https://example.invalid/schedule.pdf":
            return _FakeResponse()
        if url == "https://example.invalid/special-events":
            return _FakeResponse()
        if url == "https://www.iracing.com/resources/logos/":
            return _FakeResponse(text=logos_html)
        if url == "https://example.invalid/events.zip":
            return _FakeResponse(content=events_zip)
        if url == "https://example.invalid/championships.zip":
            return _FakeResponse(content=championships_zip)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(build_cache.httpx, "get", fake_get)
    monkeypatch.setattr(build_cache, "parse_seasons_page", lambda html: [season])
    monkeypatch.setattr(build_cache, "current_season", lambda seasons, as_of: season)
    monkeypatch.setattr(build_cache, "fetch_schedule_pdf", lambda *a, **k: "fake.pdf")
    monkeypatch.setattr(build_cache, "parse_schedule_pdf", lambda path: series)
    monkeypatch.setattr(build_cache, "parse_special_events_page", lambda html: events)

    settings = Settings(
        seasons_page_url="https://example.invalid/seasons",
        schedule_pdf_url="https://example.invalid/schedule.pdf",
        special_events_page_url="https://example.invalid/special-events",
        logos_dir=str(tmp_path / "logos"),
    )
    build_cache.build_cache(settings=settings, data_dir=tmp_path, schedule_cache_dir=tmp_path / "pdfs")

    current = json.loads((tmp_path / "2026_s3.json").read_text())
    event = current["special_events"][0]
    assert event["logo_url"] == "logos/events/firecracker-400.png"
    assert (tmp_path / "logos" / "events" / "firecracker-400.png").read_bytes() == b"EVENTPNG"

    championship = current["championships"][0]
    assert championship["logo_url"] == "logos/championships/gt3-fixed.png"
    assert (tmp_path / "logos" / "championships" / "gt3-fixed.png").read_bytes() == b"CHAMPPNG"


def test_extract_matched_logos_prunes_stale_files(tmp_path):
    out_dir = tmp_path / "events"
    out_dir.mkdir()
    (out_dir / "stale-event.png").write_bytes(b"OLD")

    zf = zipfile.ZipFile(io.BytesIO(_zip_bytes({"new-event.png": b"NEW"})))
    urls = build_cache._extract_matched_logos(zf, {"new-event": "new-event.png"}, out_dir, lambda k: k)

    assert urls == {"new-event": "logos/events/new-event.png"}
    assert (out_dir / "new-event.png").read_bytes() == b"NEW"
    assert not (out_dir / "stale-event.png").exists()


def test_base_series_name_strips_trailing_fixed_marker():
    assert build_cache._base_series_name("CARS Tour Late Model Stocks - Fixed") == "cars tour late model stocks"
    assert build_cache._base_series_name("CARS Tour Late Model Stocks") == "cars tour late model stocks"
    assert build_cache._base_series_name("Dirt Midget Cup Fixed") == "dirt midget cup"
    # "Fixed" mid-name (a sponsor-style tag, not a variant suffix) is left alone.
    assert build_cache._base_series_name("GT3 Challenge Fixed by Fanatec") == "gt3 challenge fixed by fanatec"


def test_fixed_variant_without_its_own_logo_reuses_the_regular_variants():
    championships = [
        {"name": "CARS Tour Late Model Stocks", "logo_url": "logos/championships/cars-tour.png"},
        {"name": "CARS Tour Late Model Stocks - Fixed", "logo_url": None},
    ]
    build_cache._share_logos_across_fixed_variants(championships)
    assert championships[1]["logo_url"] == "logos/championships/cars-tour.png"


def test_regular_variant_without_its_own_logo_reuses_the_fixed_variants():
    championships = [
        {"name": "Dirt Midget Cup", "logo_url": None},
        {"name": "Dirt Midget Cup - Fixed", "logo_url": "logos/championships/dirt-midget-cup-fixed.png"},
    ]
    build_cache._share_logos_across_fixed_variants(championships)
    assert championships[0]["logo_url"] == "logos/championships/dirt-midget-cup-fixed.png"


def test_sharing_is_a_noop_when_neither_variant_has_a_logo_or_theres_no_sibling():
    championships = [
        {"name": "Some Series", "logo_url": None},
        {"name": "Some Series - Fixed", "logo_url": None},
        {"name": "Standalone Series", "logo_url": None},
    ]
    build_cache._share_logos_across_fixed_variants(championships)
    assert all(c["logo_url"] is None for c in championships)


def test_sharing_does_not_touch_an_already_matched_fixed_variant():
    championships = [
        {"name": "Some Series", "logo_url": "logos/championships/some-series.png"},
        {"name": "Some Series - Fixed", "logo_url": "logos/championships/some-series-fixed.png"},
    ]
    build_cache._share_logos_across_fixed_variants(championships)
    assert championships[0]["logo_url"] == "logos/championships/some-series.png"
    assert championships[1]["logo_url"] == "logos/championships/some-series-fixed.png"
