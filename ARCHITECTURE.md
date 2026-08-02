# Architecture

This document explains the pieces that aren't obvious just from reading the
code: the calendar-code scheme, the season cache's index-stability invariant,
the retention/pruning contract, why the Worker fetches data live instead of
bundling it, the client-side rollover-matching flow, and how the three GitHub
Actions workflows chain together.

## Why any of this is shaped the way it is

The site is 100% static (GitHub Pages). GitHub Pages cannot execute code per
request, but a calendar app subscribing to a URL needs to re-fetch it
periodically and get back a live, correct `.ics`. That requires *some*
always-on compute — the one piece in this whole system — provided by a small
Cloudflare Worker. Everything else (parsing iRacing's public pages, building
the season cache, publishing the site) is a periodic build step, not a
request-time one.

There is also no database. A user's "calendar" isn't a row anywhere — it's a
code, and the code alone fully determines the calendar. This means:

- No accounts, no email, no PII stored anywhere, ever.
- Sharing a calendar is just sharing a URL.
- "Editing" a calendar is decoding the old code, changing some checkboxes, and
  getting a new code — there's nothing to look up or authenticate.

The tradeoff this buys is that the code's structure now matters a lot: it has
to unambiguously address specific championships/events/timeslots in a season's
data, and that addressing has to stay valid for as long as the code might be
used. See the index-stability invariant below for how that's guaranteed.

## The calendar code

Shape: `<season code><record><record>...<record>` — a 6-character season
prefix followed by zero or more records concatenated with **no separators**.

Each record starts with an uppercase tag (`C` for championship, `E` for
special event); every field inside a record is lowercase base36 (`0-9a-z`).
This isn't actually required for parsing — each record is self-describing via
its own count field — but it makes a well-formed code visibly readable (one
capital letter per record) and gives a cheap sanity check.

| Field | Width | Alphabet | Range | Why |
|---|---|---|---|---|
| Season year | 4 | decimal | 0000–9999 | human-readable |
| Literal `S` | 1 | fixed | — | separates year/quarter |
| Season quarter | 1 | decimal | 0–9 (1–4 used) | iRacing has 4 quarters/year |
| Record tag | 1 | uppercase | `C`, `E` | starts a record |
| Championship index | 2 | base36 lower | 0–1295 | real max ~152 series/season (2026 S3) |
| Timeslot count | 1 | base36 lower | 0–35 | realistic max is 2-5/week |
| Day of week | 1 | base36 lower | 0–6 | 0=Monday..6=Sunday |
| Time-slot index | 2 | base36 lower | 0–1295 | worst case is the 48-slot/day fallback grid |
| Special-event index | 2 | base36 lower | 0–1295 | real max ~10-30/year |

Season prefix is a fixed 6 chars (`YYYY` + `S` + `Q`), always sliced off
first. A championship record is `4 + 3n` characters (`n` = timeslot count):
tag + index + count, then `day + time-index` per timeslot. An event record is
3 characters: tag + index.

**Worked example** — season 2026 S3, championship index 5 with two timeslots
(Tuesday @ time-index 1, Friday @ time-index 5), plus special event index 12:

```
2026S3 C 05 2 1 01 4 05 E 0c
→ "2026S3C052101405E0c"
```

Implemented in `shared/calendar-code.js` (`encodeCalendarCode` /
`decodeCalendarCode` / `parseCalendarCode`), with a full round-trip test suite
in `shared/calendar-code.test.js`.

**Decoding is always lenient, encoding is always strict.** The picker UI
builds codes (`encodeCalendarCode`) and throws on a programming error — bad
input there means a bug, not a user mistake. Decoding (`decodeCalendarCode`)
has to tolerate arbitrary pasted/hand-edited/stale input, so it never throws:
a malformed code, an out-of-range index, or a season outside the retention
window all degrade to "no selections for that part" rather than an error.

## The season cache

One JSON file per retained season, e.g. `docs/data/2026_s3.json`:

