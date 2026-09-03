#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=== Tapo Studio Setup ==="

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required."
    exit 1
fi

# 2. Virtual Environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing dependencies..."
./venv/bin/pip install -r requirements.txt

# 3. Download go2rtc if missing
mkdir -p bin config snapshots
if [ ! -f "bin/go2rtc" ]; then
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64) GO_ARCH="amd64" ;;
        aarch64|arm64) GO_ARCH="arm64" ;;
        armv7l) GO_ARCH="arm" ;;
        *) echo "Unknown architecture: $ARCH"; exit 1 ;;
    esac
    echo "Downloading go2rtc for $GO_ARCH..."
    curl -sL -o bin/go2rtc "https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_${GO_ARCH}"
    chmod +x bin/go2rtc
fi

# 4. Environment config
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created default .env file. Please edit it with your camera IP and credentials."
fi

# 5. go2rtc config
if [ ! -f "config/go2rtc.yaml" ] && [ -f "config/go2rtc.example.yaml" ]; then
    cp config/go2rtc.example.yaml config/go2rtc.yaml
fi

echo "Setup complete! Start services with ./start.sh or PM2."
