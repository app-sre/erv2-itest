#!/bin/sh
# Fake ERv2 module for demoing erv2-itest's terminal UI to a team - NOT used by
# `make test` (see ../smoketest/ for that). Adds a configurable SLEEP_SECONDS
# delay per invocation on top of smoketest's converge-after-N-loops pattern, so
# a scenario can take real, visible wall-clock time - enough to show off the
# live progress bar and streamed docker output - without needing real AWS.
if [ -n "$SLEEP_SECONDS" ] && [ "$SLEEP_SECONDS" -gt 0 ]; then
  echo "Working... (sleeping ${SLEEP_SECONDS}s to simulate real module work)"
  sleep "$SLEEP_SECONDS"
fi
echo "ACTION=$ACTION DRY_RUN=$DRY_RUN"
if [ "$ACTION" = "Destroy" ]; then
  echo "Destroy complete! Resources: 0 added, 0 changed, 1 destroyed."
  exit 0
fi
COUNTER_FILE=/work/counter
if [ ! -f "$COUNTER_FILE" ]; then
  echo 0 > "$COUNTER_FILE"
fi
COUNT=$(cat "$COUNTER_FILE")
COUNT=$((COUNT + 1))
echo "$COUNT" > "$COUNTER_FILE"
echo "loop count: $COUNT"
if [ "$COUNT" -lt "${CONVERGE_AFTER:-2}" ]; then
  echo "still in progress"
  exit 1
fi
echo "No changes. Your infrastructure matches the configuration."
exit 0
