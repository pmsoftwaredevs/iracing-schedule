/**
 * Direct JS port of the old app/ics_builder.py's event-generation semantics,
 * operating on the plain data shapes shared/resolve.js's resolveSelections
 * produces (championship + timeslots, or special event) instead of SQLModel
 * rows — there's no database, so there are no row ids for UIDs either; UIDs are
 * built from the season code + championship/event index + week/day/time instead,
 * which is exactly as stable across requests since those indices don't change
 * once published (see tools/build_cache.py's index-stability invariant).
 *
 * Pure functions over plain data — no Workers-specific API — so this is fully
 * testable with plain `node --test`, no Workers runtime needed.
 */

import { buildAllDayEvent, buildTimedEvent, buildCalendar } from "./ics-format.js";

export const DEFAULT_RACE_DURATION_MINUTES = 60;

function weekOffsetMinutes(slot) {
  const [hh, mm] = slot.timeGmt.split(":").map(Number);
  return slot.day * 24 * 60 + hh * 60 + mm;
}

// Half of a gap between two back-to-back sessions, rounded to the nearest 5
// minutes — or the default once the gap is a day or more, since at that point
// the two sessions aren't really "back-to-back" anymore.
function durationFromGapMinutes(gapMinutes) {
  if (gapMinutes >= 24 * 60) return DEFAULT_RACE_DURATION_MINUTES;
  return Math.round(gapMinutes / 2 / 5) * 5;
}

// Sessions of the same championship can land less than 24h apart (e.g. a
// Wednesday and a Thursday timeslot). When that happens, shrink the earlier
// session's event to half the gap until the next one instead of the default
// duration, so back-to-back races don't show as overlapping on the calendar.
// Keyed by object identity (not a DB row id, which doesn't exist here) since
// every timeslot object is freshly resolved per request.
function slotDurationsMinutes(timeslots) {
  if (timeslots.length < 2) {
    return new Map(timeslots.map((slot) => [slot, DEFAULT_RACE_DURATION_MINUTES]));
  }
  const ordered = [...timeslots].sort((a, b) => weekOffsetMinutes(a) - weekOffsetMinutes(b));
  const durations = new Map();
  for (let i = 0; i < ordered.length; i++) {
    const slot = ordered[i];
    const next = ordered[(i + 1) % ordered.length];
    let gap = weekOffsetMinutes(next) - weekOffsetMinutes(slot);
    if (gap <= 0) gap += 7 * 24 * 60;
    durations.set(slot, durationFromGapMinutes(gap));
  }
  return durations;
}

// Finds the date within [rangeStartIso, rangeEndIso] (inclusive "YYYY-MM-DD"
// strings) matching the given weekday (0=Monday..6=Sunday, matching the old
// Timeslot.day_of_week convention) — weeks don't all start on the same weekday.
function dateForWeekday(rangeStartIso, rangeEndIso, dayOfWeek) {
  const start = new Date(`${rangeStartIso}T00:00:00Z`);
  const end = new Date(`${rangeEndIso}T00:00:00Z`);
  const totalDays = Math.round((end.getTime() - start.getTime()) / (24 * 3600 * 1000));
  for (let offset = 0; offset <= totalDays; offset++) {
    const candidate = new Date(start.getTime() + offset * 24 * 3600 * 1000);
    const pyDow = (candidate.getUTCDay() + 6) % 7; // JS Sunday=0 -> Python Monday=0
    if (pyDow === dayOfWeek) return candidate;
  }
  return null;
}

function seriesEvents(picked, seasonCode) {
  const { index, championship, timeslots } = picked;
  const durations = slotDurationsMinutes(timeslots);
  const events = [];
  for (const week of championship.weeks || []) {
    for (const slot of timeslots) {
      const day = dateForWeekday(week.date_start, week.date_end, slot.day);
      if (!day) continue;
      const [hh, mm] = slot.timeGmt.split(":").map(Number);
      const dtstart = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hh, mm));
      const durationMinutes = week.duration_minutes || durations.get(slot);
      const dtend = new Date(dtstart.getTime() + durationMinutes * 60000);
      events.push(
        buildTimedEvent({
          uid: `${seasonCode}-c${index}-w${week.week_number}-d${slot.day}t${slot.timeGmt.replace(":", "")}@iracing-calendar`,
          summary: `${championship.name} — ${week.track_name} (W${week.week_number})`,
          location: week.track_name,
          dtstart,
          dtend,
        })
      );
    }
  }
  return events;
}

// Week 13 has no regular schedule (daily track changes, iRacing's own
// build/transition week), so instead of a real race there's one reminder event
// nudging the subscriber to come re-pick championships for next season — timed
// using the first subscribed championship's own timeslot, and that specific
// championship's own last scheduled week (not blended across every picked
// championship, since a few continuous multi-season ones run far more than 12
// weeks and would otherwise skew the estimate months too late).
function week13ReminderEvent(resolved, seasonCode) {
  const first = resolved.championships[0];
  if (!first || !first.timeslots.length) return null;
  const weeks = first.championship.weeks || [];
  if (!weeks.length) return null;

  const lastWeekEndIso = weeks.reduce((max, w) => (w.date_end > max ? w.date_end : max), weeks[0].date_end);
  const lastWeekEnd = new Date(`${lastWeekEndIso}T00:00:00Z`);
  const week13Start = new Date(lastWeekEnd.getTime() + 24 * 3600 * 1000);
  const week13End = new Date(week13Start.getTime() + 6 * 24 * 3600 * 1000);

  const slot = first.timeslots[0];
  const day = dateForWeekday(week13Start.toISOString().slice(0, 10), week13End.toISOString().slice(0, 10), slot.day);
  if (!day) return null;

  const [hh, mm] = slot.timeGmt.split(":").map(Number);
  const dtstart = new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), hh, mm));
  const dtend = new Date(dtstart.getTime() + DEFAULT_RACE_DURATION_MINUTES * 60000);
  return buildTimedEvent({
    uid: `${seasonCode}-week13-reminder-c${first.index}@iracing-calendar`,
    summary: "Pick your iRacing championships for next season!",
    location: "",
    dtstart,
    dtend,
  });
}

function addDaysIso(isoDate, days) {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// All-day, spanning the full announced date range inclusive. Per RFC 5545 an
// all-day DTEND is exclusive, so it's set to one day past the last inclusive day
// (e.g. a cross-month event like Firecracker 400, June 30 - July 6).
function specialEventEvent(picked, seasonCode) {
  const { index, event } = picked;
  return buildAllDayEvent({
    uid: `${seasonCode}-e${index}@iracing-calendar`,
    summary: event.name,
    location: event.track_name || "",
    dtstartDate: event.date_start,
    dtendDate: addDaysIso(event.date_end, 1),
  });
}

/** Builds the full `.ics` text for a resolved set of selections (see
 * shared/resolve.js's resolveSelections). `resolved` with no championships and
 * no events is a normal, valid input — it just produces a calendar with zero
 * events, per the "always decode to empty, never error" contract. */
export function buildIcs(resolved, seasonCode) {
  const eventLines = [];
  for (const picked of resolved.championships) {
    eventLines.push(...seriesEvents(picked, seasonCode));
  }
  for (const picked of resolved.events) {
    eventLines.push(specialEventEvent(picked, seasonCode));
  }
  const reminder = week13ReminderEvent(resolved, seasonCode);
  if (reminder) eventLines.push(reminder);
  return buildCalendar({ calname: "iRacing Calendar", eventLines });
}
