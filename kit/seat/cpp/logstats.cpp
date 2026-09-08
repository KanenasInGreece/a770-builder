// logstats hot-path re-implementation (S3 optimisation stage). Fresh, MIT, this repository.
//
// Re-does the counting half of `python/logstats/stats.py`'s `compute_stats` (the per-level
// counts and the total parsed-line count) over the raw text of a log file, so a caller with a
// large file avoids the Python-level per-line loop. Every line is
// `<ISO timestamp> <LEVEL> <component> <message>` (space-separated, LEVEL one of
// DEBUG/INFO/WARN/ERROR); a line is counted only when its second whitespace-separated field is
// exactly one of those four uppercase words and it has a non-empty third field (the component) —
// the same shape `parse.LOG_LINE_RE` matches.
//
// extern "C" int count_levels(const char* text, size_t len, long* out5):
//   text/len   — the file's bytes (need not be NUL-terminated; only `len` bytes are read).
//   out5       — written with exactly 5 longs, in this fixed order:
//                out5[0] = DEBUG count, out5[1] = INFO count, out5[2] = WARN count,
//                out5[3] = ERROR count, out5[4] = total recognised-line count
//                (== the sum of out5[0..3] when every line's level is one of the four).
//   returns 0 on success, non-zero (and out5 left untouched) on a null argument.

#include <cstddef>
#include <cstring>

extern "C" int count_levels(const char* text, size_t len, long* out5) {
    if (text == nullptr || out5 == nullptr) {
        return 1;
    }
    // TODO(S3): scan `text[0..len)` line by line, classify each line's LEVEL field and
    // increment the matching counter in `out5`; leave `out5[4]` as the total. For now this
    // stub reports "not implemented" (zero counts, non-zero return) so a caller that checks
    // the return code learns the library is unfinished rather than trusting all-zero counts.
    (void)text;
    (void)len;
    std::memset(out5, 0, 5 * sizeof(long));
    return 2;
}
