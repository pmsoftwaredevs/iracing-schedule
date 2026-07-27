import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, available_timezones

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session, init_db
from app.email import send_recovery_email, send_signup_email
from app.ics_builder import build_calendar
from app.models import NotificationLog, ScheduleWeek, Selection, Series, SpecialEvent, Timeslot, User
from app.scheduler import bootstrap_if_needed, build_scheduler

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")

AVAILABLE_TIMEZONES = sorted(available_timezones())
DEFAULT_TIMEZONE = "UTC"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    try:
        bootstrap_if_needed(settings)
    except Exception:
        logger.exception("startup data bootstrap failed; continuing with whatever data already exists")
    scheduler = build_scheduler(settings)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="iRacing Calendar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

SessionDep = Annotated[Session, Depends(get_session)]


def _format_date(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def _series_group_label_and_subtitle(series: Series) -> tuple[str, str | None]:
    if series.category:
        return series.category, None
    season = series.season
    if season is None:
        return "Other", None
    return f"{season.year} Season {season.quarter}", f"Starts {_format_date(season.start_date)}"


def _current_or_next_week(weeks: list[ScheduleWeek], now: datetime) -> ScheduleWeek | None:
    """The week whose track is "in use" right now, for the collapsed summary line —
    the first not-yet-finished week, or the season's last week once it's all over."""
    if not weeks:
        return None
    upcoming = [w for w in weeks if w.date_end >= now]
    if upcoming:
        return min(upcoming, key=lambda w: w.week_number)
    return max(weeks, key=lambda w: w.week_number)


def _browse_context(session: Session) -> dict:
    series = session.exec(select(Series)).all()
    special_events = session.exec(select(SpecialEvent)).all()
    now = datetime.now(UTC).replace(tzinfo=None)

    groups: dict[str, dict] = {}
    series_weeks: dict[int | None, list[ScheduleWeek]] = {}
    series_current_week: dict[int | None, ScheduleWeek | None] = {}
    for s in series:
        label, subtitle = _series_group_label_and_subtitle(s)
        group = groups.setdefault(label, {"label": label, "subtitle": subtitle, "series": []})
        group["series"].append(s)
        weeks = sorted(s.weeks, key=lambda w: w.week_number)
        series_weeks[s.id] = weeks
        series_current_week[s.id] = _current_or_next_week(weeks, now)

    return {
        "series_groups": list(groups.values()),
        "series_weeks": series_weeks,
        "series_current_week": series_current_week,
        "special_events": special_events,
        "special_events_year": datetime.now(UTC).year,
        "timezones": AVAILABLE_TIMEZONES,
        "now": now,
    }


def _local_slot_label(day_of_week: int, time_gmt: time, tz_name: str) -> str | None:
    """"Tue 09:30"-style label for the next occurrence of a GMT weekly timeslot,
    converted into tz_name — the same "next occurrence" approach the live picker
    uses in JS, kept server-side here since this page has no interactive timezone
    control to react to."""
    if tz_name == "UTC":
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        return None
    now = datetime.now(UTC)
    days_ahead = (day_of_week - now.weekday()) % 7
    candidate = datetime.combine(now.date(), time_gmt, tzinfo=UTC) + timedelta(days=days_ahead)
    if days_ahead == 0 and candidate <= now:
        candidate += timedelta(days=7)
    local = candidate.astimezone(zone)
    return f"{local.strftime('%a')} {local.strftime('%H:%M')}"


def _clean_timezone(raw: str) -> str:
    if raw in AVAILABLE_TIMEZONES:
        return raw
    return DEFAULT_TIMEZONE


def _require_at_least_one_selection(form: FormData) -> None:
    """Backend backstop for the "pick at least one" rule the form's JS already
    enforces — guards direct/non-JS POSTs, since _apply_selections otherwise
    happily creates a user with zero selections."""
    if not form.getlist("series_ids") and not form.getlist("special_event_ids"):
        raise HTTPException(status_code=400, detail="Pick at least one championship or special event")


def _apply_selections(session: Session, user: User, form: FormData) -> None:
    """Replaces user's Selections/Timeslots with whatever the form submitted. Used
    by both the new-signup route and the edit route — a brand-new user simply has no
    existing selections to clear, so the same code path handles both."""
    # dict.fromkeys dedups while preserving submission order — matters because
    # ics_builder.py's week-13 reminder uses the "first" subscribed championship.
    series_ids = list(dict.fromkeys(int(v) for v in form.getlist("series_ids")))
    special_event_ids = list(dict.fromkeys(int(v) for v in form.getlist("special_event_ids")))

    timeslots_by_series: dict[int, list[tuple[int, time]]] = defaultdict(list)
    for series_id_raw, day_raw, time_raw in zip(
        form.getlist("slot_series_id"), form.getlist("slot_day"), form.getlist("slot_time")
    ):
        series_id = int(str(series_id_raw))
        if series_id not in series_ids:
            continue
        timeslots_by_series[series_id].append((int(str(day_raw)), time.fromisoformat(str(time_raw))))

    for selection in user.selections:
        for slot in selection.timeslots:
            session.delete(slot)
        session.delete(selection)
    session.flush()

    for series_id in series_ids:
        selection = Selection(user_id=user.id, series_id=series_id)
        session.add(selection)
        session.flush()
        for day, time_gmt in timeslots_by_series.get(series_id, []):
            session.add(Timeslot(selection_id=selection.id, day_of_week=day, time_gmt=time_gmt))

    for event_id in special_event_ids:
        session.add(Selection(user_id=user.id, special_event_id=event_id))

    session.commit()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, session: SessionDep):
    context = _browse_context(session)
    context.update(
        action_url="/select",
        existing_user=None,
        selected_series_ids=set(),
        selected_special_event_ids=set(),
        timeslots_by_series={},
        selected_timezone=DEFAULT_TIMEZONE,
    )
    return templates.TemplateResponse(request, "index.html", context)


