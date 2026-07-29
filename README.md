# iRacing Planner

A static site for planning your iRacing season: pick your championships and
special events, then subscribe to a `.ics` feed built from your picks. No
accounts, no email, no database — your selections live entirely in a
deterministic code embedded in the URL. See [ARCHITECTURE.md](ARCHITECTURE.md)
for exactly how that works.

## How it's built

- **`docs/`** — the static picker/manager site, published to GitHub Pages.
  Plain HTML/CSS/vanilla JS, no framework. Reads `docs/data/*.json` (the season
  cache) to render championships/special events, and builds a calendar code
  live as you check things.
- **`shared/`** — the calendar-code encode/decode logic and the previous-season
  rollover matcher, used by both the static site and the Worker. Single source
  of truth; copied into `docs/shared/` at deploy time (browsers can't import
  outside their served root) and imported directly by the Worker (bundled at
  build time, no such restriction).
- **`worker/`** — a Cloudflare Worker serving the live `.ics` endpoint
  (`GET /calendar/{code}.ics`). GitHub Pages can't run code per request, so
  this is the one piece of always-on compute in the whole system.
- **`pipeline/`** — pure, deterministic Python that parses iRacing's own public
  pages/PDF (no account/login anywhere). Reused as a library by:
- **`tools/build_cache.py`** — the GitHub Actions entry point that refreshes
  `docs/data/*.json` from live sources on a schedule.
- **`.github/workflows/`** — refreshes the cache, publishes `docs/`, and
  deploys the Worker. See ARCHITECTURE.md for how they chain.

## Local development

### Python (`pipeline/`, `tools/`)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

Run the cache build for real (writes `docs/data/*.json` from live iRacing
sources):

```
python -m tools.build_cache
```

### JavaScript (`shared/`, `worker/`, `docs/`)

```
npm install
npm test
```

Serve the static site locally (copies `shared/` into `docs/shared/` first,
since a browser can only import modules from within its served root):

```
npm run dev:site
```

Run the Worker locally against that same local site:

```
npm run dev:worker -- --var PAGES_BASE_URL:http://localhost:3000
```

(adjust the port to whatever `dev:site` printed). Then fetch e.g.
`http://localhost:8787/calendar/2026S3.ics` to see a live-generated calendar.

## Deploy

See [DEPLOYMENT.md](DEPLOYMENT.md) for enabling GitHub Pages, creating the
Cloudflare Worker, and the repo secrets/variables both need.

## Status

Working end-to-end against real, official, live sources — no iRacing account
used anywhere:

- `pipeline/parsers/seasons_page.py`, `schedule_pdf.py`, `special_events_page.py`,
  `race_cadence.py` are unchanged from the original server-based version:
  deterministic, rule-based (BeautifulSoup/pdfplumber/regex, no LLM), and still
  fully covered by `tests/` against real captured fixtures.
- `tools/build_cache.py` detects the current season, rebuilds its cache fully
  from live sources every run, and reconciles against whatever was previously
  committed so that championship/special-event array positions never shift
  once published (see ARCHITECTURE.md's index-stability invariant) — that
  stability is what makes a deterministic, position-addressed calendar code
  safe to hand out.
- `docs/app.js` reimplements the full picker experience from the old
  server-rendered templates client-side: license/text filtering, dynamic
  weekly-timeslot rows constrained to each championship's real cadence, live
  local-timezone badges, per-championship week-by-week schedule detail, and a
  live-updating calendar code + subscribe URL with no submit button.
- Pasting an existing code or subscribe URL back into the site decodes it and
  re-ticks the matching selections (`shared/calendar-code.js` +
  `shared/resolve.js`) — this replaces the old token-based `/u/{token}/edit`
  manage page.
- A code from last season is matched against the current season's
  championships by name (`shared/series-match.js`, ported from the old
  `app/matcher.py`) entirely client-side, with inline banners for matched,
  time-changed, and unmatched championships — this replaces the old emailed
  rollover recap.
- `worker/src/ics.js` ports the old `app/ics_builder.py`'s event-generation
  semantics: one VEVENT per week × timeslot in UTC, gap-based duration
  shrinking for back-to-back sessions, the week-13 "pick next season" reminder,
  and all-day special-event spans with an RFC 5545-correct exclusive DTEND.
- The Worker always returns a valid calendar — a malformed code or a season
  outside the current+previous retention window decodes to an empty (not
  broken) calendar.

Remaining gaps (carried over from the original app):

- No admin UI for special events with no announced date yet (excluded until
  iRacing publishes one and the next cache refresh picks it up).
- Series aren't categorized by discipline (Oval/Road/Dirt/etc.) —
  `championship.category` isn't populated from the schedule PDF yet.
