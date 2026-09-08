// expect-shim.mjs — a minimal Jest-compatible surface over node:test / node:assert, just enough to run
// the polyglot-benchmark JavaScript reference exercise's own spec file unmodified under `node --test`.
// Supplies exactly what forth.spec.js uses: describe, test, xtest (-> test, since Aider's own grading flips
// every xtest to test before running the suite, so the shim reproduces that all-enabled run rather than
// Exercism's own progressive-unlock default), beforeEach, and
// expect(x) with toEqual, toBe, toThrow, toBeUndefined, toBeNull, toBeTruthy. Nothing more.
//
// Import this module for its side effect (it installs the names on globalThis), before importing the
// spec file itself — see run.test.mjs.

import { describe as nodeDescribe, test as nodeTest, beforeEach as nodeBeforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.describe = nodeDescribe;
globalThis.test = nodeTest;
globalThis.xtest = nodeTest;
globalThis.beforeEach = nodeBeforeEach;

function toEqual(actual, expected) {
  assert.deepStrictEqual(actual, expected);
}

function toBe(actual, expected) {
  assert.strictEqual(actual, expected);
}

function toThrow(actual, expected) {
  if (typeof actual !== 'function') {
    throw new TypeError('expect(...).toThrow() requires a function that throws');
  }
  let thrown = null;
  try {
    actual();
  } catch (err) {
    thrown = err;
  }
  assert.ok(thrown !== null, 'expected function to throw');
  if (expected === undefined) {
    return;
  }
  if (expected instanceof Error) {
    assert.ok(
      thrown instanceof expected.constructor,
      `expected thrown error to be an instance of ${expected.constructor.name}, got ${thrown && thrown.constructor && thrown.constructor.name}`,
    );
    assert.strictEqual(thrown.message, expected.message);
    return;
  }
  throw new TypeError('expect(...).toThrow(expected) only supports an Error instance or no argument');
}

function toBeUndefined(actual) {
  assert.strictEqual(actual, undefined);
}

function toBeNull(actual) {
  assert.strictEqual(actual, null);
}

function toBeTruthy(actual) {
  assert.ok(Boolean(actual), `expected ${JSON.stringify(actual)} to be truthy`);
}

globalThis.expect = function expect(actual) {
  return {
    toEqual: (expected) => toEqual(actual, expected),
    toBe: (expected) => toBe(actual, expected),
    toThrow: (expected) => toThrow(actual, expected),
    toBeUndefined: () => toBeUndefined(actual),
    toBeNull: () => toBeNull(actual),
    toBeTruthy: () => toBeTruthy(actual),
  };
};
