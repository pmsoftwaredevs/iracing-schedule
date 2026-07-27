"""Fetches the official season schedule PDF — a plain public GET, no iRacing account
involved. The URL is fixed and always serves "whatever season is current" (no
year/quarter in it), so the caller supplies (year, quarter) — determined separately
via app/parsers/seasons_page.py — purely to name the local cache file and avoid
re-downloading the same season's PDF repeatedly.
"""

import logging
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def fetch_schedule_pdf(settings: Settings, year: int, quarter: int, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"schedule-{year}S{quarter}.pdf"
    if cache_path.exists():
        logger.info("using cached schedule PDF for %s S%s at %s", year, quarter, cache_path)
        return cache_path

    logger.info("fetching schedule PDF for %s S%s from %s", year, quarter, settings.schedule_pdf_url)
    response = httpx.get(settings.schedule_pdf_url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    cache_path.write_bytes(response.content)

    return cache_path
