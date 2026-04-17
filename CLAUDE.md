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
    ├── sync_service.py  # Scope resolver for granular access, push/pull processing
    └── inspection_service.py  # recompute_flat_inspection_status (entry-count-based status)
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
  - `GET /flats/{id}/checklist-preview` — returns templates grouped by room for cherry-pick UI
  - `POST /entries/{flatId}/initialize-checklist` — creates entries from templates. Accepts optional `{ template_ids: [...] }` body for cherry-picking. Returns 409 if entries already exist (idempotent guard).
- **Media:** `POST /files/upload`, `GET /files/{key}?token=JWT` (proxied to/from MinIO, auth via query param for img/audio/video tags)
- **AI:** `POST /ai/describe-snag` (proxied to vLLM)
- **Sync:** `POST /sync/pull` (accepts ISO8601 or epoch), `POST /sync/push`, `POST /sync/upload-file`
- **Users:** CRUD + granular assignment:
  - `POST/DELETE /users/{id}/assign-project/{projectId}`
  - `POST/DELETE /users/{id}/assign-building/{buildingId}`
  - `POST/DELETE /users/{id}/assign-flat/{flatId}`
  - UserResponse includes `assigned_project_ids`, `assigned_building_ids`, `assigned_flat_ids`
- **Seed:** `POST /seed-hierarchy` (5 Godrej projects + towers/floors/flats), `POST /checklist-templates/seed-defaults` (templates + rooms + floor plans). Both reject if already seeded.
- **Dashboard:**
  - `GET /dashboard/projects/{id}/stats` — project-wide flat-status counts (from `Flat.inspection_status`) + snag severity/category breakdowns.
  - `GET /dashboard/projects/{id}/building-stats` — flat per-tower list (legacy; simpler shape, no floor nesting).
  - `GET /dashboard/projects/{id}/tower-stats` — per-tower rollup with nested per-floor progress + per-tower snag severity breakdown. Three separate SQL queries (towers, per-floor flat counts, per-tower snag counts) stitched in Python to avoid row multiplication from joining flats and entries together. Buildings with 0 floors still surface with empty `floors`.
  - `GET /dashboard/projects-overview` — cross-project rollup: every project with its tower-level (no floor) progress in one response. Used by the portal's Projects list page for tower mini-cards.
  - `GET /dashboard/projects/{id}/inspector-activity?days=N` — day-bucketed per-inspector counts.
  - Route paths use plural (`/projects/`, `/buildings/`).
- **Checklists / Contractors:** Manager CRUD

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
- **Push:** Individual mutations from sync queue. `data` dict applied via `setattr` (skips `id` key). For `inspection_entry` CREATEs, `inspector_id` is auto-set. **CREATEs are idempotent** — if a record with the same ID already exists, it's accepted without inserting a duplicate.
- **File upload:** `POST /sync/upload-file` accepts multipart with `file`, `type` (snag_image/voice_note/inspection_video), `inspection_entry_id`, `client_id`. Uploads to MinIO AND creates the DB record (SnagImage/VoiceNote/InspectionVideo) in one request. Returns `{ minio_key, size }`.
- Computed response fields: `ProjectResponse` includes `total_buildings`, `total_flats`; `BuildingResponse` includes `total_floors`, `total_flats`; `FloorResponse` includes `total_flats`, `label`.

## Flat Inspection Status

`Flat.inspection_status` is a stored column recomputed from entry counts by `recompute_flat_inspection_status()` in `app/services/inspection_service.py`:

| Status | Rule |
|---|---|
| `NOT_STARTED` | No entries exist, or all entries have `status = 'NA'` |
| `IN_PROGRESS` | At least one entry has `status != 'NA'`, but not all |
| `COMPLETED` | All entries have `status != 'NA'` (and at least one entry exists) |

The helper is called automatically after every mutation that can change the status:
- `POST /entries/{flatId}/initialize-checklist` (all entries start as NA → NOT_STARTED)
- `PATCH /entries/{id}` when entry status changes
- Sync push when an `inspection_entry` UPDATE includes a `status` field

`PATCH /flats/{id}` still allows managers to manually override `inspection_status` (bypasses recompute). Dashboard reads the stored column directly.

## RunPod Deployment

```bash
# SSH into pod
ssh root@<IP> -p <PORT> -i ~/.ssh/id_ed25519

# Update and restart (use start.sh, not manual uvicorn)
cd /root/nosara-backend && git pull
lsof -ti :8000 | xargs kill -9    # Kill stale processes on port 8000
find . -name '__pycache__' -exec rm -rf {} + 2>/dev/null  # Clear bytecode cache
bash start.sh                       # Handles PostgreSQL, MinIO, migrations, uvicorn

# Check logs
tail -f /workspace/logs/uvicorn.log
```

**Important:** Always use `bash start.sh` to restart — it handles PostgreSQL, MinIO, and uvicorn together. Manual `pkill uvicorn` can leave ghost processes; always check `lsof -i :8000` and kill stale PIDs before restarting. Clear `__pycache__` if code changes don't take effect after restart.

## Local Dev Commands

```bash
docker-compose up -d          # PostgreSQL + MinIO
alembic upgrade head           # Run migrations
uvicorn app.main:app --reload  # Dev server on :8000
uv pip install -r requirements.txt  # Install deps
```
