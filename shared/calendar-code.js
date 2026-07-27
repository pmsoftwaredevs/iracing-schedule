/**
 * Encodes/decodes a user's calendar selections as a deterministic, self-describing
 * code embedded directly in the URL — there is no server-side mapping table. Two
 * codes with the same characters always mean the same selections; reconstructing a
 * code from scratch (by re-picking the same championships/events/timeslots)
 * reproduces the exact same calendar.
 *
 * Shape: <season code><record><record>...<record>, with an arbitrary number of
 * records (zero or more) concatenated directly with no separators. Two record
 * types exist:
 *
 *   Championship: "C" + championship index (2 base36) + timeslot count (1 base36)
 *                 + for each timeslot: day-of-week (1 base36) + time-slot index (2 base36)
 *   Event:        "E" + special-event index (2 base36)
 *
 * Record tags are always uppercase; every field inside a record is always
 * lowercase base36 (0-9a-z). This isn't actually load-bearing for parsing — each
 * record is self-describing via its own count field, so a parser never needs to
 * scan for the next uppercase letter — but it's kept as a cheap human-readable
 * validity cue (a well-formed code visibly has exactly one capital letter per
 * record) and a defensive assertion point.
 *
 * Field widths are sized against real data, not the arbitrary "20-40 series"
 * assumption one might guess: the real 2026 S3 schedule has 152 series, so the
 * 2-char base36 championship index (max 1295) still has ~8.5x headroom. Special
 * events (~10-30/year) and time-slot options (worst case is the 48-slot/day
 * fallback grid used when a cadence string doesn't parse) both fit comfortably in
 * 2 base36 chars too.
 *
 * Indices are POSITIONS into a season's cache JSON arrays (championships[i],
 * special_events[i], championships[i].session_time_options[j]) — see
 * tools/build_cache.py's index-stability invariant, which is what makes these
 * positions stable across cache refreshes.
 */

const BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz";

const CHAMPIONSHIP_INDEX_WIDTH = 2; // max 1295 — real max ~152 series/season
const TIMESLOT_COUNT_WIDTH = 1; // max 35 — realistic max 2-5 timeslots/week
const DAY_WIDTH = 1; // valid range 0-6 (0=Monday..6=Sunday, matches the old Timeslot.day_of_week)
const TIME_INDEX_WIDTH = 2; // max 1295 — worst case is a 48-slot/day fallback grid
const EVENT_INDEX_WIDTH = 2; // max 1295 — real max ~10-30 special events/year

const SEASON_CODE_RE = /^(\d{4})S(\d)/;

export function encodeBase36(value, width) {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`encodeBase36: value must be a non-negative integer, got ${value}`);
  }
  const maxValue = 36 ** width - 1;
  if (value > maxValue) {
    throw new Error(`encodeBase36: value ${value} exceeds max ${maxValue} for width ${width}`);
  }
  return value.toString(36).padStart(width, "0");
}

// Returns NaN for malformed/out-of-alphabet input rather than throwing — callers
// (resolve.js) treat NaN the same as any other out-of-range index: silently
// dropped, never a hard error, since a hand-edited or corrupted code should
// degrade gracefully rather than break the whole calendar.
export function decodeBase36(str) {
  if (typeof str !== "string" || str.length === 0) return NaN;
  for (const ch of str) {
    if (!BASE36_ALPHABET.includes(ch)) return NaN;
  }
  return parseInt(str, 36);
}

export function formatSeasonCode(year, quarter) {
  if (!Number.isInteger(year) || year < 0 || year > 9999) {
    throw new Error(`formatSeasonCode: invalid year ${year}`);
  }
  if (!Number.isInteger(quarter) || quarter < 0 || quarter > 9) {
    throw new Error(`formatSeasonCode: invalid quarter ${quarter}`);
  }
  return `${String(year).padStart(4, "0")}S${quarter}`;
}

export function isValidSeasonCode(seasonCode) {
  return typeof seasonCode === "string" && /^\d{4}S\d$/.test(seasonCode);
}

function encodeChampionshipRecord({ index, timeslots }) {
  let out = "C" + encodeBase36(index, CHAMPIONSHIP_INDEX_WIDTH) + encodeBase36(timeslots.length, TIMESLOT_COUNT_WIDTH);
  for (const slot of timeslots) {
    out += encodeBase36(slot.day, DAY_WIDTH) + encodeBase36(slot.timeIndex, TIME_INDEX_WIDTH);
  }
  return out;
}

