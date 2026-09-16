#!/bin/sh
# Fake ERv2 module entrypoint, used only to exercise run.py's own mechanics
# (mount handling, env vars, per-run work dir persistence across steps, exit
# code + log_contains assertion checking) without needing real AWS credentials
# or the real elasticache image. See ../scenarios/smoketest_*.yaml.
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
if [ "$COUNT" -lt 2 ]; then
  echo "still in progress"
  exit 1
fi
echo "No changes. Your infrastructure matches the configuration."
exit 0
