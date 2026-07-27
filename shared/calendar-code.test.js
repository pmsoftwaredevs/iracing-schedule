import { test } from "node:test";
import assert from "node:assert/strict";
import {
  encodeBase36,
  decodeBase36,
  formatSeasonCode,
  isValidSeasonCode,
  encodeCalendarCode,
  parseCalendarCode,
  decodeCalendarCode,
} from "./calendar-code.js";

test("encodeBase36 pads and round-trips through decodeBase36", () => {
  assert.equal(encodeBase36(5, 2), "05");
  assert.equal(encodeBase36(1295, 2), "zz");
  assert.equal(decodeBase36("05"), 5);
  assert.equal(decodeBase36("zz"), 1295);
});

test("encodeBase36 rejects negative, non-integer, and over-width values", () => {
  assert.throws(() => encodeBase36(-1, 2));
  assert.throws(() => encodeBase36(1.5, 2));
  assert.throws(() => encodeBase36(1296, 2));
});

test("decodeBase36 returns NaN for malformed input instead of throwing", () => {
  assert.ok(Number.isNaN(decodeBase36("!!")));
  assert.ok(Number.isNaN(decodeBase36("")));
  assert.ok(Number.isNaN(decodeBase36(undefined)));
});

test("formatSeasonCode / isValidSeasonCode", () => {
  assert.equal(formatSeasonCode(2026, 3), "2026S3");
  assert.equal(isValidSeasonCode("2026S3"), true);
  assert.equal(isValidSeasonCode("2026s3"), false); // quarter/tag casing matters
  assert.equal(isValidSeasonCode("26S3"), false);
  assert.throws(() => formatSeasonCode(2026, 10));
});

test("worked example from ARCHITECTURE.md round-trips exactly", () => {
  const code = encodeCalendarCode({
    seasonCode: "2026S3",
    championships: [
      { index: 5, timeslots: [{ day: 1, timeIndex: 1 }, { day: 4, timeIndex: 5 }] },
    ],
    events: [{ index: 12 }],
  });
  assert.equal(code, "2026S3C052101405E0c");

  const parsed = parseCalendarCode(code);
  assert.equal(parsed.seasonCode, "2026S3");
  assert.deepEqual(parsed.championships, [
    { index: 5, timeslots: [{ day: 1, timeIndex: 1 }, { day: 4, timeIndex: 5 }] },
  ]);
  assert.deepEqual(parsed.events, [{ index: 12 }]);
});

test("encodeCalendarCode with no selections is just the season code", () => {
  assert.equal(encodeCalendarCode({ seasonCode: "2026S3" }), "2026S3");
});

test("encodeCalendarCode rejects an invalid season code", () => {
  assert.throws(() => encodeCalendarCode({ seasonCode: "bogus" }));
});

test("multiple championships and events concatenate with no separators", () => {
  const code = encodeCalendarCode({
    seasonCode: "2026S3",
    championships: [
      { index: 0, timeslots: [{ day: 0, timeIndex: 0 }] },
      { index: 34, timeslots: [] },
    ],
    events: [{ index: 1 }, { index: 2 }],
  });
  const parsed = parseCalendarCode(code);
  assert.equal(parsed.championships.length, 2);
  assert.equal(parsed.championships[1].timeslots.length, 0);
  assert.equal(parsed.events.length, 2);
});

test("parseCalendarCode returns empty result for a string with no valid season prefix", () => {
  assert.deepEqual(parseCalendarCode("not-a-code"), { seasonCode: null, championships: [], events: [] });
  assert.deepEqual(parseCalendarCode(""), { seasonCode: null, championships: [], events: [] });
});

test("parseCalendarCode stops cleanly at an unknown tag, keeping prior records", () => {
  const parsed = parseCalendarCode("2026S3E0cX garbage");
  assert.equal(parsed.seasonCode, "2026S3");
  assert.deepEqual(parsed.events, [{ index: 12 }]);
  assert.equal(parsed.championships.length, 0);
});

test("parseCalendarCode stops cleanly on truncated trailing record", () => {
  // A championship record claiming 2 timeslots but only providing 1 full slot.
  const parsed = parseCalendarCode("2026S3C0521010");
  assert.equal(parsed.seasonCode, "2026S3");
  // Nothing usable parsed for the truncated record - not even a partial one.
  assert.equal(parsed.championships.length, 0);
});

test("decodeCalendarCode marks a season outside the retention window as empty but valid", () => {
  const manifest = { current: "2026S3", previous: "2026S2" };
  const result = decodeCalendarCode("2025S1C0000", manifest);
  assert.equal(result.valid, true);
  assert.equal(result.inRetentionWindow, false);
  assert.equal(result.seasonCode, "2025S1");
  assert.deepEqual(result.championships, []);
  assert.deepEqual(result.events, []);
});

test("decodeCalendarCode resolves records for the current season", () => {
  const manifest = { current: "2026S3", previous: "2026S2" };
  const code = encodeCalendarCode({ seasonCode: "2026S3", championships: [{ index: 1, timeslots: [] }] });
  const result = decodeCalendarCode(code, manifest);
  assert.equal(result.inRetentionWindow, true);
  assert.equal(result.championships.length, 1);
});

test("decodeCalendarCode resolves records for the previous season too", () => {
  const manifest = { current: "2026S3", previous: "2026S2" };
  const code = encodeCalendarCode({ seasonCode: "2026S2", events: [{ index: 3 }] });
  const result = decodeCalendarCode(code, manifest);
  assert.equal(result.inRetentionWindow, true);
  assert.equal(result.events.length, 1);
});

test("decodeCalendarCode on a completely malformed string is invalid, not throwing", () => {
  const result = decodeCalendarCode("garbage", { current: "2026S3", previous: "2026S2" });
  assert.equal(result.valid, false);
  assert.equal(result.inRetentionWindow, false);
});
