# kit/corpus/ — the generated long-context prompt

`large.py` (this directory) is produced, never committed (see `.gitignore`), by `../corpus.py`:

```
python3 kit/corpus.py generate --out kit/corpus/large.py
```

It is a straight, deterministic CONCATENATION of real source already in this repository — no
synthetic filler. Each source file is preceded by a one-line marker, `### FILE <relative/path>`,
and the files are taken in a fixed order: every file under `kit/seat/`, then every file under
`kit/reference/` (both sorted by relative path, whole trees — those are the kit's own language
corners), then this repository's own `harness/*.sh`, `harness/*.py` (top level only), every
`.py`/`.sh`/`.js`/`.md` file under `skills/` and `tests/` (recursive), and `docs/*.md` (top level
only). Content is filtered to 7-bit printable ASCII (the same filter `harness/ctx_sweep.sh` already
applies to its own prompt text), so the harness's byte-to-token convention — a token is ~4 bytes,
`len(text)//4` — holds for it.

Why: the sweep and the depth probe need a prompt that reaches the display serious profile's
131,072-token window (the smallest window any registry profile serves) on ANY machine, from THIS
repository alone, without depending on a second cloned repository. `generate`'s default floor is
655,360 bytes (640 KiB, ~163,840 estimated tokens), comfortably past that window. Because the walk
is sorted and the byte filter is deterministic, the same repository state always produces the same
bytes (`python3 kit/corpus.py check` proves it with a sha256 of two independent generations).

If the real source under `kit/seat/`, `kit/reference/`, `harness/`, `skills/`, `tests/` and
`docs/` cannot reach the requested floor, `generate` exits 2 with a message and writes nothing —
it never pads with synthetic text. The fix is to add real material to those directories (or pass
a smaller `--min-bytes` for a local demonstration), not to lower the shipped default.

`python3 kit/corpus.py index kit/corpus/large.py` prints `<line>: <name>` for every top-level
`def`, `async def` and `class` found in the `.py` segments of the generated file ONLY — the
corpus now also carries C++, JavaScript, shell, JSON and Markdown real source, and none of that is
indexed. This is what the long-read task's grader (`read-1` / S4) checks the model's answer
against.

`harness/env.sh` points `A770B_CORPUS_FILE` at this file by default. The corpus is part of the
measuring instrument, not a convenience: `harness/ctx_sweep.sh` (via `harness/guard.sh`'s
`a770b_ensure_corpus_file`) GENERATES this file when it is missing — the exact command above — and
refuses, naming that command, if generation itself fails. It never silently swaps in a different
corpus (a stranger's "8k point" would otherwise be a different prompt from ours).
