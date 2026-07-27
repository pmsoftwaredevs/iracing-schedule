import { test } from "node:test";
import assert from "node:assert/strict";
import { buildIcs } from "../src/ics.js";

function unfoldLines(text) {
  return text.replace(/\r\n[ \t]/g, "");
}

function parseEvents(icsText) {
  const unfolded = unfoldLines(icsText);
  const blocks = unfolded.split("BEGIN:VEVENT").slice(1).map((b) => b.split("END:VEVENT")[0]);
  return blocks.map((block) => {
    const props = {};
    for (const line of block.split("\r\n")) {
      const idx = line.indexOf(":");
      if (idx === -1) continue;
      props[line.slice(0, idx)] = line.slice(idx + 1);
    }
    return props;
  });
}

function nonReminderEvents(icsText) {
  return parseEvents(icsText).filter((e) => !e.SUMMARY.includes("next season"));
}

function parseDateTimeUtc(stamp) {
  return new Date(`${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)}T${stamp.slice(9, 11)}:${stamp.slice(11, 13)}:00Z`);
}

function championship(overrides = {}) {
  return { name: "GT3 Fixed", ...overrides };
}

test("buildIcs produces one UTC VEVENT per week per timeslot", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({
          weeks: [{ week_number: 1, track_name: "Spa-Francorchamps", date_start: "2026-01-06", date_end: "2026-01-12" }],
        }),
        timeslots: [
          { day: 2, timeGmt: "19:00" }, // Wednesday
          { day: 5, timeGmt: "14:30" }, // Saturday
        ],
      },
    ],
    events: [],
  };
  const ics = buildIcs(resolved, "2026S3");
  const events = nonReminderEvents(ics);

  assert.equal(events.length, 2);
  const starts = events.map((e) => e.DTSTART).sort();
  assert.deepEqual(starts, ["20260107T190000Z", "20260110T143000Z"]);
  assert.ok(events.every((e) => e.SUMMARY === "GT3 Fixed — Spa-Francorchamps"));
});

test("sessions less than 24h apart shrink to half the gap as duration", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({
          weeks: [{ week_number: 1, track_name: "Spa-Francorchamps", date_start: "2026-01-06", date_end: "2026-01-12" }],
        }),
        // Same day, 3 hours apart.
        timeslots: [{ day: 2, timeGmt: "19:00" }, { day: 2, timeGmt: "22:00" }],
      },
    ],
    events: [],
  };
  const ics = buildIcs(resolved, "2026S3");
  const events = nonReminderEvents(ics).sort((a, b) => (a.DTSTART < b.DTSTART ? -1 : 1));

  assert.equal(events.length, 2);
  assert.equal(events[0].DTSTART, "20260107T190000Z");
  assert.equal(events[0].DTEND, "20260107T203000Z"); // 3h gap, half is 1h30
  assert.equal(events[1].DTSTART, "20260107T220000Z");
  assert.equal(events[1].DTEND, "20260107T230000Z"); // wraps to next week, ~a week away -> default 60min
});

test("sessions a week apart keep the default 60-minute duration", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({
          weeks: [{ week_number: 1, track_name: "Spa-Francorchamps", date_start: "2026-01-06", date_end: "2026-01-12" }],
        }),
        timeslots: [{ day: 2, timeGmt: "19:00" }, { day: 5, timeGmt: "14:30" }],
      },
    ],
    events: [],
  };
  const ics = buildIcs(resolved, "2026S3");
  const events = nonReminderEvents(ics);

  assert.equal(events.length, 2);
  for (const event of events) {
    assert.equal(parseDateTimeUtc(event.DTEND).getTime() - parseDateTimeUtc(event.DTSTART).getTime(), 60 * 60000);
  }
});

test("an explicit week duration overrides the gap-based estimate", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({
          name: "IMSA Michelin Pilot Challenge",
          weeks: [{ week_number: 1, track_name: "Watkins Glen", date_start: "2026-01-06", date_end: "2026-01-12", duration_minutes: 120 }],
        }),
        // Close together would normally shrink to a 1h30 half-gap estimate, but
        // the PDF explicitly states 120 minutes for this week.
        timeslots: [{ day: 2, timeGmt: "19:00" }, { day: 2, timeGmt: "22:00" }],
      },
    ],
    events: [],
  };
  const ics = buildIcs(resolved, "2026S3");
  const events = nonReminderEvents(ics);
  assert.equal(events.length, 2);
  // Both events span exactly 120 minutes regardless of the tight 3h gap between
  // them — including the 22:00 one, whose +120min end crosses midnight into the
  // next calendar day.
  for (const event of events) {
    assert.equal(parseDateTimeUtc(event.DTEND).getTime() - parseDateTimeUtc(event.DTSTART).getTime(), 120 * 60000);
  }
});

