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
- **Inspection entry `status` values: `"PASS"` / `"FAIL"` / `"NA"`** (string column, no PG enum). Dashboard snag aggregates filter on `status == "FAIL"`; a previous revision used `"SNAG"`/`"OK"` which never matched any row — do not resurrect those literals.
- **Three roles: `MANAGER` / `INSPECTOR` / `CONTRACTOR`** (string column on `users`, validated app-side). Contractors are full User rows (`role=CONTRACTOR`) with `company: text` and `trades: text[]` populated — the old standalone `contractors` table was dropped in migration 004. "Business Associate" is the portal's user-visible label for CONTRACTOR users. Only identifier columns / endpoints still say `contractor` (e.g., `snag_contractor_assignments.contractor_id` FK → `users.id`, `/entries/{id}/assign-contractor/{contractor_id}`).
- **Trade taxonomy** — `PLUMBING`, `ELECTRICAL`, `PAINTING`, `CARPENTRY`, `TILING`, `CIVIL`, `HVAC`, `MISC`. Single source of truth: `app/constants/trades.py` (`VALID_TRADES`, `is_valid_trade`). Every `ChecklistTemplate` and `InspectionEntry` carries a `trade`; assignment routes enforce `entry.trade IN contractor.trades`.
- **Snag image kinds** — `SnagImage.kind` is `"NC"` (original defect photo, inspector-uploaded) or `"CLOSURE"` (post-fix photo, contractor-uploaded). `/files/upload` and `/sync/upload-file` require `kind` on image uploads and role-gate it: INSPECTOR→NC, CONTRACTOR→CLOSURE, MANAGER→NC only (managers never upload closure proof — that's the contractor's evidence of work done).
- **Auth deps** — `get_current_user` rejects CONTRACTOR tokens by default, so legacy inspector/manager routes remain contractor-safe without per-route guards. New `get_current_user_allow_all` accepts every active role; used only by routes that dispatch on role internally (`/sync/push`, `/sync/pull`, `/sync/upload-file`, `/files/upload`) or serve contractors via `require_contractor`. Same pattern: `require_manager`, `require_inspector`, `require_contractor`.
- **Inspection fix-flow state machine** — `snag_fix_status`: `OPEN → FIXED` (via `/entries/{id}/mark-fixed` or CONTRACTOR sync push) → `VERIFIED` (via `/entries/{id}/verify`) or `OPEN` (via `/entries/{id}/reject`, clearing `fixed_at`/`fixed_by_id`, setting `rejection_remark`/`rejected_at`). `PATCH /entries/{id}` rejects any `snag_fix_status` change that isn't an idempotent no-op — all transitions must go through the dedicated endpoints so timeline columns stay consistent.

## Project Structure

