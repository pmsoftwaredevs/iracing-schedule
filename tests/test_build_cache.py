import json
from datetime import datetime

import pytest

from pipeline.config import Settings
from pipeline.parsers.schedule_pdf import ParsedSeries, ParsedWeek
from pipeline.parsers.seasons_page import ParsedSeason
from pipeline.parsers.special_events_page import ParsedSpecialEvent
from tools import build_cache


class _FakeResponse:
    def __init__(self, text: str = ""):
        self.text = text

    def raise_for_status(self):
        pass


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
