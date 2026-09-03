#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Source environment
if [ -f ".env" ]; then
    set -a
    source ".env"
    set +a
fi

# Ensure go2rtc config exists
if [ ! -f "config/go2rtc.yaml" ] && [ -f "config/go2rtc.example.yaml" ]; then
    cp config/go2rtc.example.yaml config/go2rtc.yaml
fi

# Start go2rtc streaming engine as child process
./bin/go2rtc -config config/go2rtc.yaml &
GO2RTC_PID=$!

# Cleanup child on exit
cleanup() {
    echo "Stopping go2rtc (PID: $GO2RTC_PID)..."
    kill "$GO2RTC_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Start FastAPI web server (exec keeps PID 1 of the script)
./venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8555}" --app-dir "$DIR"
