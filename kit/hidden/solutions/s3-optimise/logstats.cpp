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

namespace {

int level_index(const char* field, size_t flen) {
    switch (flen) {
        case 4:
            if (std::memcmp(field, "INFO", 4) == 0) return 1;
            if (std::memcmp(field, "WARN", 4) == 0) return 2;
            return -1;
        case 5:
            if (std::memcmp(field, "DEBUG", 5) == 0) return 0;
            if (std::memcmp(field, "ERROR", 5) == 0) return 3;
            return -1;
        default:
            return -1;
    }
}

}  // namespace

extern "C" int count_levels(const char* text, size_t len, long* out5) {
    if (text == nullptr || out5 == nullptr) {
        return 1;
    }

    long counts[5] = {0, 0, 0, 0, 0};
    size_t i = 0;
    while (i < len) {
        size_t line_start = i;
        while (i < len && text[i] != '\n') {
            i++;
        }
        size_t line_end = i;  // exclusive
        if (i < len) {
            i++;  // skip the newline itself
        }

        // field 1: the timestamp — skip it, we only need where it ends.
        size_t p = line_start;
        while (p < line_end && text[p] != ' ') p++;
        while (p < line_end && text[p] == ' ') p++;

        // field 2: the level.
        size_t f2_start = p;
        while (p < line_end && text[p] != ' ') p++;
        size_t f2_end = p;
        while (p < line_end && text[p] == ' ') p++;

        // field 3: the component — only its presence matters here.
        size_t f3_start = p;
        while (p < line_end && text[p] != ' ') p++;
        size_t f3_end = p;

        if (f2_end > f2_start && f3_end > f3_start) {
            int idx = level_index(text + f2_start, f2_end - f2_start);
            if (idx >= 0) {
                counts[idx]++;
                counts[4]++;
            }
        }
    }

    std::memcpy(out5, counts, 5 * sizeof(long));
    return 0;
}
