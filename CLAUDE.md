# CLAUDE.md — nosara-backend

## What Is Nosara

Snagging inspection system for real estate handover. Three repos, one backend:

| Repo | Role | Stack | Hosted |
|---|---|---|---|
| **nosara-backend** (this) | API server | FastAPI, SQLAlchemy (async), PostgreSQL, MinIO | RunPod CPU pod |
| **nosara-portal** | Manager web UI | React 19, Vite, TypeScript, Tailwind | Vercel |
| **nosara** (Android) | Inspector field app | Kotlin, Jetpack Compose, Room | `~/StudioProjects/nosara` |

**Data flow:** Portal and Android app both call this backend. Backend proxies file storage (MinIO) and AI (vLLM) — clients never talk to those directly.

## Tech Stack

- **FastAPI** + **uvicorn** — async, OpenAPI docs at `/docs`
- **SQLAlchemy 2.0 (async)** + **asyncpg** — ORM
- **Alembic** — migrations
- **PostgreSQL 16** — primary DB
- **MinIO** — S3-compatible object storage for media
- **JWT + bcrypt** — auth (7-day token expiry, no refresh in V1)
- **uv** — package manager (deps pinned in `requirements.txt`)

## Critical Conventions

- **All IDs are UUIDs stored as String columns** — not PostgreSQL-native UUID type.
- **No PostgreSQL Enum types** — use String columns + app-level validation. Enums cause Alembic migration issues.
- **CORS origins:** `localhost:5173` (local dev) + Vercel deployment domain.
- **Backend URL is dynamic** — RunPod assigns a new proxy URL each pod restart. Clients store URL at runtime.
- **Default creds:** `admin` / `admin123` (created on first startup).

## Project Structure

```
app/
├── main.py              # FastAPI app, lifespan, CORS
├── config.py            # pydantic-settings (env vars)
├── database.py          # async engine + session
├── models/              # SQLAlchemy ORM models
├── schemas/             # Pydantic request/response
├── api/                 # Route handlers
│   └── deps.py          # get_db, get_current_user, require_manager
└── services/            # Business logic (auth, minio, ai, sync, reports)
alembic/                 # DB migrations
```

## Key API Areas (all under `/api/v1/`)

- **Health:** `GET /health` (unauthenticated — used by clients to test connection)
- **Auth:** `POST /auth/login`
- **Hierarchy CRUD:** projects → buildings → floors → flats (Manager writes, any reads)
- **Inspections:** entries under flats, checklist initialization
- **Media:** `POST /files/upload`, `GET /files/{key}` (proxied to/from MinIO)
- **AI:** `POST /ai/describe-snag` (proxied to vLLM)
- **Sync:** `POST /sync/pull`, `/sync/push` (offline-first sync for Android app)
- **Contractors / Users / Checklists / Dashboard:** Manager CRUD + analytics

## Sync Design (Android ↔ Backend)

- **Pull:** Server → app. Full hierarchy + templates for inspector's assigned projects. Server-wins for hierarchy data.
- **Push:** App → server. Individual mutations from sync queue. Client-wins, last-write-wins by `updatedAt`.
- Files uploaded one at a time through `POST /sync/upload-file`.

## Commands

```bash
# Local dev
docker-compose up -d          # PostgreSQL + MinIO
alembic upgrade head           # Run migrations
uvicorn app.main:app --reload  # Dev server on :8000

# Production (RunPod)
bash start.sh                  # Boots PostgreSQL + MinIO + backend

# Install deps
uv pip install -r requirements.txt
```
