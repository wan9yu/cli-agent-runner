#!/usr/bin/env bash
# Restart-on-exit wrapper for agent-runner.
#
# On success (exit 0): immediate restart after $RESTART_DELAY seconds.
# On failure: exponential backoff up to $MAX_DELAY seconds.
# Anthropic-API-outage defense: avoids burning quota in tight loop.

set -u
RESTART_DELAY="${RESTART_DELAY:-3}"
MAX_DELAY="${MAX_DELAY:-60}"
delay="$RESTART_DELAY"

while true; do
  if agent-runner round; then
    delay="$RESTART_DELAY"
    sleep "$delay"
  else
    echo "agent-runner exit nonzero — backoff ${delay}s" >&2
    sleep "$delay"
    next=$(( delay * 2 ))
    if [ "$next" -gt "$MAX_DELAY" ]; then
      delay="$MAX_DELAY"
    else
      delay="$next"
    fi
  fi
done
