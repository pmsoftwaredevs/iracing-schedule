from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="IRCAL_")

    database_url: str = "sqlite:///./iracing_calendar.db"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = "iracing-calendar@localhost"

    base_url: str = "http://localhost:8000"

    # All public, no account/auth involved. Both are fixed, evergreen URLs (no year/quarter
    # in either — each always serves "whatever's current").
    seasons_page_url: str = "https://www.iracing.com/seasons/"
    schedule_pdf_url: str = "https://members-assets.iracing.com/public/schedulepdf/SeasonSchedule.pdf"
    special_events_page_url: str = "https://www.iracing.com/special-events/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
