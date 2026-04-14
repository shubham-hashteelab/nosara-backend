#!/bin/bash
set -e

echo "=== Nosara Backend Startup ==="

# ----------------------------------------
# 1. Install system packages if not present
# ----------------------------------------
if ! command -v pg_isready &> /dev/null; then
    echo "Installing PostgreSQL..."
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        postgresql postgresql-client curl wget sudo \
        > /dev/null 2>&1
    rm -rf /var/lib/apt/lists/*
fi

# Install MinIO server if not present
if ! command -v minio &> /dev/null; then
    echo "Downloading MinIO server..."
    wget -q https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio
    chmod +x /usr/local/bin/minio
fi

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# ----------------------------------------
# 2. Setup data directories
# ----------------------------------------
DATA_DIR="${RUNPOD_VOLUME_PATH:-/workspace}"
PG_DATA="$DATA_DIR/pgdata"
MINIO_DATA="$DATA_DIR/miniodata"
LOG_DIR="$DATA_DIR/logs"

mkdir -p "$MINIO_DATA" "$LOG_DIR"

# Give postgres user ownership of its directories
chown -R postgres:postgres "$LOG_DIR"

# ----------------------------------------
# 3. Start PostgreSQL
# ----------------------------------------
echo "Starting PostgreSQL..."

PG_VERSION=$(pg_config --version | grep -oP '\d+' | head -1)
PG_BIN="/usr/lib/postgresql/$PG_VERSION/bin"

# Initialize PostgreSQL data directory if not exists
if [ ! -d "$PG_DATA/base" ]; then
    echo "Initializing PostgreSQL data directory..."
    mkdir -p "$PG_DATA"
    chown -R postgres:postgres "$PG_DATA"
    sudo -u postgres $PG_BIN/initdb -D "$PG_DATA"

    # Configure PostgreSQL
    cat >> "$PG_DATA/postgresql.conf" <<PGCONF
listen_addresses = 'localhost'
port = 5432
PGCONF

    # Allow password auth for local TCP connections
    echo "host all all 127.0.0.1/32 md5" >> "$PG_DATA/pg_hba.conf"
fi

# Ensure correct ownership (may have been created by root on first run)
chown -R postgres:postgres "$PG_DATA"

# Start PostgreSQL as postgres user
sudo -u postgres $PG_BIN/pg_ctl start -D "$PG_DATA" -l "$LOG_DIR/postgresql.log" -w -t 30

echo "PostgreSQL started."

# Create database and user if not exists
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='nosara'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER nosara WITH PASSWORD 'nosara';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='nosara'" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE nosara OWNER nosara;"

echo "PostgreSQL ready."

# ----------------------------------------
# 4. Start MinIO (background)
# ----------------------------------------
echo "Starting MinIO..."
export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD=minioadmin
minio server "$MINIO_DATA" --console-address ":9001" --address ":9000" > "$LOG_DIR/minio.log" 2>&1 &

# Wait for MinIO to be ready
for i in $(seq 1 10); do
    if curl -sf http://localhost:9000/minio/health/live > /dev/null 2>&1; then
        echo "MinIO ready."
        break
    fi
    sleep 1
done

# ----------------------------------------
# 5. Install Python dependencies
# ----------------------------------------
echo "Setting up Python dependencies..."

# Figure out where the app code is
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Install with uv (fast), fallback to pip
if command -v uv &> /dev/null; then
    uv pip install --system --no-cache -r requirements.txt
else
    pip install --no-cache-dir -r requirements.txt
fi

# ----------------------------------------
# 6. Set environment variables
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
# 7. Run database migrations
# ----------------------------------------
echo "Running database migrations..."
alembic upgrade head || echo "WARNING: Alembic migration failed — check alembic config"

# ----------------------------------------
# 8. Start FastAPI
# ----------------------------------------
echo ""
echo "========================================"
echo "  Nosara Backend running on :8000"
echo "  MinIO Console on :9001"
echo "  PostgreSQL on :5432"
echo "  Logs at $LOG_DIR/"
echo "========================================"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