```
app/
├── main.py              # FastAPI app, lifespan, CORS, router registration order
├── config.py            # pydantic-settings (env vars)
├── database.py          # async engine + session
├── constants/
│   └── trades.py        # VALID_TRADES, VALID_SNAG_IMAGE_KINDS, validators
├── models/              # SQLAlchemy ORM models
│   ├── user.py          # User (role, trades, company, email, phone) + assignment tables
│   └── contractor.py    # SnagContractorAssignment only (Contractor class removed, contractors are now Users)
├── schemas/             # Pydantic request/response
├── api/                 # Route handlers
│   ├── deps.py          # get_db, get_current_user (rejects CONTRACTOR), get_current_user_allow_all, require_manager/inspector/contractor
│   ├── users.py         # User CRUD + project/building/flat assignment endpoints + contractor field validation + orphan-check deactivation
│   ├── inspections.py   # /flats/{id}/entries, /entries/snags, /entries/{id} (PATCH blocks snag_fix_status transitions)
│   ├── contractor_entries.py  # /entries/my-assigned, /entries/{id}/mark-fixed|verify|reject, verification-queue, orphaned-assignments, assign-contractor
│   ├── entry_helpers.py # entry_to_response() — denormalizes contractor_assignments onto InspectionEntryResponse
│   ├── contractors.py   # 410 Gone stubs for the retired /contractors CRUD (contractors are users now)
│   ├── checklists.py    # Template CRUD + seed-defaults + seed-hierarchy (seeds 4 demo contractors)
│   ├── media.py         # /files/upload with kind form field + role-gated NC/CLOSURE validation
│   └── sync.py          # Pull/push sync, accepts ISO8601 or epoch timestamps; passes full User to service
└── services/
    ├── sync_service.py  # Role-branched pull/push; _resolve_scope (inspector/manager), _process_pull_contractor, _apply_contractor_op
    └── inspection_service.py  # recompute_flat_inspection_status, initialize_flat_checklist (propagates trade), backfill_uninitialized_flats
alembic/                 # DB migrations (001 initial, 002 building/flat assignments, 003 dedupe entries, 004 contractor role rollout)
docs/                    # Design docs (contractor-role-rollout.md)
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
  - `GET /entries/snags` — cross-project list of snag entries (`status == "FAIL"`). Query params: `project_id`, `severity`, `category`, `snag_fix_status`, `contractor_id`, `skip`, `limit` (1–500, default 200). Powers the portal's Inspections page. Ordered by `updated_at DESC`. Response entries include `contractor_assignments` eager-loaded.
  - `POST /entries/{flatId}/initialize-checklist` — legacy idempotent fallback. Flats now auto-initialize on creation, so this endpoint is rarely called. Returns existing entries if already initialized, otherwise instantiates from templates.
- **Contractor / verification flow** (`app/api/contractor_entries.py` — registered before `inspections_router` in `main.py` so static segments like `/my-assigned` resolve before `/entries/{entry_id}`):
  - `GET /entries/my-assigned?snag_fix_status=&skip=&limit=` (CONTRACTOR) — snags assigned to the caller, filterable by fix status.
  - `POST /entries/{id}/mark-fixed` (CONTRACTOR) — transition OPEN→FIXED. Validates caller is the assigned contractor, `entry.status == "FAIL"`, and ≥1 `CLOSURE`-kind image exists. Clears `rejection_remark`/`rejected_at` if this is a re-fix after rejection.
  - `POST /entries/{id}/verify` (MANAGER) — transition FIXED→VERIFIED with required `verification_remark`. Idempotent on already-VERIFIED (returns current state without overwriting the original remark).
  - `POST /entries/{id}/reject` (MANAGER) — transition FIXED→OPEN with required `rejection_remark`. Clears `fixed_at`/`fixed_by_id` so the contractor can re-submit.
  - `GET /entries/verification-queue?project_id=` (MANAGER) — FIXED entries awaiting verification, oldest first (FIFO).
  - `GET /entries/orphaned-assignments` (MANAGER) — assignments whose contractor user is deactivated or no longer has role=CONTRACTOR. Powers the portal's reassignment queue.
  - `POST /entries/{id}/assign-contractor/{contractor_id}?force=` (MANAGER) — validates contractor is active CONTRACTOR role and `entry.trade IN contractor.trades`; 409 `EXCLUSIVE_CONFLICT` if another assignment exists, unless `force=true` (deletes the existing row atomically first). Same `contractor_id` re-assignment is idempotent.
  - `DELETE /entries/{id}/assign-contractor/{contractor_id}` (MANAGER) — unassign, 204.
- **Media:** `POST /files/upload` requires `kind` form field (`"NC"` or `"CLOSURE"`) for image uploads, role-gated (INSPECTOR→NC, CONTRACTOR→CLOSURE, MANAGER→NC). Accepts optional `duration_ms` for voice/video. `GET /files/{key}?token=JWT` proxied to/from MinIO, auth via query param for img/audio/video tags.
- **AI:** `POST /ai/describe-snag` (proxied to vLLM)
- **Sync:** `POST /sync/pull` (accepts ISO8601 or epoch), `POST /sync/push`, `POST /sync/upload-file`
- **Users:** CRUD + granular assignment:
  - `POST/DELETE /users/{id}/assign-project/{projectId}`
  - `POST/DELETE /users/{id}/assign-building/{buildingId}`
  - `POST/DELETE /users/{id}/assign-flat/{flatId}`
  - UserResponse includes `assigned_project_ids`, `assigned_building_ids`, `assigned_flat_ids`, plus contractor fields `email`/`phone`/`company`/`trades` (trades + company populated only when role=CONTRACTOR).
  - `POST /users` with `role=CONTRACTOR` requires `trades` as a non-empty list of valid taxonomy values; rejects `trades`/`company` on non-contractor roles. `PATCH /users/{id}` enforces the same contractor-only invariants and cannot leave a CONTRACTOR's `trades` empty.
  - `PATCH /users/{id}` with `is_active=false` on a CONTRACTOR returns 409 `OPEN_ASSIGNMENTS` listing open (non-VERIFIED) snag entries unless `?force=true`. The portal uses the orphan list to drive a reassign-before-deactivate prompt.
- **Seed:** `POST /seed-hierarchy` (5 Godrej projects + towers/floors/flats + 4 demo CONTRACTOR users with password `contractor123` covering PLUMBING / ELECTRICAL / TILING+PAINTING / CIVIL+CARPENTRY), `POST /checklist-templates/seed-defaults` (templates + rooms + floor plans; every template row carries a `trade` that propagates onto entries via `initialize_flat_checklist`). Both reject if already seeded.
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

- **Role branching:** `process_pull` / `process_push` take the full `User` object (`caller`) and dispatch on `caller.role`. CONTRACTOR hits a dedicated path (`_process_pull_contractor`, `_apply_contractor_op`); INSPECTOR / MANAGER hit the original hierarchy-based path.
- **Pull (INSPECTOR/MANAGER):** `_resolve_scope()` computes accessible project/building/floor/flat IDs from all assignment levels. Returns only data within scope, filtered by `updated_at >= last_synced_at`. Always returns all `flat_type_rooms` and `floor_plan_layouts` (global, not time-filtered). `contractors` is now always an empty list (kept in the wire shape for Android client compatibility).
- **Pull (CONTRACTOR):** returns **only** the entries the caller is currently assigned to via `snag_contractor_assignments` + the parent flat/floor/building/project hierarchy for navigation. No sibling entries, no templates, no room/layout definitions — a contractor cannot author checklists. `scope_snapshot` reflects the parent hierarchy so Android's prune-on-scope-diff logic still works on assignment revocation. Full hierarchy is delivered (bypassing `updated_at` filter) when the caller has an assignment granted after `last_synced_at`.
- **Push (CONTRACTOR):** `_apply_contractor_op` restricts CONTRACTOR push to `UPDATE` on `inspection_entry` with **only** `snag_fix_status=FIXED` in `data`. Enforces: caller is the assigned contractor, `entry.status == "FAIL"`, current `snag_fix_status == "OPEN"`, and at least one `CLOSURE` image exists. All other ops (CREATE, DELETE, other entity types, other fields) are rejected. Same integrity guarantees as `POST /entries/{id}/mark-fixed` endpoint.
- **`scope_snapshot` on every pull:** `SyncPullResponse.scope_snapshot` carries the complete set of IDs the user is currently entitled to — `{project_ids, building_ids, floor_ids, flat_ids}`. Serialized from the same `_resolve_scope()` output (no extra queries). The Android client diffs this against its local rows to prune anything that fell out of scope (e.g., a revoked assignment) without needing a full reset. This is how mid-session revocation propagates to the app on the next pull.
- **Push:** Individual mutations from sync queue. `data` dict applied via `setattr` (skips `id` key). For `inspection_entry` CREATEs, `inspector_id` is auto-set. **CREATEs are idempotent** — if a record with the same ID already exists, it's accepted without inserting a duplicate. **Per-op SAVEPOINT** via `db.begin_nested()`: a DB-level failure on one op rolls back only that op, leaving prior-accepted ops and subsequent ops intact. Without this, any flush error would cascade on `db.commit()` and silently discard the entire batch while the client had already marked each op COMPLETED locally.
- **File upload:** `POST /sync/upload-file` accepts multipart with `file`, `type` (snag_image/voice_note/inspection_video), `inspection_entry_id`, `client_id`, and optional `duration_ms` (for voice_note/inspection_video). Uploads to MinIO AND creates the DB record (SnagImage/VoiceNote/InspectionVideo) in one request. Returns `{ minio_key, size }`.
- **Assignment change detection:** `_assignments_changed_since(user_id, last_synced_at)` checks `max(assigned_at)` across UserProject/Building/FlatAssignment. When true, `process_pull` drops the `updated_at >= last_synced_at` filter for Project/Building/Floor/Flat/InspectionEntry so newly-granted scope arrives in full — parent rows with stale `updated_at` would otherwise sit in `scope_snapshot` without any accompanying data and break navigation.
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
