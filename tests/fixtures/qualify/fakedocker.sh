#!/usr/bin/env bash
# Stub for `docker logs`. Prints the next cumulative log and moves the clock.
# It ignores --since so the scanner's own filter is what the test exercises.
d="${FAKE_DIR:?}"
n=$(( $(cat "$d/poll" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$d/poll"
echo $(( $(cat "$d/clock0") + n * ${FAKE_STEP:-60} )) > "$d/now"
f="$d/log.$n"
if [ ! -f "$f" ]; then
  f=$(ls "$d"/log.* | sort -t. -k2 -n | tail -1)
fi
cat "$f"
