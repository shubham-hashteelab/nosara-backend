#!/bin/bash
set -e

echo "=== Nosara Backend Startup ==="

# ----------------------------------------
# 1. Install system packages if not present
# ----------------------------------------
if ! command -v pg_isready &> /dev/null; then
    echo "Installing PostgreSQL and MinIO..."
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        postgresql postgresql-client curl wget \
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
    export PATH="$HOME/.local/bin:$PATH"
fi

# ----------------------------------------
# 2. Setup data directories (use /workspace for RunPod network volume persistence)
# ----------------------------------------
DATA_DIR="${RUNPOD_VOLUME_PATH:-/workspace}"
PG_DATA="$DATA_DIR/pgdata"
MINIO_DATA="$DATA_DIR/miniodata"
APP_DIR="$DATA_DIR/nosara-backend"

mkdir -p "$MINIO_DATA"

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
    chown postgres:postgres "$PG_DATA"
    su - postgres -c "$PG_BIN/initdb -D $PG_DATA"

    # Configure PostgreSQL to listen on localhost
    echo "listen_addresses = 'localhost'" >> "$PG_DATA/postgresql.conf"
    echo "port = 5432" >> "$PG_DATA/postgresql.conf"
fi

# Start PostgreSQL
su - postgres -c "$PG_BIN/pg_ctl start -D $PG_DATA -l $DATA_DIR/pg.log -w"

# Create database and user if not exists
su - postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='nosara'\" | grep -q 1 || psql -c \"CREATE USER nosara WITH PASSWORD 'nosara';\""
su - postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='nosara'\" | grep -q 1 || psql -c \"CREATE DATABASE nosara OWNER nosara;\""

echo "PostgreSQL ready."

# ----------------------------------------
# 4. Start MinIO (background)
# ----------------------------------------
echo "Starting MinIO..."
export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD=minioadmin
minio server "$MINIO_DATA" --console-address ":9001" --address ":9000" > "$DATA_DIR/minio.log" 2>&1 &
sleep 2
echo "MinIO ready."

# ----------------------------------------
# 5. Install Python dependencies and run backend
# ----------------------------------------
echo "Setting up backend..."

# Clone or update code
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    # If running from repo directly, skip git
else
    # Running from /app (Docker) or first time
    APP_DIR="/app"
    cd "$APP_DIR"
fi

# Install dependencies with uv
export PATH="$HOME/.local/bin:$PATH"
uv pip install --system --no-cache -r requirements.txt 2>/dev/null || pip install --no-cache-dir -r requirements.txt

# Set environment variables
export DATABASE_URL="postgresql+asyncpg://nosara:nosara@localhost:5432/nosara"
export MINIO_ENDPOINT="localhost:9000"
export MINIO_ACCESS_KEY="minioadmin"
export MINIO_SECRET_KEY="minioadmin"
export MINIO_BUCKET="nosara-snags"
export MINIO_USE_SSL="false"
export JWT_SECRET="${JWT_SECRET:-nosara-secret-change-in-prod}"
export CORS_ORIGINS='["*"]'

# Run Alembic migrations
echo "Running database migrations..."
alembic upgrade head 2>/dev/null || echo "Alembic migration skipped (may need initial setup)"

# ----------------------------------------
# 6. Start FastAPI server
# ----------------------------------------
echo ""
echo "========================================"
echo "  Nosara Backend is starting on :8000"
echo "  MinIO Console on :9001"
echo "========================================"
echo ""

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
