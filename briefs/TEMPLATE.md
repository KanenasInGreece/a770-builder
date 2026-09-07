# Brief template

A brief is the ceiling of what the seat delivers: the builder follows it to the letter and adds nothing unasked, so
anything that must appear in the result must be written here, and the test command must be one that can pass inside the
boundary. Copy this file, keep the headings, replace the text under them, delete what does not apply. The example that
qualified the seat is `T1-sanitize-entity-tests.md` beside it.

# Task: <one line: what changes, in which file>

You are in a standalone clone of <repository> at commit <sha> (<version>). The task is <N> edits in <file(s)> and
nothing else. Do not create files. Do not edit any file not named below.

## The files

- `<path>` — <what it is, what to read first>. Large files are read with `sed -n '<from>,<to>p'`, never whole.
- `<path>` — <the file whose idiom to copy, and the lines to look at>.

## The change

<Each edit as a row: file, line, the line today, the line after. Or each behaviour as its own numbered item, with the
exact value to assert. Anything that must appear verbatim goes in a fenced block, quoted, not described.>

## Text that must appear as written

```
<docstrings, comments, messages: exactly as they must land>
```

## Verify — run exactly this and print its last two lines

```
<one command, on the named test files only, never the whole suite; it must finish inside the model's shell limit
(120 seconds) and pass without network or host services>
```

Expected: `<the pytest summary line>` and `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state (no commit, push, merge, branch switch).
No docker, no systemctl, no network. Keep every edit on one physical line; never re-wrap.

## Stop when

<the observable condition: the files grep as stated, the summary line reads as expected, EXIT=0>. Then print the list of
files you changed.
