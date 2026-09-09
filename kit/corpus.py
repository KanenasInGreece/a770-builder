#!/usr/bin/env python3
"""kit/corpus.py — the deterministic long-context corpus, from real source only.

`generate` CONCATENATES real files (no synthetic filler) into one text file, each file preceded
by a one-line marker `### FILE <relative/path>`, in a FIXED order: every file under `kit/seat/`,
then every file under `kit/reference/` (both sorted by relative path, no extension filter — those
two directories are the kit's own language corners, built by the rest of the profiling-kit cycle),
then this repository's own `harness/*.sh`, `harness/*.py` (top level only), every `.py`/`.sh`/`.js`/
`.md` file under `skills/` and `tests/` (recursive), and `docs/*.md` (top level only). Content is
sanitised to 7-bit printable ASCII (tab, newline, CR, 0x20-0x7e — the same filter `harness/ctx_sweep.sh`
already applies) so the harness's byte-to-token convention holds: **a token is estimated as 4 bytes**
(`len(text)//4` — the same convention `harness/bench_model.sh` and the sweep scripts use). The default
floor is 655,360 bytes (640 KiB, ~163,840 estimated tokens) so a generated file alone clears the
display serious profile's 131,072-token window. Nothing here is random and nothing depends on the
seed's iteration order: every directory walk is sorted by relative POSIX path before it is used, so
the same files on any machine produce byte-identical output — `check` proves it with sha256.

`generate` exits 2 with a message (nothing written) when the available real source cannot reach the
requested floor: add more material under `kit/seat/` or `kit/reference/` rather than lowering the
floor, unless the caller explicitly passes `--min-bytes` for a smaller demonstration (as the tests
and this file's own verify step do while the sibling corners are still being built).

THE CORPUS THIS SCRIPT GENERATES IS PART OF THE MEASURING INSTRUMENT, NOT A CONVENIENCE FILE. A
consumer that reads `A770B_CORPUS_FILE` (harness/ctx_sweep.sh, and any script measuring against a
fixed point on the context curve) must either find that file already generated, or generate it itself
by running exactly `python3 kit/corpus.py generate --out <A770B_CORPUS_FILE>` (harness/guard.sh's
`a770b_ensure_corpus_file` is the one place that rule is implemented) and say so — NEVER fall back to
some other corpus (the seat's own source files, say) when the generated file is missing. Two runs on
two machines that silently measured two different prompts at "the 8k point" would not be comparable,
which defeats the entire purpose of a deterministic, byte-identical corpus.

`index <file>` reads a file this script produced and prints `<line>: <name>` for every TOP-LEVEL
`def`, `async def` and `class` FOUND IN THE PYTHON SEGMENTS ONLY (the segments whose marker path ends
in `.py`) — the corpus now also carries C++, JavaScript, shell, JSON, Markdown and other real files,
which are not Python and are not indexed; a global line number is computed for each segment by parsing
just that segment's own text with `ast.parse` and offsetting by where the marker put it in the whole
file. `check` builds the corpus twice in memory, compares sha256, and reports the size in bytes and
the byte/4 token estimate; it exits 2 (with the two hashes, or the byte count against the floor) on
any mismatch.

Usage:
    python3 kit/corpus.py generate [--out kit/corpus/large.py] [--min-bytes 655360]
    python3 kit/corpus.py index <file>
    python3 kit/corpus.py check [--min-bytes 655360]
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "kit" / "corpus" / "large.py"
DEFAULT_MIN_BYTES = 655360  # 640 KiB; four bytes a token (the harness's convention) is ~163,840 tokens

# extension filter used for the unqualified directory buckets (skills/, tests/); kit/seat and
# kit/reference are walked with NO extension filter — "every file" per the design.
TEXT_EXTS = {".py", ".sh", ".js", ".md"}
SKIP_DIR_NAMES = {"__pycache__", ".git", "node_modules", ".pytest_cache", "build", ".cache"}
_ASCII_OK = re.compile(rb"[^\t\n\r\x20-\x7e]")
_MARKER_RE = re.compile(r"^### FILE (.+)$")


def _skip(rel_parts: tuple[str, ...]) -> bool:
    return any(p in SKIP_DIR_NAMES for p in rel_parts)


def _walk_all(base: Path, ext_filter: bool) -> list[Path]:
    """Every file under base, sorted by relative POSIX path; noise directories skipped."""
    if not base.is_dir():
        return []
    found = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if _skip(rel.parts[:-1]):
            continue
        if ext_filter and p.suffix not in TEXT_EXTS:
            continue
        found.append(p)
    return sorted(found, key=lambda p: p.relative_to(base).as_posix())


def _glob_top(base: Path, pattern: str) -> list[Path]:
    """Non-recursive glob, sorted by name."""
    if not base.is_dir():
        return []
    return sorted(base.glob(pattern), key=lambda p: p.name)


def collect_source_files(root: Path) -> list[Path]:
    """The fixed, ordered list of source files the corpus concatenates. Each bucket is sorted
    internally; the buckets themselves run in this fixed order (not merged and re-sorted)."""
    files: list[Path] = []
    files += _walk_all(root / "kit" / "seat", ext_filter=False)
    files += _walk_all(root / "kit" / "reference", ext_filter=False)
    files += _glob_top(root / "harness", "*.sh")
    files += _glob_top(root / "harness", "*.py")
    files += _walk_all(root / "skills", ext_filter=True)
    files += _walk_all(root / "tests", ext_filter=True)
    files += _glob_top(root / "docs", "*.md")
    seen: set[Path] = set()
    ordered: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            ordered.append(f)
    return ordered


def build_corpus(root: Path) -> bytes:
    """The concatenated corpus: `### FILE <relpath>` then the file's own (ASCII-sanitised) bytes,
    for every file `collect_source_files` names, in that order. Deterministic: no timestamps, no
    set ordering, every walk sorted by relative path before use."""
    parts: list[bytes] = []
    for f in collect_source_files(root):
        rel = f.relative_to(root).as_posix()
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            continue  # not text: not "real source" for a prompt, skipped rather than mangled
        data = _ASCII_OK.sub(b"", raw)
        if not data.endswith(b"\n"):
            data += b"\n"
        parts.append(f"### FILE {rel}\n".encode("ascii"))
        parts.append(data)
    return b"".join(parts)


def cmd_generate(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kit/corpus.py generate")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES)
    args = ap.parse_args(argv)
    data = build_corpus(REPO_ROOT)
    if len(data) < args.min_bytes:
        print(
            f"kit/corpus.py generate: real source gives {len(data)} bytes, need at least "
            f"{args.min_bytes} — add material under kit/seat/ or kit/reference/ (not a lower floor)",
            file=sys.stderr,
        )
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"wrote {out} — {len(data)} bytes (~{len(data) // 4} tokens at 4 bytes/token)")
    return 0


def cmd_check(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kit/corpus.py check")
    ap.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES)
    args = ap.parse_args(argv)
    d1 = build_corpus(REPO_ROOT)
    d2 = build_corpus(REPO_ROOT)
    h1, h2 = hashlib.sha256(d1).hexdigest(), hashlib.sha256(d2).hexdigest()
    if h1 != h2:
        print(f"check: FAIL — two generations differ (sha256 {h1} vs {h2})", file=sys.stderr)
        return 2
    if len(d1) < args.min_bytes:
        print(
            f"check: FAIL — {len(d1)} bytes (sha256 {h1}), need at least {args.min_bytes}",
            file=sys.stderr,
        )
        return 2
    print(f"check: OK — sha256 {h1} — {len(d1)} bytes (~{len(d1) // 4} tokens at 4 bytes/token)")
    return 0


def cmd_index(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kit/corpus.py index",
        description="prints <line>: <name> for top-level def/async def/class in the .py segments only",
    )
    ap.add_argument("path")
    args = ap.parse_args(argv)
    text = Path(args.path).read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    markers = [(i, m.group(1)) for i, line in enumerate(lines) if (m := _MARKER_RE.match(line))]
    for idx, (marker_i, relpath) in enumerate(markers):
        if not relpath.endswith(".py"):
            continue
        content_start = marker_i + 2  # 1-based line number of the segment's first content line
        end_i = markers[idx + 1][0] if idx + 1 < len(markers) else len(lines)
        segment = "\n".join(lines[content_start - 1 : end_i])
        try:
            tree = ast.parse(segment)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                print(f"{content_start + node.lineno - 1}: {node.name}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("generate", "index", "check"):
        print("usage: kit/corpus.py {generate,index,check} ...", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    return {"generate": cmd_generate, "index": cmd_index, "check": cmd_check}[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
