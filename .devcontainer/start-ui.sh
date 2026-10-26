#!/usr/bin/env bash
# Start the ApplyPilot web UI in the background every time the container starts.
# Log: /tmp/applypilot-ui.log. Restart by hand with: bash .devcontainer/start-ui.sh
PIDFILE=/tmp/applypilot-ui.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  OLD=$(cat "$PIDFILE")
  kill "$OLD"
  for _ in $(seq 20); do kill -0 "$OLD" 2>/dev/null || break; sleep 0.5; done
fi
setsid nohup python -m applypilot ui --port 8000 > /tmp/applypilot-ui.log 2>&1 < /dev/null &
echo $! > "$PIDFILE"
echo "ApplyPilot UI starting on http://localhost:8000 (log: /tmp/applypilot-ui.log)"