function encodeEventRecord({ index }) {
  return "E" + encodeBase36(index, EVENT_INDEX_WIDTH);
}

/**
 * Builds a full code from a season code + selections. Throws on invalid input
 * (invalid season code, out-of-range indices) — this is the picker UI's own
 * write path, so it should fail loudly on a programming error rather than
 * silently emit a bad code. Decoding (see below) is the lenient, never-throws
 * direction, since it has to tolerate arbitrary pasted/hand-edited input.
 */
export function encodeCalendarCode({ seasonCode, championships = [], events = [] }) {
  if (!isValidSeasonCode(seasonCode)) {
    throw new Error(`encodeCalendarCode: invalid season code ${seasonCode}`);
  }
  let out = seasonCode;
  for (const championship of championships) {
    out += encodeChampionshipRecord(championship);
  }
  for (const event of events) {
    out += encodeEventRecord(event);
  }
  return out;
}

function emptyParsed() {
  return { seasonCode: null, championships: [], events: [] };
}

/**
 * Pure syntactic parse of a code string into a season code + records, with no
 * awareness of which seasons actually exist (see decodeCalendarCode for that).
 * Never throws: malformed input just yields fewer/no records. Truncated trailing
 * data (a record that starts but runs out of characters) stops parsing there,
 * keeping whatever records parsed cleanly before it.
 */
export function parseCalendarCode(code) {
  if (typeof code !== "string") return emptyParsed();
  const seasonMatch = SEASON_CODE_RE.exec(code);
  if (!seasonMatch) return emptyParsed();
  const seasonCode = seasonMatch[0];

  const championships = [];
  const events = [];
  let pos = seasonCode.length;

  while (pos < code.length) {
    const tag = code[pos];
    if (tag === "C") {
      pos += 1;
      if (pos + CHAMPIONSHIP_INDEX_WIDTH + TIMESLOT_COUNT_WIDTH > code.length) break;
      const index = decodeBase36(code.slice(pos, pos + CHAMPIONSHIP_INDEX_WIDTH));
      pos += CHAMPIONSHIP_INDEX_WIDTH;
      const count = decodeBase36(code.slice(pos, pos + TIMESLOT_COUNT_WIDTH));
      pos += TIMESLOT_COUNT_WIDTH;

      const timeslots = [];
      let truncated = false;
      for (let i = 0; i < count; i++) {
        if (pos + DAY_WIDTH + TIME_INDEX_WIDTH > code.length) {
          truncated = true;
          break;
        }
        const day = decodeBase36(code.slice(pos, pos + DAY_WIDTH));
        pos += DAY_WIDTH;
        const timeIndex = decodeBase36(code.slice(pos, pos + TIME_INDEX_WIDTH));
        pos += TIME_INDEX_WIDTH;
        timeslots.push({ day, timeIndex });
      }
      if (truncated) break;
      championships.push({ index, timeslots });
    } else if (tag === "E") {
      pos += 1;
      if (pos + EVENT_INDEX_WIDTH > code.length) break;
      const index = decodeBase36(code.slice(pos, pos + EVENT_INDEX_WIDTH));
      pos += EVENT_INDEX_WIDTH;
      events.push({ index });
    } else {
      break;
    }
  }

  return { seasonCode, championships, events };
}

/**
 * Decodes a code against a manifest ({current, previous} season codes — see
 * tools/build_cache.py). A season outside that 2-season retention window decodes
 * to an empty-but-valid result (inRetentionWindow: false) rather than an error,
 * per the "always decode to empty, never error" contract — both the picker UI and
 * the Cloudflare Worker rely on this to gracefully handle old bookmarked codes.
 */
export function decodeCalendarCode(code, manifest) {
  const parsed = parseCalendarCode(code);
  if (!parsed.seasonCode) {
    return { valid: false, inRetentionWindow: false, seasonCode: null, championships: [], events: [] };
  }
  const inRetentionWindow = Boolean(
    manifest && (parsed.seasonCode === manifest.current || parsed.seasonCode === manifest.previous)
  );
  if (!inRetentionWindow) {
    return { valid: true, inRetentionWindow: false, seasonCode: parsed.seasonCode, championships: [], events: [] };
  }
  return { valid: true, inRetentionWindow: true, seasonCode: parsed.seasonCode, championships: parsed.championships, events: parsed.events };
}
