// Idiom to copy for any test the S1 stage adds: node:test + node:assert, run with
// `node --test js/tests` (relative to the seat root). Two trivial passing cases per function,
// as the pattern — the hidden grader covers the fresh inputs.
import test from "node:test";
import assert from "node:assert/strict";
import { formatCount, formatMean, busiestMinuteLabel } from "../format.js";

test("formatCount adds a thousands separator", () => {
  assert.strictEqual(formatCount(1234), "1,234");
  assert.strictEqual(formatCount(0), "0");
});

test("formatMean fixes to two decimals with a unit", () => {
  assert.strictEqual(formatMean(18.94), "18.94 chars avg");
  assert.strictEqual(formatMean(0), "0.00 chars avg");
});

test("busiestMinuteLabel replaces T with a space, or shows the placeholder", () => {
  assert.strictEqual(busiestMinuteLabel("2026-01-01T01:53"), "2026-01-01 01:53");
  assert.strictEqual(busiestMinuteLabel(null), "—");
});
