#!/bin/bash
set -e

# One-time installer for the Nosara backend pod.
# Installs system packages, MinIO, uv, Python 3.12, and the Python venv/deps.
# Run once per pod (or after bumping requirements.txt). For boot, use start.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${RUNPOD_VOLUME_PATH:-/workspace}"
VENV_DIR="$DATA_DIR/venv"

echo "=== Nosara Backend Install ==="

# Some RunPod pods block outbound HTTP (port 80) but allow HTTPS (443).
# Default Ubuntu apt sources use http://, so apt fails with connection timeouts.
# Detect once and rewrite sources to https:// if HTTP is unreachable.
ensure_apt_reachable() {
    if curl -sf --connect-timeout 5 -o /dev/null http://archive.ubuntu.com/; then
        return 0
    fi
    echo "HTTP to archive.ubuntu.com blocked; switching apt sources to HTTPS."
    sed -i 's|http://archive.ubuntu.com|https://archive.ubuntu.com|g' /etc/apt/sources.list
    sed -i 's|http://security.ubuntu.com|https://security.ubuntu.com|g' /etc/apt/sources.list
    if ls /etc/apt/sources.list.d/*.list >/dev/null 2>&1; then
        sed -i 's|http://ppa.launchpad.net|https://ppa.launchpadcontent.net|g' /etc/apt/sources.list.d/*.list
    fi
}

# ----------------------------------------
# 1. System packages
# ----------------------------------------
if ! command -v pg_isready &> /dev/null; then
    echo "Installing PostgreSQL and system deps..."
    ensure_apt_reachable
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        postgresql postgresql-client curl wget sudo \
        software-properties-common \
        > /dev/null 2>&1
    rm -rf /var/lib/apt/lists/*
fi

# MinIO server
if ! command -v minio &> /dev/null; then
    echo "Downloading MinIO server..."
    wget -q https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio
    chmod +x /usr/local/bin/minio
fi

# uv (Python package manager)
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# ----------------------------------------
# 2. Python 3.12
# ----------------------------------------
if ! python3.12 --version &> /dev/null; then
    echo "Installing Python 3.12..."
    add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
    ensure_apt_reachable
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev \
        > /dev/null 2>&1
    rm -rf /var/lib/apt/lists/*
fi

# ----------------------------------------
# 3. Python venv + dependencies
# ----------------------------------------
cd "$SCRIPT_DIR"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python 3.12 virtual environment at $VENV_DIR..."
    uv venv --python python3.12 "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
echo "Installing Python dependencies..."
uv pip install --no-cache -r requirements.txt

echo ""
echo "========================================"
echo "  Install complete. Run: bash start.sh"
echo "========================================"
