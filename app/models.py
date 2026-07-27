import secrets
from datetime import UTC, datetime, time
from enum import Enum

from sqlmodel import JSON, Column, Field, Relationship, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class SpecialEventSource(str, Enum):
    PAGE = "page"  # parsed straight from iracing.com/special-events
    MANUAL = "manual"  # no date announced yet on the page ("TBD"); admin-entered instead


class Season(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    year: int
    quarter: int
    start_date: datetime
    end_date: datetime
    fetched_at: datetime = Field(default_factory=_utcnow)

    series: list["Series"] = Relationship(back_populates="season")


class Series(SQLModel, table=True):
    """A championship within a season, e.g. 'GT3 Fixed'."""

    id: int | None = Field(default=None, primary_key=True)
    season_id: int = Field(foreign_key="season.id")
    name: str = Field(index=True)
    category: str = ""
    license_level: str = ""
    cadence_text: str = ""  # e.g. "Races every even 2 hours at :30 past", from the PDF
    link_url: str | None = None  # "go racing" link to the series on members-ng.iracing.com, from the PDF
    # day_of_week (0=Monday..6=Sunday, as a JSON string key) -> sorted "HH:MM" GMT
    # times that day, from app/parsers/race_cadence.py. A day absent from this dict
    # has no sessions at all — e.g. a Thu/Sat-only series omits Mon/Tue/Wed/Fri/Sun.
    session_times_by_day: dict[str, list[str]] = Field(default_factory=dict, sa_column=Column(JSON))
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))

    season: Season | None = Relationship(back_populates="series")
    weeks: list["ScheduleWeek"] = Relationship(back_populates="series")


class ScheduleWeek(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series.id")
    week_number: int
    track_name: str
    date_start: datetime
    date_end: datetime
    duration_minutes: int | None = None  # explicit session length from the PDF, when stated (see app/parsers/schedule_pdf.py)
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))

    series: Series | None = Relationship(back_populates="weeks")


class SpecialEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    year: int
    name: str = Field(index=True)
    date_start: datetime
    date_end: datetime
    track_name: str | None = None
    track_path: str | None = None
    car_class: str | None = None
    source: SpecialEventSource = SpecialEventSource.PAGE
    raw_json: dict = Field(default_factory=dict, sa_column=Column(JSON))


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True)
    timezone: str = "UTC"  # IANA name, e.g. "America/New_York" — display-only context for the
    # user (session times are fixed by iRacing in GMT; this just helps them compare)
    token: str = Field(default_factory=_new_token, unique=True, index=True)
    created_at: datetime = Field(default_factory=_utcnow)

    selections: list["Selection"] = Relationship(back_populates="user")


class Selection(SQLModel, table=True):
    """A user's pick of one championship or special event."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    series_id: int | None = Field(default=None, foreign_key="series.id")
    special_event_id: int | None = Field(default=None, foreign_key="specialevent.id")

    user: User | None = Relationship(back_populates="selections")
    series: Series | None = Relationship()
    special_event: SpecialEvent | None = Relationship()
    timeslots: list["Timeslot"] = Relationship(back_populates="selection")


class Timeslot(SQLModel, table=True):
    """One weekly recurring GMT slot for a selection — for championships, picked
    from the series' own Series.session_times (iRacing's actual session cadence is
    fixed in GMT, not user-timezone-relative). Multiple rows = multiple sessions per
    week, added via the "Add" button in the UI."""

    id: int | None = Field(default=None, primary_key=True)
    selection_id: int = Field(foreign_key="selection.id")
    day_of_week: int  # 0 = Monday ... 6 = Sunday, matches datetime.weekday(); GMT week starts Tuesday
    time_gmt: time
    label: str = ""

    selection: Selection | None = Relationship(back_populates="timeslots")


class NotificationLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    season_id: int | None = Field(default=None, foreign_key="season.id")
    sent_at: datetime = Field(default_factory=_utcnow)
    summary: str = ""
