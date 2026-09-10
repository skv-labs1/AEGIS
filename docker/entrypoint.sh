#!/bin/sh
# Start the demo enterprise systems, wait for them, then serve the console.
#
# A process supervisor would be tidier, but four processes with one dependency
# between them does not need one, and a shell script is easier to read when the
# container will not start.
set -e

PORT="${PORT:-8000}"

echo "Seeding the synthetic enterprise..."
python -m aegis_demo.common.seed

echo "Starting the demo enterprise systems..."
python -m aegis_demo.run_all --host 127.0.0.1 &
DEMO_PID=$!

# Stop everything together, so a crashed demo system does not leave a console
# serving an empty queue.
trap 'kill "$DEMO_PID" 2>/dev/null || true' INT TERM EXIT

echo "Waiting for the enterprise systems..."
for _ in $(seq 1 60); do
    if python - <<'PY'
import socket, sys
sys.exit(0 if all(
    socket.socket().connect_ex(("127.0.0.1", port)) == 0 for port in (8801, 8802, 8803)
) else 1)
PY
    then
        echo "Enterprise systems are up."
        break
    fi
    sleep 1
done

echo "Starting Aegis on port ${PORT}..."
exec python -m uvicorn aegis.api.app:app --host 0.0.0.0 --port "${PORT}"
