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
- **Alembic** — migrations (001: initial schema, 002: building/flat assignments)
- **PostgreSQL 16** — primary DB
- **MinIO** — S3-compatible object storage for media
- **JWT + bcrypt** — auth (7-day token expiry, no refresh in V1)
- **uv** — package manager (deps pinned in `requirements.txt`)

## Critical Conventions

- **IDs are PostgreSQL-native UUIDs** (`UUID(as_uuid=True)`) — portal and app treat them as strings.
- **No PostgreSQL Enum types** — use String columns + app-level validation. Enums cause Alembic migration issues.
- **CORS origins:** `localhost:5173` (local dev) + Vercel deployment domain.
- **Backend URL is dynamic** — RunPod assigns a new proxy URL each pod restart. Clients store URL at runtime.
- **Default creds:** `admin` / `admin123` (created on first startup).
- **Sync push `data` dict must never contain `id`** — the `process_push` UPDATE loop skips `id` to prevent PK overwrite crashes.

## Project Structure

```
app/
├── main.py              # FastAPI app, lifespan, CORS
├── config.py            # pydantic-settings (env vars)
├── database.py          # async engine + session
├── models/              # SQLAlchemy ORM models
│   └── user.py          # User, UserProjectAssignment, UserBuildingAssignment, UserFlatAssignment
├── schemas/             # Pydantic request/response
├── api/                 # Route handlers
│   ├── deps.py          # get_db, get_current_user, require_manager
│   ├── users.py         # User CRUD + project/building/flat assignment endpoints
│   ├── checklists.py    # Template CRUD + seed-defaults + seed-hierarchy
│   └── sync.py          # Pull/push sync, accepts ISO8601 or epoch timestamps
└── services/            # Business logic (auth, minio, ai, sync, reports)
    └── sync_service.py  # Scope resolver for granular access, push/pull processing
alembic/                 # DB migrations
```

## Key API Areas (all under `/api/v1/`)

- **Health:** `GET /health` (unauthenticated — used by clients to test connection)
- **Auth:** `POST /auth/login` — returns `{ access_token, user: { id, username, full_name, role } }`
- **Hierarchy CRUD:** nested URL pattern:
  - `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}`
  - `GET/POST /projects/{id}/buildings`, `GET/PATCH/DELETE /buildings/{id}`
  - `GET/POST /buildings/{id}/floors`, `GET/PATCH/DELETE /floors/{id}`
  - `GET/POST /floors/{id}/flats`, `GET/PATCH/DELETE /flats/{id}`
- **Inspections:**
  - `GET /flats/{id}/entries`, `GET/PATCH /entries/{id}`
  - `POST /entries/{flatId}/initialize-checklist` — creates entries from templates
- **Media:** `POST /files/upload`, `GET /files/{key}` (proxied to/from MinIO)
- **AI:** `POST /ai/describe-snag` (proxied to vLLM)
- **Sync:** `POST /sync/pull` (accepts ISO8601 or epoch), `POST /sync/push`
- **Users:** CRUD + granular assignment:
  - `POST/DELETE /users/{id}/assign-project/{projectId}`
  - `POST/DELETE /users/{id}/assign-building/{buildingId}`
  - `POST/DELETE /users/{id}/assign-flat/{flatId}`
  - UserResponse includes `assigned_project_ids`, `assigned_building_ids`, `assigned_flat_ids`
- **Seed:** `POST /seed-hierarchy` (5 Godrej projects + towers/floors/flats), `POST /checklist-templates/seed-defaults` (templates + rooms + floor plans). Both reject if already seeded.
- **Checklists / Contractors / Dashboard:** Manager CRUD + analytics

## Granular Access Control

Three levels of assignment, resolved by `sync_service._resolve_scope()`:

| Level | What inspector sees |
|---|---|
| **Project** | All towers, floors, flats in that project |
| **Building** | Only assigned towers + their floors/flats; parent project included for navigation |
| **Flat** | Only assigned flats; parent floor/building/project included for navigation |

The scope resolver unions all three levels. Building-only assignments auto-include the parent project. Flat-only assignments auto-include parent floor/building/project.

## Sync Design (Android ↔ Backend)

- **Pull:** `_resolve_scope()` computes accessible project/building/floor/flat IDs from all assignment levels. Returns only data within scope, filtered by `updated_at >= last_synced_at`. Always returns all `flat_type_rooms` and `floor_plan_layouts` (global, not time-filtered).
- **Push:** Individual mutations from sync queue. `data` dict applied via `setattr` (skips `id` key). For `inspection_entry` CREATEs, `inspector_id` is auto-set.
- Files uploaded one at a time through `POST /sync/upload-file`.
- Computed response fields: `ProjectResponse` includes `total_buildings`, `total_flats`; `BuildingResponse` includes `total_floors`, `total_flats`; `FloorResponse` includes `total_flats`, `label`.

## RunPod Deployment

```bash
# SSH into pod
ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519

# Update and restart
cd /root/nosara-backend && git pull
alembic upgrade head          # Run any new migrations
pkill -f uvicorn
nohup /workspace/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 > /workspace/logs/uvicorn.log 2>&1 &

# Check logs
tail -f /workspace/logs/uvicorn.log
```

## Local Dev Commands

```bash
docker-compose up -d          # PostgreSQL + MinIO
alembic upgrade head           # Run migrations
uvicorn app.main:app --reload  # Dev server on :8000
uv pip install -r requirements.txt  # Install deps
```
