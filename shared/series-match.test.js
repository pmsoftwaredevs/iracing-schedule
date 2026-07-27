import { test } from "node:test";
import assert from "node:assert/strict";
import { matchSeries } from "./series-match.js";

test("exact name match", () => {
  const old = [{ id: 1, name: "GT3 Fixed" }];
  const current = [{ id: 10, name: "GT3 Fixed" }, { id: 11, name: "Formula Vee" }];

  const result = matchSeries(old, current);

  assert.deepEqual(result.matched.map((m) => [m.old.id, m.new.id]), [[1, 10]]);
  assert.deepEqual(result.unmatched, []);
});

test("case and punctuation insensitive match", () => {
  const old = [{ id: 1, name: "IMSA Continental Tire Series - Fixed" }];
  const current = [{ id: 10, name: "imsa continental tire series fixed" }];

  const result = matchSeries(old, current);

  assert.deepEqual(result.matched.map((m) => [m.old.id, m.new.id]), [[1, 10]]);
});

test("no match falls into unmatched", () => {
  const old = [{ id: 1, name: "Retired Series" }];
  const current = [{ id: 10, name: "GT3 Fixed" }];

  const result = matchSeries(old, current);

  assert.deepEqual(result.matched, []);
  assert.deepEqual(result.unmatched.map((s) => s.id), [1]);
});

test("an ambiguous normalized match is left unmatched", () => {
  const old = [{ id: 1, name: "GT-3 Fixed" }];
  const current = [{ id: 10, name: "GT3 Fixed" }, { id: 11, name: "GT 3 Fixed" }];

  const result = matchSeries(old, current);

  assert.deepEqual(result.matched, []);
  assert.deepEqual(result.unmatched.map((s) => s.id), [1]);
});

test("extra fields on the old entry are carried through untouched", () => {
  const old = [{ name: "GT3 Fixed", timeslots: [{ day: 1, timeGmt: "19:00" }] }];
  const current = [{ name: "GT3 Fixed", index: 5 }];

  const result = matchSeries(old, current);

  assert.equal(result.matched.length, 1);
  assert.deepEqual(result.matched[0].old.timeslots, [{ day: 1, timeGmt: "19:00" }]);
  assert.equal(result.matched[0].new.index, 5);
});
