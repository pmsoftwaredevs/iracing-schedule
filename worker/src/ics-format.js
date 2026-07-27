/**
 * Minimal hand-rolled RFC 5545 helpers — no npm icalendar-equivalent dependency,
 * since only a handful of fixed property types (UID/SUMMARY/LOCATION/DTSTART/
 * DTEND/DTSTAMP) are ever emitted by ics.js.
 */

const CRLF = "\r\n";

export function escapeText(text) {
  return String(text)
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\n/g, "\\n");
}

// RFC 5545 §3.1: lines SHOULD be folded at 75 octets, with each continuation
// line starting with a single space.
export function foldLine(text) {
  if (text.length <= 75) return text;
  const parts = [];
  let rest = text;
  while (rest.length > 75) {
    parts.push(rest.slice(0, 75));
    rest = " " + rest.slice(75);
  }
  parts.push(rest);
  return parts.join(CRLF);
}

function pad(n, width = 2) {
  return String(n).padStart(width, "0");
}

export function formatDateTimeUtc(date) {
  return (
    `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}Z`
  );
}

export function formatDateOnly(isoDate) {
  return isoDate.replace(/-/g, "");
}

function property(name, value) {
  return foldLine(`${name}:${value}`);
}

/** Timed VEVENT — championship sessions and the week-13 reminder, always UTC
 * (Z-suffixed), no VTIMEZONE, matching the old app/ics_builder.py's approach
 * since iRacing session times are fixed GMT and don't shift with DST. */
export function buildTimedEvent({ uid, summary, location, dtstart, dtend }) {
  const lines = ["BEGIN:VEVENT", property("UID", uid), property("SUMMARY", escapeText(summary))];
  if (location) lines.push(property("LOCATION", escapeText(location)));
  lines.push(property("DTSTART", formatDateTimeUtc(dtstart)));
  lines.push(property("DTEND", formatDateTimeUtc(dtend)));
  lines.push(property("DTSTAMP", formatDateTimeUtc(new Date())));
  lines.push("END:VEVENT");
  return lines;
}

/** All-day VEVENT — special events. `dtendDate` must already be the exclusive
 * end (caller adds +1 day to the inclusive announced end date, per RFC 5545). */
export function buildAllDayEvent({ uid, summary, location, dtstartDate, dtendDate }) {
  const lines = ["BEGIN:VEVENT", property("UID", uid), property("SUMMARY", escapeText(summary))];
  if (location) lines.push(property("LOCATION", escapeText(location)));
  lines.push(property("DTSTART", formatDateOnly(dtstartDate)));
  lines.push(property("DTEND", formatDateOnly(dtendDate)));
  lines.push(property("DTSTAMP", formatDateTimeUtc(new Date())));
  lines.push("END:VEVENT");
  return lines;
}

export function buildCalendar({ calname, eventLines }) {
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//iRacing Calendar//iracing-calendar//EN",
    property("X-WR-CALNAME", escapeText(calname)),
    ...eventLines.flat(),
    "END:VCALENDAR",
  ];
  return lines.join(CRLF) + CRLF;
}
