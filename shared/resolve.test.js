import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveSelections, timeIndexForTime } from "./resolve.js";

const seasonCache = {
  championships: [
    { name: "GT3 Fixed", session_time_options: ["00:30", "02:30", "04:30"] },
    { name: "Formula B", session_time_options: ["10:00", "19:00"] },
  ],
  special_events: [
    { slug: "firecracker-400", name: "Firecracker 400" },
    { slug: "24-hours-of-daytona", name: "24 Hours of Daytona" },
  ],
};

test("resolveSelections maps indices to championships/events with concrete timeslots", () => {
  const decoded = {
    championships: [{ index: 0, timeslots: [{ day: 1, timeIndex: 1 }] }],
    events: [{ index: 1 }],
  };
  const resolved = resolveSelections(decoded, seasonCache);
  assert.equal(resolved.championships.length, 1);
  assert.equal(resolved.championships[0].championship.name, "GT3 Fixed");
  assert.deepEqual(resolved.championships[0].timeslots, [{ day: 1, timeGmt: "02:30" }]);
  assert.equal(resolved.events.length, 1);
  assert.equal(resolved.events[0].event.name, "24 Hours of Daytona");
});

test("resolveSelections silently drops an out-of-range championship index", () => {
  const decoded = { championships: [{ index: 99, timeslots: [] }], events: [] };
  const resolved = resolveSelections(decoded, seasonCache);
  assert.deepEqual(resolved.championships, []);
});

test("resolveSelections silently drops an out-of-range event index", () => {
  const decoded = { championships: [], events: [{ index: 99 }] };
  const resolved = resolveSelections(decoded, seasonCache);
  assert.deepEqual(resolved.events, []);
});

test("resolveSelections drops a timeslot whose time index no longer exists", () => {
  const decoded = { championships: [{ index: 1, timeslots: [{ day: 0, timeIndex: 50 }] }], events: [] };
  const resolved = resolveSelections(decoded, seasonCache);
  assert.equal(resolved.championships.length, 1);
  assert.deepEqual(resolved.championships[0].timeslots, []);
});

test("resolveSelections drops a timeslot with an out-of-range day", () => {
  const decoded = { championships: [{ index: 1, timeslots: [{ day: 9, timeIndex: 0 }] }], events: [] };
  const resolved = resolveSelections(decoded, seasonCache);
  assert.deepEqual(resolved.championships[0].timeslots, []);
});

test("resolveSelections tolerates a missing/empty cache without throwing", () => {
  const decoded = { championships: [{ index: 0, timeslots: [] }], events: [{ index: 0 }] };
  assert.doesNotThrow(() => resolveSelections(decoded, {}));
  assert.doesNotThrow(() => resolveSelections(decoded, undefined));
});

test("timeIndexForTime finds the position, or -1 when absent", () => {
  assert.equal(timeIndexForTime(seasonCache.championships[0], "02:30"), 1);
  assert.equal(timeIndexForTime(seasonCache.championships[0], "23:59"), -1);
});
