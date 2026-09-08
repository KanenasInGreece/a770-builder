# Task: the logstats front end — format.js, render.js, and wiring index.html

You are in `kit/seat/` of a standalone clone of a770-builder at this release's tag: that
directory IS your project root (every path below is relative to it). The task is four edits
— `js/format.js`, a new `js/render.js`, `html/index.html`, and (only if you add cases)
`js/tests/format.test.js` — and nothing else. Do not edit `js/bar-meta.js` (already
complete, load it as-is) or any file under `python/`, `cpp/` or `design/`.

## The files

- `product-brief.md` — the JSON `compute_stats` returns (the shape `render.js` renders) and
  the page's required behaviour (the level filter, accessibility).
- `js/format.js` — three exported functions, each with a TODO body and a JSDoc contract with
  a worked example. Implement all three exactly to their contracts.
- `js/tests/format.test.js` — the idiom: `node:test` + `node:assert/strict`, two trivial
  cases per function. Copy this shape if you add more.
- `html/index.html` — the seeded skeleton: header bar (do not touch), an empty
  `<select id="level"></select>`, an empty `<section id="stats"></section>`, and two `TODO(S1)`
  comments marking exactly what to add.

## The change

1. In `js/format.js`, implement `formatCount`, `formatMean`, `busiestMinuteLabel` to their
   JSDoc contracts (each already states the exact return value for a worked example).
2. Write `js/render.js`, a pure ES module exporting exactly one function,
   `renderStats(stats)`, which takes a `compute_stats`-shaped object (optionally pre-filtered
   to one level — see item 4) and returns an HTML string that includes, somewhere in it, the
   result of `formatCount(stats.total_lines)`, `formatMean(stats.mean_message_length)` and
   `busiestMinuteLabel(stats.busiest_minute)` — no DOM access in this file (no `document`, no
   `window`).
3. In `html/index.html`, replace the first `TODO(S1)` comment with a `<label for="level">`
   and the select's `<option>`s: `"All"` plus one option per level key actually present in
   `product-brief.md`'s worked example (`DEBUG`, `ERROR`, `WARN`).
4. Replace the second `TODO(S1)` comment with an inline `<script type="module">` that imports
   `./js/render.js` (and, through it, `./js/format.js`), `fetch`es `"stats.json"`, renders
   into `#stats`, and re-renders filtered to the selected level on the select's `"change"`
   event (`"All"` shows the unfiltered totals).
5. Give `#stats` `aria-live="polite"` so a screen reader announces re-renders.

Size budget: 180 lines total across `js/format.js`, `js/render.js` and `js/tests/format.test.js`
(the files you may edit; `js/bar-meta.js` is unedited third-party code and does not count).

## Text that must appear as written

```
<label for="level">
```

```
export function renderStats(stats) {
```

## Verify — run exactly this and print its last two lines

```
node --test js/tests/*.test.js
```

Expected: `# pass 3` (or more, if you added cases) and no `# fail` above 0.

## Rules

Work only inside `kit/seat/`. No version-control command that changes state (no commit,
push, merge, branch switch). No docker, no systemctl, no network. Keep every edit on one
physical line; never re-wrap.

## Stop when

`node --test js/tests/*.test.js` passes, `html/index.html` has the label, the populated
select, the inline module script and `aria-live` on `#stats`, and the three JS files
together are at or under the 180-line budget. Then print the files you changed.

## A run specification beside this brief

See `s1-frontend.spec.json`. **FINDING carried into this brief**: `harness/render_profile.py`
`check` (rule 7, `bash_allow`) refuses any pattern whose first word is `node` (it is in
`BASH_ALLOW_FORBIDDEN_FIRST`, the "no wrapper or interpreter" rule) — so this run's
specification cannot grant you `node` in your own shell tool at all; you cannot run the
Verify command above live. Write and reason about the three files carefully; the harness runs
`node --test` for you after your patch is captured.
