# iRacing Calendar

Self-hosted iCal feeds for your iRacing championships and special events.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and fill in SMTP details (or leave blank for local dev —
email sends just get logged instead). No iRacing account credentials are used anywhere
in this project — all schedule data comes from public, unauthenticated sources.

## Run

```
source .venv/bin/activate
uvicorn app.main:app --reload
```

Then open http://localhost:8000/.

## Test

```
source .venv/bin/activate
python -m pytest
```

## Deploy

See [DEPLOYMENT.md](DEPLOYMENT.md) for running this in Docker behind an
Apache 2 reverse proxy.

## Status

Working end-to-end against real, official, live sources (verified this session with a
fresh empty DB — no iRacing account used anywhere):

- `app/parsers/seasons_page.py` parses `iracing.com/seasons` (BeautifulSoup, no LLM) to
  find the current season's `(year, quarter, start_date)` — including a workaround for
  a real bug on iRacing's own page (the current season's button links to the wrong
  season slug; the parser reads the season number from the link text instead).
- `app/schedule_source.py` fetches the **official** season schedule PDF
  (`members-assets.iracing.com/public/schedulepdf/SeasonSchedule.pdf`, no cookies);
  `app/parsers/schedule_pdf.py` deterministically parses it into per-series weekly
  schedules (pdfplumber tables + regex, no LLM), including page-break continuation
  handling.
- `app/parsers/special_events_page.py` parses the real
  `iracing.com/special-events` HTML page directly (dates, track links, car-class
  labels are all real markup — no OCR/image parsing involved). Handles cross-month
  date ranges (e.g. "June 30 – July 6"), abbreviated month names, and skips events
  with no announced date ("Date : TBD") rather than guessing.
- `app/scheduler.py`'s `check_upcoming_seasons` runs on startup and every ~2 months:
  detects the current season, and if it's new, fetches/parses/upserts the schedule,
  matches previous selections to the new season (`app/matcher.py`), and emails
  affected users a rollover recap (`app/email.py`). Special events refresh on the
  same cadence, plus once at startup if none exist yet.
- Full self-service flow: browse (grouped by season, e.g. "2026 Season 3") → pick
  championships/events → set weekly timeslots in your own timezone (`+ Add` for
  multiple sessions/week; checking a championship auto-adds a first timeslot so it
  can't be forgotten) → name+email → private manage link emailed. `/u/{token}/edit`
  reuses the same form, pre-filled, to change selections. `/manage` lets you recover
  lost links by email (always shows the same confirmation, so it doesn't leak which
  emails are registered). Special events already in the past are struck through.
- Each championship's actual session cadence is parsed from its schedule PDF header
  (`app/parsers/race_cadence.py`, e.g. "Races every even 2 hours at :30 past" ->
  00:30, 02:30, 04:30 GMT, ...) and the timeslot picker is a dropdown of only those
  real GMT times — not a free-form input — defaulting to Tuesday (when iRacing's
  week rolls over) and the first available time. A handful of irregular
  day-specific cadences ("every other Saturday...") fall back to a coarse default
  grid with a logged warning rather than guessing. `User.timezone` (defaulted from
  the browser via `Intl.DateTimeFormat`, editable) is kept for the user's own
  reference only, since session times are fixed by iRacing in GMT.
- Each series' required license (Rookie/D/C/B/A/Pro-World-Champion) is parsed from
  its schedule PDF header's promotion-range line (`app/parsers/schedule_pdf.py`,
  e.g. "Rookie (1.0) --> Pro/WC (4.0)" -> Rookie; "Class C (4.0) --> Pro/WC (4.0)"
  -> Class B, since an SR of 4.0 is iRacing's own auto-promotion threshold, so
  that's effectively a Class B series). Shown as a colored letter badge (R/D/C/B/A/P,
  iRacing's own per-license colors, `app/licenses.py`) next to each series on the
  browse and manage pages, with checkbox filters to show/hide championships by
  license while picking.
- Special events render as proper all-day banners spanning their full announced
  date range inclusive (handles cross-month events like Firecracker 400, June 30 –
  July 6) rather than a zero-duration blip on the start date.
- Each user's `.ics` gets one synthetic reminder event during "Week 13" (iRacing's
  build/transition week with no regular schedule) nudging them to come re-pick
  championships for the next season — timed using their first subscribed
  championship's own timeslot and that specific series' own last scheduled week
  (not the season's blended end date, which a few continuous multi-season series
  would otherwise skew months too late).
- 69 tests passing, including against real captured PDF/HTML fixtures and a FastAPI
  `TestClient` integration test covering signup → edit → recovery.

Remaining gaps:

- No admin UI for events with no announced date yet (currently just excluded until
  iRacing publishes one and the next refresh picks it up).
- Series aren't categorized by discipline (Oval/Road/Dirt/etc.) — the browse page
  groups by season instead, since `Series.category` isn't populated from the
  schedule PDF yet.
