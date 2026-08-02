from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the build-time-only cache pipeline (tools/build_cache.py).

    There is no server anymore, so there is nothing here about databases, SMTP, or
    a base URL — the only job left is fetching and parsing iRacing's own public
    pages/PDF, which needs none of that.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IRCAL_")

    # All public, no account/auth involved. Both are fixed, evergreen URLs (no year/quarter
    # in either — each always serves "whatever's current").
    seasons_page_url: str = "https://www.iracing.com/seasons/"
    schedule_pdf_url: str = "https://members-assets.iracing.com/public/schedulepdf/SeasonSchedule.pdf"
    special_events_page_url: str = "https://www.iracing.com/special-events/"
    logos_page_url: str = "https://www.iracing.com/resources/logos/"

    schedule_pdf_cache_dir: str = "cache/schedules"
    data_dir: str = "docs/data"
    logos_dir: str = "docs/logos"


@lru_cache
def get_settings() -> Settings:
    return Settings()