```json
{
  "season": { "year": 2026, "quarter": 3, "code": "2026S3", "start_date": "...", "end_date": "..." },
  "generated_at": "...",
  "championships": [
    { "name": "GT3 Fixed", "license_level": "B", "link_url": "...", "logo_url": "logos/championships/gt3-fixed.png",
      "session_times_by_day": { "0": ["00:30", "02:30"] },
      "session_time_options": ["00:30", "02:30"],
      "typical_session_duration_minutes": 45,
      "weeks": [ { "week_number": 1, "track_name": "...", "date_start": "...", "date_end": "...", "duration_minutes": null } ] }
  ],
  "special_events": [
    { "slug": "firecracker-400", "name": "Firecracker 400", "date_start": "...", "date_end": "...", "track_name": "...", "track_path": "...", "car_class": "...", "link_url": "...", "logo_url": "logos/events/firecracker-400.png" }
  ]
}
```

`docs/data/manifest.json` records which two seasons are currently retained:

```json
{ "current": "2026S3", "previous": "2026S2" }
```

`championships[i]` **is** championship index `i` in the calendar-code scheme;
`special_events[i]` **is** event index `i`; `championships[i].session_time_options[j]`
**is** time-slot index `j` for that specific championship.
`session_time_options` is precomputed once in Python (flattened + sorted from
`session_times_by_day`) so the browser and the Worker never independently
flatten/sort the same data and risk landing on a different order.

### The index-stability invariant

Because array **position** is the addressing scheme, `tools/build_cache.py`
must never reorder an already-published season's `championships` or
`special_events` array. If it did, every code issued against the old ordering
would silently start pointing at the wrong championship or event.

Every run of `build_cache.py`, for the season it's about to write, loads
whatever was previously committed for that exact season and reconciles:

- An existing entry whose key (`name` for championships, `slug` for events)
  still appears in this run's fresh parse keeps its position, with its data
  refreshed.
- An existing entry whose key has *disappeared* from this run's parse (a rare
  edge case — a series pulled mid-season, a parser hiccup) is kept **frozen**
  at its old position rather than dropped, because dropping it would shift
  every later entry's index.
- A brand-new key is **appended** at the end, never inserted in the middle.

See `_reconcile` in `tools/build_cache.py` and its tests in
`tests/test_build_cache.py`.

### Retention and pruning

Exactly two season files are kept: `manifest.current` and `manifest.previous`.
When a new season is detected, whatever was `current` simply becomes
`previous` — that file is never touched again, because the schedule PDF's URL
is evergreen (no year/quarter in it — it always serves "whatever's current"),
so a past season's real data can never be re-fetched to rebuild it anyway.
Anything older than `current`/`previous` is deleted.

A calendar code whose season isn't in that 2-season window decodes to an
**empty but valid** calendar — never an error — both in the picker UI and in
the Worker's `.ics` output. This is `decodeCalendarCode`'s `inRetentionWindow`
flag.

### Links and logos

`link_url` is scraped directly, not matched: a special event's is its
section's "MORE INFO" button on iracing.com/special-events (checked to
actually be on the `forums.iracing.com` domain before being trusted — see
`pipeline/parsers/special_events_page.py`); a championship's comes from the
schedule PDF's own annotation links (`pipeline/parsers/schedule_pdf.py`).

`logo_url` is different: iRacing's logo packs (zips linked from
iracing.com/resources/logos/, found by `pipeline/parsers/logos_page.py`) have
**no id linking a file to a championship or event** — just a folder of PNGs
named close to, but not exactly, the on-site display name (sponsor tags,
"Fixed"/"Open" suffixes, and the odd typo appear on one side and not the
other). `pipeline/logo_matching.py` fuzzy-matches filenames to
championships/events via token-overlap (Jaccard) scoring, assigned
greedily best-score-first across the whole set, with anything below a
minimum-confidence threshold left unmatched rather than risking the wrong
logo — no logo beats a wrong one, the same "skip rather than guess" stance
`special_events_page.py` takes on undated events. Matched PNGs are extracted
to `docs/logos/championships/{slugified-name}.png` and
`docs/logos/events/{slug}.png`; both directories are pruned each run so a
renamed or removed entry doesn't leave a stale file behind. Because matching
runs fresh every time, `logo_url` isn't covered by the index-stability
reconciliation above the way `name`/`slug` are — a match that's confident one
run and just under the threshold the next (rare, since the underlying data
barely changes) can make a logo disappear, which is an accepted tradeoff, not
a bug.

