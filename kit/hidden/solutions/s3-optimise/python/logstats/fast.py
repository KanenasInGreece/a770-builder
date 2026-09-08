"""The ctypes wrapper for the C++ counting hot path (S3 optimisation stage). MIT, this repository.

Loads `../cpp/liblogstats.so` (built by `../cpp/Makefile`, `count_levels`) and exposes
`fast_level_counts`, which returns the same shape of result as taking `by_level` and
`total_lines` out of `stats.compute_stats`'s return value — nothing else `compute_stats`
returns (`by_component`, `busiest_minute`, `mean_message_length`) is in this library's scope.
When the library is missing or fails to load, falls back to a pure-Python count over the same
rule (`parse.LOG_LINE_RE`) so a caller never hard-fails for lack of a build.
"""

import ctypes
import re
from pathlib import Path

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
_LIB_PATH = Path(__file__).resolve().parent.parent / "cpp" / "liblogstats.so"
_LINE_RE = re.compile(r"^(\S+) ([A-Z]+) (\S+) (.*)$")


def _load_library():
    try:
        lib = ctypes.CDLL(str(_LIB_PATH))
    except OSError:
        return None
    lib.count_levels.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_long * 5)]
    lib.count_levels.restype = ctypes.c_int
    return lib


_LIB = _load_library()


def _fallback_level_counts(text: str) -> dict:
    counts = {level: 0 for level in LEVELS}
    total = 0
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        level = m.group(2)
        if level in counts:
            counts[level] += 1
            total += 1
    return {"by_level": counts, "total_lines": total}


def fast_level_counts(text: str) -> dict:
    """Return {"by_level": {"DEBUG": n, "INFO": n, "WARN": n, "ERROR": n}, "total_lines": n}
    for the given log text, using the compiled library when it is present and loads
    successfully, and the pure-Python fallback otherwise."""
    if _LIB is not None:
        data = text.encode("utf-8")
        out5 = (ctypes.c_long * 5)()
        rc = _LIB.count_levels(data, len(data), ctypes.byref(out5))
        if rc == 0:
            counts = {level: out5[i] for i, level in enumerate(LEVELS)}
            return {"by_level": counts, "total_lines": out5[4]}
    return _fallback_level_counts(text)