test("a single-day special event's DTEND still covers that day (exclusive end)", () => {
  const resolved = {
    championships: [],
    events: [{ index: 0, event: { name: "Roar Before the 24", date_start: "2026-01-09", date_end: "2026-01-09" } }],
  };
  const events = parseEvents(buildIcs(resolved, "2026S3"));
  assert.equal(events.length, 1);
  assert.equal(events[0].DTSTART, "20260109");
  assert.equal(events[0].DTEND, "20260110");
});

test("a multi-day special event spans its full announced range inclusive", () => {
  const resolved = {
    championships: [],
    events: [{ index: 0, event: { name: "Firecracker 400", date_start: "2026-06-30", date_end: "2026-07-06" } }],
  };
  const events = parseEvents(buildIcs(resolved, "2026S3"));
  assert.equal(events.length, 1);
  assert.equal(events[0].DTSTART, "20260630");
  assert.equal(events[0].DTEND, "20260707"); // exclusive end, covers Jun 30 - Jul 6 inclusive
  assert.equal(events[0].SUMMARY, "Firecracker 400");
});

test("a special event with a track name sets LOCATION", () => {
  const resolved = {
    championships: [],
    events: [{ index: 0, event: { name: "Bathurst 12", date_start: "2026-02-20", date_end: "2026-02-22", track_name: "Mount Panorama Circuit" } }],
  };
  const events = parseEvents(buildIcs(resolved, "2026S3"));
  assert.equal(events[0].LOCATION, "Mount Panorama Circuit");
});

test("week-13 reminder is generated after the championship's last week, off its first timeslot", () => {
  const resolved = {
    // Last week (12) ends Monday 2026-08-31, so week 13 runs Tue 2026-09-01 .. Mon 2026-09-07.
    championships: [
      {
        index: 0,
        championship: championship({ weeks: [{ week_number: 12, track_name: "Some Track", date_start: "2026-08-25", date_end: "2026-08-31" }] }),
        timeslots: [{ day: 2, timeGmt: "19:00" }], // Wednesday
      },
    ],
    events: [],
  };
  const reminders = parseEvents(buildIcs(resolved, "2026S3")).filter((e) => e.SUMMARY.includes("next season"));
  assert.equal(reminders.length, 1);
  assert.equal(reminders[0].DTSTART, "20260902T190000Z");
});

test("only one week-13 reminder is generated, timed off the FIRST championship", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({ weeks: [{ week_number: 12, track_name: "Some Track", date_start: "2026-08-25", date_end: "2026-08-31" }] }),
        timeslots: [{ day: 2, timeGmt: "19:00" }], // Wednesday
      },
      {
        index: 1,
        championship: championship({ name: "Formula Vee", weeks: [{ week_number: 12, track_name: "Other Track", date_start: "2026-08-25", date_end: "2026-08-31" }] }),
        timeslots: [{ day: 5, timeGmt: "10:00" }], // Saturday
      },
    ],
    events: [],
  };
  const reminders = parseEvents(buildIcs(resolved, "2026S3")).filter((e) => e.SUMMARY.includes("next season"));
  assert.equal(reminders.length, 1);
  assert.equal(reminders[0].DTSTART, "20260902T190000Z"); // first championship's Wednesday slot, not the second's Saturday
});

test("no week-13 reminder when no championships are selected", () => {
  const resolved = { championships: [], events: [{ index: 0, event: { name: "Daytona 24", date_start: "2026-01-24", date_end: "2026-01-25" } }] };
  const reminders = parseEvents(buildIcs(resolved, "2026S3")).filter((e) => e.SUMMARY.includes("next season"));
  assert.deepEqual(reminders, []);
});

test("no week-13 reminder when the selected championship has no timeslots", () => {
  const resolved = {
    championships: [
      {
        index: 0,
        championship: championship({ weeks: [{ week_number: 12, track_name: "Some Track", date_start: "2026-08-25", date_end: "2026-08-31" }] }),
        timeslots: [],
      },
    ],
    events: [],
  };
  const reminders = parseEvents(buildIcs(resolved, "2026S3")).filter((e) => e.SUMMARY.includes("next season"));
  assert.deepEqual(reminders, []);
});

test("an entirely empty resolved selection still produces a valid, empty calendar", () => {
  const ics = buildIcs({ championships: [], events: [] }, "2026S3");
  assert.match(ics, /^BEGIN:VCALENDAR\r\n/);
  assert.match(ics, /END:VCALENDAR\r\n$/);
  assert.equal(parseEvents(ics).length, 0);
});
