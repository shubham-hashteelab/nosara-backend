#!/bin/bash
set -e

# Boot script for the Nosara backend pod.
# Assumes install.sh has already been run (postgres, minio, uv, python3.12, venv + deps).
# Starts PostgreSQL, MinIO, runs migrations, and execs uvicorn.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${RUNPOD_VOLUME_PATH:-/workspace}"
PG_DATA="$DATA_DIR/pgdata"
MINIO_DATA="$DATA_DIR/miniodata"
LOG_DIR="$DATA_DIR/logs"
VENV_DIR="$DATA_DIR/venv"

export PATH="$HOME/.local/bin:$PATH"

echo "=== Nosara Backend Startup ==="

# Fail fast if install.sh hasn't been run yet.
for cmd in pg_isready minio uv python3.12; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "ERROR: '$cmd' not found. Run 'bash install.sh' first." >&2
        exit 1
    fi
done
if [ ! -d "$VENV_DIR" ]; then
    echo "ERROR: venv not found at $VENV_DIR. Run 'bash install.sh' first." >&2
    exit 1
fi

# ----------------------------------------
# 1. Data directories
# ----------------------------------------
mkdir -p "$MINIO_DATA" "$LOG_DIR"
chown -R postgres:postgres "$LOG_DIR"

# ----------------------------------------
# 2. PostgreSQL
# ----------------------------------------
echo "Starting PostgreSQL..."

PG_VERSION=$(pg_config --version | grep -oP '\d+' | head -1)
PG_BIN="/usr/lib/postgresql/$PG_VERSION/bin"

# Initialize PostgreSQL data directory if not exists
if [ ! -d "$PG_DATA/base" ]; then
    echo "Initializing PostgreSQL data directory..."
    mkdir -p "$PG_DATA"
    chown -R postgres:postgres "$PG_DATA"
    cd /tmp && sudo -u postgres $PG_BIN/initdb -D "$PG_DATA"

    cat >> "$PG_DATA/postgresql.conf" <<PGCONF
listen_addresses = 'localhost'
port = 5432
PGCONF

    echo "host all all 127.0.0.1/32 md5" >> "$PG_DATA/pg_hba.conf"
fi

chown -R postgres:postgres "$PG_DATA"

# Stop any existing PostgreSQL instance and clean stale pid
cd /tmp
if sudo -u postgres $PG_BIN/pg_ctl status -D "$PG_DATA" > /dev/null 2>&1; then
    echo "Stopping existing PostgreSQL..."
    sudo -u postgres $PG_BIN/pg_ctl stop -D "$PG_DATA" -m fast -w || true
    sleep 1
fi
rm -f "$PG_DATA/postmaster.pid"

sudo -u postgres $PG_BIN/pg_ctl start -D "$PG_DATA" -l "$LOG_DIR/postgresql.log" -w -t 30

echo "PostgreSQL started."

# Create database and user if not exists
cd /tmp
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='nosara'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER nosara WITH PASSWORD 'nosara';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='nosara'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE nosara OWNER nosara;"

echo "PostgreSQL ready."

# ----------------------------------------
# 3. MinIO
# ----------------------------------------
echo "Starting MinIO..."

lsof -ti :9000 2>/dev/null | xargs -r kill -9 2>/dev/null || true
lsof -ti :9001 2>/dev/null | xargs -r kill -9 2>/dev/null || true

export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD=minioadmin
minio server "$MINIO_DATA" --console-address ":9001" --address ":9000" > "$LOG_DIR/minio.log" 2>&1 &

for i in $(seq 1 10); do
    if curl -sf http://localhost:9000/minio/health/live > /dev/null 2>&1; then
        echo "MinIO ready."
        break
    fi
    sleep 1
done

# ----------------------------------------
# 4. Activate venv
# ----------------------------------------
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"

# ----------------------------------------
# 5. Environment variables
# ----------------------------------------
export DATABASE_URL="postgresql+asyncpg://nosara:nosara@localhost:5432/nosara"
export MINIO_ENDPOINT="localhost:9000"
export MINIO_ACCESS_KEY="minioadmin"
export MINIO_SECRET_KEY="minioadmin"
export MINIO_BUCKET="nosara-snags"
export MINIO_USE_SSL="false"
export JWT_SECRET="${JWT_SECRET:-nosara-secret-change-in-prod}"
export CORS_ORIGINS='["*"]'

# ----------------------------------------
# 6. Database migrations
# ----------------------------------------
echo "Running database migrations..."
if ! alembic upgrade head 2>&1; then
    echo "Migration failed — dropping and recreating database..."
    cd /tmp
    sudo -u postgres psql -c "DROP DATABASE IF EXISTS nosara;"
    sudo -u postgres psql -c "CREATE DATABASE nosara OWNER nosara;"
    cd "$SCRIPT_DIR"
    echo "Retrying migration..."
    alembic upgrade head
fi

# ----------------------------------------
# 7. FastAPI
# ----------------------------------------
lsof -ti :8000 2>/dev/null | xargs -r kill -9 2>/dev/null || true

echo ""
echo "========================================"
echo "  Nosara Backend running on :8000"
echo "  MinIO Console on :9001"
echo "  PostgreSQL on :5432"
echo "  Logs at $LOG_DIR/"
echo "========================================"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
