# Task: implement the Forth evaluator in `kit/reference/javascript/forth/forth.js`

You are in a standalone clone of KanenasInGreece/a770-builder, branch `profiling-kit`. The task is implementing
one file, `kit/reference/javascript/forth/forth.js`, and nothing else. Do not create files. Do not edit any
file not named below. This is Aider's polyglot-benchmark JavaScript exercise `forth` (Exercism practice track,
MIT licence 2021 — see `kit/reference/SOURCES.md` and the `LICENSE` beside this exercise), carried into this
kit verbatim.

## The files

- `kit/reference/javascript/forth/forth.js` — the file to implement. It ships as a skeleton `Forth` class
  whose `constructor`, `evaluate()` and `stack` getter each throw.
- `kit/reference/javascript/forth/forth.spec.js` — the exercise's own spec file. Read it, never edit it: it
  constructs `new Forth()`, calls `forth.evaluate(program)`, and reads `forth.stack`.

## The exercise's instructions, verbatim

```
# Instructions

Implement an evaluator for a very simple subset of Forth.

[Forth][forth]
is a stack-based programming language.
Implement a very basic evaluator for a small subset of Forth.

Your evaluator has to support the following words:

- `+`, `-`, `*`, `/` (integer arithmetic)
- `DUP`, `DROP`, `SWAP`, `OVER` (stack manipulation)

Your evaluator also has to support defining new words using the customary syntax: `: word-name definition ;`.

To keep things simple the only data type you need to support is signed integers of at least 16 bits size.

You should use the following rules for the syntax: a number is a sequence of one or more (ASCII) digits, a word is a sequence of one or more letters, digits, symbols or punctuation that is not a number.
(Forth probably uses slightly different rules, but this is close enough.)

Words are case-insensitive.

[forth]: https://en.wikipedia.org/wiki/Forth_%28programming_language%29
```

## The change

Implement the `Forth` class: `stack` starts empty; `evaluate(program)` parses the (case-insensitive)
whitespace-separated words, pushing numbers, applying `+ - * /` and `dup drop swap over` to the stack, and
defining new words with `: name ... ;` (a defined word may reference an existing word or number of the same
name — capture it at definition time). Throw `new Error('Stack empty')` when an operator or stack word needs
more values than the stack holds, `new Error('Division by zero')` on `/` by zero, `new Error('Invalid
definition')` when a definition's name is a number, `new Error('Unknown command')` on an undefined word.

## Verify — run exactly this and print its last two lines

```
node --test kit/reference/javascript/forth/run.test.mjs
```

Expected: `ℹ pass 1` and `ℹ fail 0` (only the one enabled `test` in `forth.spec.js` runs; the rest of that file
is `xtest`, which the shim maps to `test.skip`, so a passing run reports 1 pass and many skipped — see the note
in `kit/reference/SOURCES.md` on why this count is not Aider's own number). `EXIT=0`.

## Rules

Work only inside this directory. No version-control command that changes state (no commit, push, merge, branch
switch). No docker, no systemctl, no network. Keep every edit on one physical line; never re-wrap.

## Stop when

`node --test kit/reference/javascript/forth/run.test.mjs` exits 0 with `pass 1` and `fail 0`. Then print the
list of files you changed.

## A run specification beside this brief

`ref-javascript-forth.spec.json`, beside this file. Its `bash_allow` carries no entry for this grader — see the
FINDING in `kit/reference/SOURCES.md`: the run specification's rule refuses any `bash_allow` pattern whose
first word is `node`.
