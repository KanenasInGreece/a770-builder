/**
 * Pure rendering: a compute_stats-shaped object to an HTML fragment. Fresh, MIT, this
 * repository. No DOM access here (no `document`, no `window`) — index.html's inline module
 * injects the returned string into #stats itself.
 */
import { formatCount, formatMean, busiestMinuteLabel } from "./format.js";

function escape(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function list(counts) {
  return Object.entries(counts)
    .map(([key, n]) => `<li>${escape(key)}: ${formatCount(n)}</li>`)
    .join("");
}

export function renderStats(stats) {
  return [
    `<p>Total lines: ${formatCount(stats.total_lines)}</p>`,
    `<p>Busiest minute: ${escape(busiestMinuteLabel(stats.busiest_minute))}</p>`,
    `<p>Mean message length: ${formatMean(stats.mean_message_length)}</p>`,
    `<h2>By level</h2><ul>${list(stats.by_level)}</ul>`,
    `<h2>By component</h2><ul>${list(stats.by_component)}</ul>`,
  ].join("");
}