@app.post("/select")
async def submit_selection(request: Request, session: SessionDep):
    """Reads raw form data instead of typed FastAPI Form() params because the
    timeslot rows are a dynamic, JS-added repeating group (one "Add" click = one
    more slot_series_id/slot_day/slot_time triplet), which typed Form() params
    can't express."""
    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")
    _require_at_least_one_selection(form)
    timezone_name = _clean_timezone(str(form.get("timezone", "")).strip())

    user = User(name=name, email=email, timezone=timezone_name)
    session.add(user)
    session.flush()

    _apply_selections(session, user, form)
    session.refresh(user)

    settings = get_settings()
    manage_url = f"{settings.base_url}/u/{user.token}"
    send_signup_email(settings, user.email, user.name, manage_url)

    return RedirectResponse(url=f"/u/{user.token}", status_code=303)


@app.get("/u/{token}", response_class=HTMLResponse)
def manage(request: Request, token: str, session: SessionDep):
    user = session.exec(select(User).where(User.token == token)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown token")
    settings = get_settings()
    subscribe_url = f"{settings.base_url}/u/{user.token}/calendar.ics"
    return templates.TemplateResponse(
        request,
        "manage.html",
        {
            "user": user,
            "subscribe_url": subscribe_url,
            "now": datetime.now(UTC).replace(tzinfo=None),
            "local_slot_label": lambda dow, t: _local_slot_label(dow, t, user.timezone),
        },
    )


@app.get("/u/{token}/edit", response_class=HTMLResponse)
def edit_selection(request: Request, token: str, session: SessionDep):
    user = session.exec(select(User).where(User.token == token)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown token")

    timeslots_by_series: dict[int, list[Timeslot]] = {}
    selected_series_ids: set[int] = set()
    selected_special_event_ids: set[int] = set()
    for selection in user.selections:
        if selection.series_id is not None:
            selected_series_ids.add(selection.series_id)
            timeslots_by_series[selection.series_id] = selection.timeslots
        elif selection.special_event_id is not None:
            selected_special_event_ids.add(selection.special_event_id)

    context = _browse_context(session)
    context.update(
        action_url=f"/u/{token}/select",
        existing_user=user,
        selected_series_ids=selected_series_ids,
        selected_special_event_ids=selected_special_event_ids,
        timeslots_by_series=timeslots_by_series,
        selected_timezone=user.timezone,
    )
    return templates.TemplateResponse(request, "index.html", context)


@app.post("/u/{token}/select")
async def update_selection(request: Request, token: str, session: SessionDep):
    user = session.exec(select(User).where(User.token == token)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown token")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")
    _require_at_least_one_selection(form)
    user.name = name
    user.email = email
    user.timezone = _clean_timezone(str(form.get("timezone", "")).strip())
    session.add(user)

    _apply_selections(session, user, form)

    return RedirectResponse(url=f"/u/{token}", status_code=303)


@app.post("/u/{token}/delete")
def delete_user(token: str, session: SessionDep):
    user = session.exec(select(User).where(User.token == token)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown token")

    for selection in user.selections:
        for slot in selection.timeslots:
            session.delete(slot)
        session.delete(selection)
    for log in session.exec(select(NotificationLog).where(NotificationLog.user_id == user.id)).all():
        session.delete(log)
    session.delete(user)
    session.commit()

    return RedirectResponse(url="/manage", status_code=303)


@app.get("/u/{token}/calendar.ics")
def calendar_ics(token: str, session: SessionDep):
    user = session.exec(select(User).where(User.token == token)).first()
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown token")
    calendar = build_calendar(session, user)
    return PlainTextResponse(calendar.to_ical().decode("utf-8"), media_type="text/calendar")


@app.get("/manage", response_class=HTMLResponse)
def manage_lookup_form(request: Request):
    return templates.TemplateResponse(request, "manage_lookup.html", {"submitted": False})


@app.post("/manage/lookup", response_class=HTMLResponse)
async def manage_lookup(request: Request, session: SessionDep):
    form = await request.form()
    email = str(form.get("email", "")).strip()

    if email:
        users = session.exec(select(User).where(User.email == email)).all()
        if users:
            settings = get_settings()
            links = [(u.name, f"{settings.base_url}/u/{u.token}") for u in users]
            send_recovery_email(settings, email, links)

    # Same confirmation regardless of match, so this doesn't leak which emails are registered.
    return templates.TemplateResponse(request, "manage_lookup.html", {"submitted": True})
