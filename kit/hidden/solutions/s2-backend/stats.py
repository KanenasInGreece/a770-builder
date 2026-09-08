"""The logstats statistics function (S2 backend stage). MIT, this repository."""

from typing import Iterable

from .parse import parse_line


def compute_stats(lines: Iterable[str]) -> dict:
    """Compute log-line statistics. Parses each line with `parse.parse_line`
    (which rejects a line whose component `parse.sanitize_component` refuses);
    a line that fails to parse is skipped and does not count toward any total.

    Returns a dict with exactly these keys (the interface fixed by
    `../../product-brief.md` and restated in `../../design/DESIGN.md`):

    - "total_lines": int — the number of lines that parsed successfully.
    - "by_level": dict[str, int] — count of parsed lines per LEVEL, only the
      levels that occur, keys sorted ascending.
    - "by_component": dict[str, int] — count of parsed lines per sanitized
      component name, only the components that occur, keys sorted ascending.
    - "busiest_minute": str | None — the minute bucket (the timestamp's first
      16 characters, "YYYY-MM-DDTHH:MM") with the most parsed lines; ties
      broken by the lexicographically earliest (= chronologically earliest)
      bucket; None when there are no parsed lines.
    - "mean_message_length": float — the mean of `len(message)` over every
      parsed line, rounded to 2 decimal places; 0.0 when there are no parsed
      lines.
    """
    total = 0
    by_level: dict[str, int] = {}
    by_component: dict[str, int] = {}
    minute_counts: dict[str, int] = {}
    msg_lengths: list[int] = []

    for raw in lines:
        parsed = parse_line(raw)
        if parsed is None:
            continue
        total += 1
        by_level[parsed["level"]] = by_level.get(parsed["level"], 0) + 1
        by_component[parsed["component"]] = by_component.get(parsed["component"], 0) + 1
        minute = parsed["timestamp"][:16]
        minute_counts[minute] = minute_counts.get(minute, 0) + 1
        msg_lengths.append(len(parsed["message"]))

    busiest_minute = None
    if minute_counts:
        best = max(minute_counts.values())
        busiest_minute = min(m for m, c in minute_counts.items() if c == best)

    mean_message_length = round(sum(msg_lengths) / len(msg_lengths), 2) if msg_lengths else 0.0

    return {
        "total_lines": total,
        "by_level": dict(sorted(by_level.items())),
        "by_component": dict(sorted(by_component.items())),
        "busiest_minute": busiest_minute,
        "mean_message_length": mean_message_length,
    }
