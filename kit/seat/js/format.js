/**
 * Pure formatting helpers for the logstats page (S1 front end stage). Fresh, MIT, this
 * repository. No DOM access here — `render.js` is where JSON meets markup.
 *
 * @param {number} n - a non-negative integer count.
 * @returns {string} `n` with a thousands separator, e.g. formatCount(1234) === "1,234",
 *   formatCount(0) === "0".
 */
export function formatCount(n) {
  // TODO(S1): implement.
  throw new Error("formatCount not implemented");
}

/**
 * @param {number} n - the mean message length, e.g. 18.94.
 * @returns {string} `n` fixed to 2 decimal places followed by " chars avg", e.g.
 *   formatMean(18.94) === "18.94 chars avg", formatMean(0) === "0.00 chars avg".
 */
export function formatMean(n) {
  // TODO(S1): implement.
  throw new Error("formatMean not implemented");
}

/**
 * @param {string|null} minute - a "YYYY-MM-DDTHH:MM" bucket, or null when there is none.
 * @returns {string} the bucket with "T" replaced by a space, e.g.
 *   busiestMinuteLabel("2026-01-01T01:53") === "2026-01-01 01:53"; busiestMinuteLabel(null)
 *   === "—" (a single em dash).
 */
export function busiestMinuteLabel(minute) {
  // TODO(S1): implement.
  throw new Error("busiestMinuteLabel not implemented");
}
