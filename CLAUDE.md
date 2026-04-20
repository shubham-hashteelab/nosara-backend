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
- **"Business Associate" = Contractor (portal UI label only).** As of 2026-04-20 the portal renders this entity as "Business Associate" in user-visible text. Backend code, tables (`contractors`, `snag_contractor_assignments`), columns (`contractor_id`), models (`Contractor`, `SnagContractorAssignment`), schemas, endpoints (`/api/v1/contractors`, `/api/v1/entries/{id}/assign-contractor/{contractor_id}`), and sync protocol keys all still use `contractor`. Do NOT rename any of this — it's a pure portal UI rename, coordinated rename would need Alembic migration + Android Room migration + portal type updates shipped together.

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
    └── inspection_service.py  # recompute_flat_inspection_status, initialize_flat_checklist, backfill_uninitialized_flats
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
  - `POST /entries/{flatId}/initialize-checklist` — legacy idempotent fallback. Flats now auto-initialize on creation, so this endpoint is rarely called. Returns existing entries if already initialized, otherwise instantiates from templates.
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
- **`scope_snapshot` on every pull:** `SyncPullResponse.scope_snapshot` carries the complete set of IDs the user is currently entitled to — `{project_ids, building_ids, floor_ids, flat_ids}`. Serialized from the same `_resolve_scope()` output (no extra queries). The Android client diffs this against its local rows to prune anything that fell out of scope (e.g., a revoked assignment) without needing a full reset. This is how mid-session revocation propagates to the app on the next pull.
- **Push:** Individual mutations from sync queue. `data` dict applied via `setattr` (skips `id` key). For `inspection_entry` CREATEs, `inspector_id` is auto-set. **CREATEs are idempotent** — if a record with the same ID already exists, it's accepted without inserting a duplicate.
- **File upload:** `POST /sync/upload-file` accepts multipart with `file`, `type` (snag_image/voice_note/inspection_video), `inspection_entry_id`, `client_id`. Uploads to MinIO AND creates the DB record (SnagImage/VoiceNote/InspectionVideo) in one request. Returns `{ minio_key, size }`.
- Computed response fields: `ProjectResponse` includes `total_buildings`, `total_flats`; `BuildingResponse` includes `total_floors`, `total_flats`; `FloorResponse` includes `total_flats`, `label`.

## Checklist Auto-Initialization

Inspection entries are created automatically from checklist templates whenever a flat is created — managers no longer run a manual "Initialize Checklist" step.

- **On flat create** (`POST /floors/{id}/flats`) and **in seed-hierarchy**: `initialize_flat_checklist(flat.id, db)` runs after the flat row is flushed. It reads the flat's `flat_type`, finds the matching `FlatTypeRoom` rows, and for each room creates one `InspectionEntry` per active `ChecklistTemplate` matching that `room_type`. All entries start with `status="NA"` and `inspector_id=None` — the inspector id is set later when the actual inspector updates the entry via sync.
- **On startup** (`main.py` lifespan): `backfill_uninitialized_flats(db)` finds every flat with zero entries and initializes it. Idempotent and safe to run every boot. Covers flats created before this feature shipped and flats whose flat_type had no rooms/templates at create time but does now. **Serialized across uvicorn workers** via a PG session-level advisory lock (`pg_try_advisory_lock`); without it, `--workers 2` would race and double-init every flat.
- **Unique index** `ix_inspection_entries_unique_item` on `(flat_id, room_label, category, item_name)` — enforces content-level uniqueness regardless of which path created the entry. `sync_service.process_push` does a content check before insert so Android CREATEs that would violate the index are silently accepted as idempotent rather than aborting the push transaction.
- **Legacy endpoint** (`POST /entries/{flatId}/initialize-checklist`) still exists but is now idempotent — it returns existing entries rather than 409'ing. The cherry-pick `template_ids` body is gone (portal no longer sends it). The companion `GET /flats/{id}/checklist-preview` endpoint was removed.
- `inspection_service.initialize_flat_checklist` is a no-op if the flat already has any entry — callers don't need to guard.

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
