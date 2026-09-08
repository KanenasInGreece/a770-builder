# logstats review rubric

For the reviewer model `harness/run_suite.sh` names for a stage's `maintainable` and (on S0
and S1 only — see `kit/suite.json`'s per-stage `axes`) `usable` scores. The reviewer reads the
stage's diff and its brief; it never sees `kit/hidden/`'s graders and never built the change
itself — a reviewer is never the builder. Score each line 0–5 (0 = fails outright, 5 =
exemplary); the reviewer's response must start with the model name it is reviewing as, e.g.
`Reviewed by: <model>`.

## Maintainable (every stage; drawn from Google's "what to look for in a code review")

1. **Design** — does the change fit the stage's stated shape (the file(s) named, the
   interface fixed in `product-brief.md`), or does it reach outside its scope?
2. **Functionality** — does the change do what its brief asked, including the edge cases the
   brief names (empty input, a rejected component, a tie in the busiest minute)?
3. **Complexity** — is the implementation as simple as the task allows, or is there
   unnecessary indirection, premature generality, or a clever trick a maintainer would have
   to puzzle over?
4. **Naming** — do names say what they hold or do (a function named for its effect, a
   variable named for its content), matching the contracts already written in the stubs?
5. **Comments and consistency** — do comments explain *why*, not restate *what*; does the
   change match the surrounding file's style (the seeded idiom it was told to copy)?

## Usable (S0 and S1 only)

1. **Labels** — is every interactive control (the level `<select>`) labelled with visible or
   accessible text, not identified by placement alone?
2. **Keyboard reachability** — can every control be reached and operated without a mouse (tab
   order, no keyboard trap)?
3. **States** — does the page show a loading state before `stats.json` resolves and does the
   rendered content reflect the current filter, not a stale one?
4. **Errors shown** — if `stats.json` fails to load, is that told to the user, not left as a
   silently blank page?
5. **No dead controls** — does every control on the page do something (no `<select>` with no
   `change` handler, no unreachable option)?

S0 is additionally judged on whether its design note states the JSON interface **completely**
(all five keys, correctly) and **concisely** (no restating of the whole product brief) — fold
that judgement into the Design and Complexity lines above rather than scoring it separately.
