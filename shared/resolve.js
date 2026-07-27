/**
 * Turns a decoded calendar code (see calendar-code.js's decodeCalendarCode) plus a
 * season's cache JSON (see tools/build_cache.py's schema) into concrete
 * selections: which championships, with which GMT timeslots, and which special
 * events. Used by both the static picker (to pre-tick checkboxes from a pasted
 * code) and the Cloudflare Worker (to build .ics events).
 *
 * Every lookup here is best-effort: an index the season's data no longer has
 * (data shrank since the code was issued, or a hand-edited/corrupted code) is
 * silently dropped, never thrown — matches the rest of the scheme's "always
 * degrade gracefully, never error" contract.
 */

export function resolveSelections(decoded, seasonCache) {
  const championships = [];
  for (const picked of decoded.championships) {
    const championship = seasonCache?.championships?.[picked.index];
    if (!championship) continue;

    const timeslots = [];
    for (const slot of picked.timeslots) {
      if (!Number.isInteger(slot.day) || slot.day < 0 || slot.day > 6) continue;
      const timeGmt = championship.session_time_options?.[slot.timeIndex];
      if (timeGmt === undefined) continue;
      timeslots.push({ day: slot.day, timeGmt });
    }
    championships.push({ index: picked.index, championship, timeslots });
  }

  const events = [];
  for (const picked of decoded.events) {
    const event = seasonCache?.special_events?.[picked.index];
    if (!event) continue;
    events.push({ index: picked.index, event });
  }

  return { championships, events };
}

// The inverse lookup the picker UI needs when building a code from what's
// checked on the page: given a championship's own session_time_options array and
// a "HH:MM" string the user picked, find its index. Returns -1 (not -1 silently
// coerced to 0) so callers can tell "not found" apart from "found at position 0".
export function timeIndexForTime(championship, timeGmt) {
  return championship.session_time_options.indexOf(timeGmt);
}
