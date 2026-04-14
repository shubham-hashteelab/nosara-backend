# CLAUDE.md — nosara-backend

## System Context

Nosara is a 3-repo snagging inspection system for real estate handover workflows:

| Repo | Stack | GitHub |
|---|---|---|
| **nosara** (Android app) | Kotlin, Jetpack Compose, Room | `hashtee-engineering/nosara` |
| **nosara-backend** (this repo) | FastAPI, SQLAlchemy, PostgreSQL | `shubham-hashteelab/nosara-backend` |
| **nosara-portal** (web portal) | React, Vite, TypeScript | `shubham-hashteelab/nosara-portal` |

Architecture plan: `/Users/hashteelab/Desktop/Work/Nosara/architecture-plan.md`

Both the portal and the Android app call this backend. The backend URL is dynamic — RunPod assigns a new proxy URL each pod restart.

## Tech Stack

- **Framework:** FastAPI
- **ORM:** SQLAlchemy (async)
- **Database:** PostgreSQL
- **Object Storage:** MinIO (S3-compatible)
- **Auth:** JWT + bcrypt
- **Migrations:** Alembic
- **Package Manager:** `uv` (deps pinned in `requirements.txt`)

## Important Conventions

- **All entity IDs are UUIDs** — use String columns, not PostgreSQL-native UUID type.
- **No PostgreSQL Enum types** — they cause Alembic migration issues. Use String columns with application-level validation instead.
- CORS allows `localhost:5173` (local dev) and the Vercel deployment domain.

## Key Endpoints

- `/api/v1/health` — health check
- `/auth/login` — JWT login
- CRUD for all entities (projects, buildings, floors, flats, inspections, checklist templates, users, contractors)
- `/sync/pull`, `/sync/push` — offline-first sync for the Android app
- `/ai/describe-snag` — AI-powered snag description from photo

## Default Credentials

Manager account created on first startup: `admin` / `admin123`

## Deployment (RunPod)

Runs on a RunPod CPU pod (4 vCPUs / 16 GB RAM) with Network Volume.

```bash
bash start.sh   # Boots PostgreSQL + MinIO + backend
```

The proxy URL is dynamic — changes on each pod restart. Clients (portal and Android app) store the URL at runtime.

## Local Development

```bash
docker-compose up -d          # Start PostgreSQL + MinIO
alembic upgrade head           # Run migrations
uvicorn app.main:app --reload  # Start dev server
```

## Build

```bash
uv pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
