# Task: implement the C++ counting hot path and its ctypes wrapper

You are in `kit/seat/` of a standalone clone of a770-builder at this release's tag: that
directory IS your project root (every path below is relative to it). The task is two files —
`cpp/logstats.cpp` and `python/logstats/fast.py` — and nothing else. Do not edit
`cpp/Makefile`, any file under `python/logstats/` other than `fast.py`, or any file under
`js/`, `html/` or `design/`.

## The files

- `cpp/logstats.cpp` — the file's own top comment states the ABI (`count_levels`'s signature,
  what each of the 5 output longs holds, and which lines count) and marks the one function
  body as `// TODO(S3)`. Read that comment; it is the contract, not this brief restating it.
- `cpp/Makefile` — already complete; `make -C cpp` builds `cpp/liblogstats.so` with
  `-Wall -Wextra -Wpedantic -Werror`.
- `python/logstats/fast.py` — `fast_level_counts` currently always calls
  `_fallback_level_counts` (a pure-Python equivalent); `_load_library` and `_LIB` are already
  written to load `cpp/liblogstats.so` through `ctypes` when it is present. Its own comment
  marks the one line to change: `TODO(S3): call into _LIB and translate its out5 array`.
- `python/logstats/stats.py` — `compute_stats`, for parity: `fast_level_counts(text)["by_level"]`
  must equal `compute_stats(text.splitlines())["by_level"]`, and `["total_lines"]` must equal
  `compute_stats(...)["total_lines"]`, on any input where every line parses (as in
  `python/data/sample.log`).

## The change

1. In `cpp/logstats.cpp`, implement `count_levels`'s body per its own top-of-file comment:
   scan `text[0..len)` by line, and for each line whose second whitespace-separated field is
   exactly `DEBUG`, `INFO`, `WARN` or `ERROR` (and which has a non-empty third field),
   increment the matching counter in `out5[0..3]` (that fixed order) and `out5[4]`; return
   `0`. Do not allocate a `std::string` per line if you can avoid it — this is the hot path.
2. In `python/logstats/fast.py`, change `fast_level_counts` to call `_LIB.count_levels` when
   `_LIB` is not `None` (encode `text` to bytes, allocate a `ctypes.c_long * 5`, check the
   return code is `0`), translating the 5 longs into the same
   `{"by_level": {...}, "total_lines": n}` shape `_fallback_level_counts` already returns;
   keep the fallback for when `_LIB is None` or the call's return code is non-zero.

## Verify — run exactly this and print its last two lines

```
make -C cpp && uv run --with pytest python -m pytest -q python/tests
```

Expected: the Makefile's last line with no warnings, then a pytest summary line with 0
failed, and `EXIT=0`.

## Rules

Work only inside `kit/seat/`. No version-control command that changes state (no commit,
push, merge, branch switch). No docker, no systemctl, no network. Keep every edit on one
physical line; never re-wrap.

## Stop when

`make -C cpp` builds `cpp/liblogstats.so` with zero warnings and
`uv run --with pytest python -m pytest -q python/tests` passes with 0 failures. Then print
the files you changed.