## The Cloudflare Worker

Route: `GET /calendar/{code}.ics`. On every request it:

1. Fetches `data/manifest.json` from the published Pages site
   (`worker/src/season-data.js`), with `cf: { cacheTtl: 300, cacheEverything: true }`
   so repeat requests hit Cloudflare's edge cache instead of re-fetching Pages
   every time.
2. Decodes the code against that manifest (`shared/calendar-code.js`).
3. If the season is in the retention window, fetches that season's cache file
   the same way and resolves the decoded indices into concrete
   championships/timeslots/events (`shared/resolve.js`).
4. Builds the `.ics` text (`worker/src/ics.js`, a direct port of the old
   `app/ics_builder.py`'s event-generation semantics) and returns it with
   `Content-Type: text/calendar`.

**Data is fetched live on every request, never bundled into the Worker.**
Bundling would force a `wrangler deploy` every time the daily cache-refresh
workflow runs, coupling data freshness to code deploys for no benefit.
Fetching live and letting Cloudflare's own cache absorb repeat traffic keeps
the Worker always in sync with whatever Actions just published, and decouples
the Worker's deploy cadence (only on actual code changes) from the data's
(daily).

The Worker never 4xx/5xxs a well-formed `/calendar/{code}.ics` request — a
malformed code, an unreachable season, or even a failed fetch of the season
cache all still return `200` with a valid (possibly empty) calendar. A broken
subscription in a calendar app is worse than a temporarily-empty one.

## Managing an existing calendar

There's no server-side lookup by token anymore. "Managing" is: paste your
existing code or subscribe URL into the site's paste box (or arrive via
`?code=...`, which the site treats the same way); it's decoded client-side
against the current season's cache, and the matching checkboxes/timeslots are
pre-ticked so you can change them and get a new code.

### Rollover matching (replacing the old emailed recap)

If the pasted code's season equals `manifest.previous` (last season), the
site can't just resolve it against the current season's cache — the indices
mean something different in a different season's data. Instead
(`docs/rollover.js`):

1. Decode the code against the **previous** season's cache to recover the old
   championship names and picked timeslots.
2. Fetch the **current** season's cache and run `shared/series-match.js`'s
   `matchSeries` — a direct port of the old `app/matcher.py`: exact name match
   first, then a normalized (lowercase, punctuation-stripped) fallback if
   that's unambiguous.
3. For each match: tick the current season's card. If the old picked GMT time
   still exists in the new championship's `session_time_options`, carry it
   over exactly; otherwise fall back to that day's first available time and
   flag it with a "time slot changed" banner.
4. For anything that didn't match: render a "not found this season, please
   re-pick" banner — this is the same message the old rollover email used to
   send, just shown on the page instead of emailed.
5. Special events are **never** auto-carried across seasons (same as the old
   matcher's explicit behavior) — events are year-scoped, not season-scoped,
   so there's no reliable mapping to carry.

A code older than `manifest.previous` can't be recovered at all (its data is
gone) — the UI says so plainly rather than pretending to match anything.

## How the GitHub Actions workflows chain

Three workflows, deliberately kept separate so each has a focused log and its
own trigger:

1. **`build-cache.yml`** — daily cron + manual dispatch. Runs
   `tools/build_cache.py`, which fetches/parses live sources, reconciles
   against the previously-committed cache (see index-stability above), prunes
   anything outside the retention window, and commits `docs/data/**` and
   `docs/logos/**` if anything changed.
2. **`deploy-pages.yml`** — triggers on any push touching `docs/**`. Since
   `build-cache.yml`'s own commit touches exactly that path, a cache refresh
   automatically triggers a republish with no explicit chaining needed. Also
   generates `docs/config.js` from repo Variables (`ADS_PUBLISHER_ID`,
   `WORKER_BASE_URL` — see DEPLOYMENT.md) and copies `shared/` into
   `docs/shared/` (both gitignored, regenerated fresh on every deploy).
3. **`deploy-worker.yml`** — triggers only on pushes to `worker/**` or
   `shared/**` (the code the Worker actually bundles), running the JS test
   suite before `wrangler deploy`. Deliberately decoupled from the daily data
   cadence, since the Worker fetches data live rather than bundling it.
