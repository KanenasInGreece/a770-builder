"""The logstats statistics function (S2 backend stage). MIT, this repository."""

from typing import Iterable


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

    TODO: implement. This stub currently raises so a caller learns at once
    that the function is unfinished, rather than silently returning wrong
    numbers.
    """
    raise NotImplementedError("compute_stats is not implemented yet")
