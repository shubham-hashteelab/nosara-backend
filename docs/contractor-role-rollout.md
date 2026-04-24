# Contractor Role Rollout

**Status:** planning — not yet implemented.
**Scope:** all three repos (nosara-backend, nosara-portal, nosara Android).
**Origin:** resolves the "Contractor assignment is a write-only black hole" problem tracked in `nosara-portal/problems.md`.

## What we're building

Convert contractors from passive directory entries into full authenticated users who log into the Android app, see the snags assigned to them, upload fix photos, and mark the work fixed. Managers then verify (or reject) from a portal queue. Checklist items are classified by trade so snags route only to contractors who handle that trade.

## Goals

1. End-to-end data flow for contractor assignment — read path, closure path, verification path.
2. Trade-based routing prevents assigning (say) a plumber to an electrical snag.
3. Faithful to the Excel snagging report format (NC photos → Contractor closure → Client verification → Client remark).
4. Idiot-proof Android UX — contractor has no sync toggles, no decisions to make, just "work on the snag, upload photo, tap Fixed."

## Non-goals (V1)

- Notifications (SSE/push). Infra exists; wiring deferred.
- Contractor-to-contractor handoffs.
- Scope-based auto-assignment (trade + tower ownership). Stays per-snag for now — Option C from `problems.md` remains deferred.
- Android contractor UI beyond the core list + detail + closure flow.

## Decisions locked (from April 2026 discussion)

| # | Decision |
|---|---|
| 1 | Trade taxonomy: `PLUMBING`, `ELECTRICAL`, `PAINTING`, `CARPENTRY`, `TILING`, `CIVIL`, `HVAC`, `MISC`. String column, validated app-side (no PG enum per project convention). |
| 2 | Contractors can have **multiple trades** — stored as `text[]` on users. Assignment rule: `entry.trade IN contractor.trades`. |
| 3 | Existing seed data is wiped and re-seeded with `trade` populated on every checklist template row. Live pod data is expendable. |
| 4 | `contractors` table is **merged into** `users`. Contractor = User with `role = CONTRACTOR`. `company` and `trades` live on users. |
| 5 | Two-step closure: contractor marks `FIXED` → manager verifies or rejects. Manager verification queue is a dedicated portal page. |
| 6 | **Only contractors** upload closure photos. Inspectors upload NC photos only. |
| 7 | Manager's verification remark is free text (`verification_remark` column). |
| 8 | **One active contractor assignment per snag.** Uniqueness on `entry_id` alone (not `(entry_id, contractor_id)` as today). |
| 9 | Contractor deactivation with open assignments → 409 with list of orphaned entries. Portal prompts manager to reassign. |
| 10 | **Contractors log into the Android app, not the portal.** Portal = managers only. App hosts two role-based UIs: INSPECTOR (existing) and CONTRACTOR (new). |
| 11 | Pod data wipe is acceptable — no migration-preservation constraints. |
| 12 | Verification queue shows FIXED entries **within the manager's project scope**. |
| 13 | Rejection → snag goes back to `OPEN`; the contractor sees the rejection remark on their app. |
| 14 | App login is a single form. Backend returns role; app routes to the right surface. Contractors see "Business Associate" terminology in-app. |
| 15 | One role per user. No User can be both MANAGER and CONTRACTOR. |
| 16 | Notifications deferred. |

## Data model changes

### `users`

| Column | Change |
|---|---|
| `role` | Accepted values extended to `MANAGER`, `INSPECTOR`, `CONTRACTOR`. String validation. |
| `email` | **New.** `text`, nullable. For all users (not contractor-specific). Not unique at the DB level for now — some users may share a company inbox, some may not have one. |
| `phone` | **New.** `text`, nullable. For all users. |
| `trades` | **New.** `text[]` (Postgres array), nullable, only populated for CONTRACTOR rows. Validated against the trade taxonomy. |
| `company` | **New.** `text`, nullable, only meaningful for CONTRACTOR rows. |

### `contractors` (dropped)

All rows migrated into `users` with `role=CONTRACTOR`. For each existing contractor:
- `name` → `users.full_name`
- `email` → `users.email` (preserved).
- `phone` → `users.phone` (preserved).
- `username` → slug generated from `name` (e.g., `upavan-supremearts`). If the email's local part is cleaner, use that. Collisions suffixed with `-2`, `-3`, etc.
- `specialty` (free text) → migration best-effort keyword-match to one of the trade enum values; unmatched rows default to `[MISC]` and flagged in migration output for manager review.
- `company` → `users.company`.
- Generated temporary password, surfaced in migration output; manager resets via portal after rollout.

### `checklist_templates`

| Column | Change |
|---|---|
| `trade` | **New.** `text`, NOT NULL after re-seed. Values from the trade taxonomy. |

### `inspection_entries`

| Column | Change |
|---|---|
| `trade` | **New.** `text`, NOT NULL, inherited from template at creation time (same pattern as `room_label`, `category`, `item_name` today). |
| `fixed_at` | **New.** `timestamptz`, nullable. |
| `fixed_by_id` | **New.** `uuid`, FK → `users.id`, nullable. |
| `verified_at` | **New.** `timestamptz`, nullable. |
| `verified_by_id` | **New.** `uuid`, FK → `users.id`, nullable. |
| `verification_remark` | **New.** `text`, nullable. Set only when status=VERIFIED. |
| `rejection_remark` | **New.** `text`, nullable. Set only on reject → OPEN. Cleared when contractor re-marks fixed. |
| `rejected_at` | **New.** `timestamptz`, nullable. Cleared when contractor re-marks fixed. |

State-transition rules:
- `OPEN` → `FIXED`: contractor marks fixed. Sets `fixed_at`, `fixed_by_id`. Clears `rejection_remark`, `rejected_at`. Requires ≥1 `CLOSURE`-kind snag image.
- `FIXED` → `VERIFIED`: manager verifies. Sets `verified_at`, `verified_by_id`, `verification_remark`.
- `FIXED` → `OPEN`: manager rejects. Sets `rejection_remark`, `rejected_at`. Clears `fixed_at`, `fixed_by_id`.

### `snag_images`

| Column | Change |
|---|---|
| `kind` | **New.** `text`, NOT NULL, default `NC`. Values: `NC` (original defect) / `CLOSURE` (post-fix). Existing rows backfilled to `NC`. |

Voice notes and inspection videos stay single-kind (inspector-only).

### `snag_contractor_assignments`

| Change | Why |
|---|---|
| FK `contractor_id` repointed: `contractors.id` → `users.id`. | Contractors are users now. |
| Unique constraint changed: `(entry_id, contractor_id)` → **unique on `entry_id`**. | One active contractor per snag (decision #8). |

## API surface

### New endpoints

| Method + Path | Role | Purpose |
|---|---|---|
| `GET /entries/my-assigned?status=` | CONTRACTOR | Snags assigned to the caller. Query filter by `snag_fix_status` (defaults to OPEN + FIXED). |
| `POST /entries/{id}/mark-fixed` | CONTRACTOR | Transition to FIXED. Validates caller is the assigned contractor and ≥1 CLOSURE image exists. |
| `POST /entries/{id}/verify` | MANAGER | Transition to VERIFIED. Body: `{ verification_remark }`. |
| `POST /entries/{id}/reject` | MANAGER | Transition back to OPEN. Body: `{ rejection_remark }`. |
| `GET /entries/verification-queue` | MANAGER | FIXED entries within the manager's project scope. Filter by project. |
| `GET /entries/orphaned-assignments` | MANAGER | Assignments where the contractor is deactivated. For reassignment UI. |

### Changed endpoints

| Endpoint | Change |
|---|---|
| `POST /entries/{id}/assign-contractor/{contractor_id}` | Validates `entry.trade IN contractor.trades`. Enforces one-active-per-entry. Contractor ID is a `users.id` now. |
| `GET /entries/{id}` and snag list | `InspectionEntryResponse` gains `contractor_assignments: list[...]` (eager-loaded via `selectinload`). Fixes the original write-only bug. Includes contractor name, trades, assigned_at, due_date, notes. |
| `POST /users` | Accepts role `CONTRACTOR`. Accepts optional `email` and `phone` for all roles. For CONTRACTOR role: validates `trades` is non-empty list of valid trade values. For non-contractor roles: `trades` and `company` must be null/absent. |
| `PATCH /users/{id}` | Allows updating `email`, `phone`, `company`, `trades` in addition to existing fields. |
| `DELETE /users/{id}` (or deactivate) | If role=CONTRACTOR and has open assignments, returns 409 with orphan list. Caller confirms via `?force=true` or reassigns first. |
| `POST /files/upload` | New form field `kind` (NC/CLOSURE). Contractor role can only upload CLOSURE. Inspector can only upload NC. |
| `POST /sync/pull` | Scope resolver branches on role. For CONTRACTOR: scope is assigned entries + their parent flats/floors/buildings/projects (read-only hierarchy metadata). No sibling entries. No checklist templates. No flat type rooms. |
| `POST /sync/push` | Accepts contractor-originated operations: mark-fixed, closure photo upload. |

## Three-repo sync contract

Fields crossing the sync boundary must be renamed consistently:

| Backend (snake_case) | Portal (camelCase, TypeScript) | Android (@SerializedName annotations) |
|---|---|---|
| `trade` | `trade` | `trade` |
| `trades` | `trades: string[]` | `trades: List<String>` |
| `fixed_at` | `fixedAt` | `@SerializedName("fixed_at") fixedAt` |
| `fixed_by_id` | `fixedById` | `@SerializedName("fixed_by_id") fixedById` |
| `verified_at` | `verifiedAt` | `@SerializedName("verified_at") verifiedAt` |
| `verified_by_id` | `verifiedById` | `@SerializedName("verified_by_id") verifiedById` |
| `verification_remark` | `verificationRemark` | `@SerializedName("verification_remark") verificationRemark` |
| `rejection_remark` | `rejectionRemark` | `@SerializedName("rejection_remark") rejectionRemark` |
| `rejected_at` | `rejectedAt` | `@SerializedName("rejected_at") rejectedAt` |
| `contractor_assignments` | `contractorAssignments` | `@SerializedName("contractor_assignments") contractorAssignments` |
| `kind` (on snag images) | `kind` | `@SerializedName("kind") kind: String` |

## Phasing

### Phase 1 — Backend schema + seed wipe (1 PR)

- Alembic migration: add columns, migrate `contractors` → `users`, drop `contractors`, repoint FK, change uniqueness, backfill `snag_images.kind`.
- Seed script: wipe `checklist_templates` and re-seed with `trade` populated per row.
- Validate: run migration locally against a snapshot of pod data, spot-check user rows, spot-check orphaned assignments after a simulated contractor deactivation.
- **No API changes yet.** Purely schema.

### Phase 2 — Backend APIs

- Add `require_contractor` dep.
- Implement all new endpoints + changes listed above.
- Update `sync_service._resolve_scope()` with a CONTRACTOR branch.
- Update `process_push` to accept contractor ops.
- Update `InspectionEntryResponse` + snag-list endpoint to eager-load `contractor_assignments`.
- Add `kind` handling to `/files/upload` + `/sync/upload-file`.

### Phase 3 — Portal

- Merge "Business Associates" into the Users page OR keep as a separate page filtering `role=CONTRACTOR` — final call during implementation, but DB is unified.
- User creation form: role picker (MANAGER/INSPECTOR/CONTRACTOR). For CONTRACTOR, surface trades multi-select + company field.
- Login: block contractors with "Please log in from the mobile app."
- InspectionDetailPage: render current assignment, closure photos (separated from NC), fix timeline (assigned → fixed → verified/rejected with timestamps).
- Assignment dropdown filters contractors by `entry.trade`.
- **New Verification Queue page** in sidebar: lists FIXED entries in manager's project scope, Verify/Reject actions, remark inputs.
- Checklist Template editor: `trade` selector per item.
- Orphan handling: deactivation dialog shows affected snags + reassignment path.

### Phase 4 — Android app (contractor surface)

- Login ViewModel branches on `user.role`:
  - `INSPECTOR` → existing `ProjectListScreen` root.
  - `CONTRACTOR` → new `ContractorHomeScreen` root.
- New screens:
  - `ContractorHomeScreen`: list of assigned snags, filter chips (Open / Fixed / Verified + Rejected-visual-state), project filter, search.
  - `ContractorSnagDetailScreen`: NC photos (read-only), inspector's notes + voice notes (read-only), location breadcrumb (Project > Tower > Floor > Flat > Room), closure photo capture (camera), upload queue state, "Mark Fixed" CTA (disabled until ≥1 closure photo uploaded), rejection remark banner if visible.
- Room schema changes:
  - `InspectionEntryEntity`: new columns `trade`, `fixedAt`, `fixedById`, `verifiedAt`, `verifiedById`, `verificationRemark`, `rejectionRemark`, `rejectedAt`.
  - `SnagImageEntity`: new column `kind` (default `NC` for migration).
  - `UserSessionEntity`: persist `role`, `trades` (CSV or JSON).
  - Room migration bumps DB version.
- Sync:
  - `SyncManager` for CONTRACTOR: pulls assigned entries + hierarchy metadata. No template tables.
  - Queue entries for `mark_fixed`, `closure_photo_upload` (reuses `fileLocalPath` pattern with `kind=CLOSURE`).
  - Post-save immediate sync reuses existing flow.
- UI principles stay the same: no sync toggles, no manual refresh needed — pull-to-refresh exists as an escape hatch, everything else is background.

## Open implementation questions (to resolve during build, not now)

- Whether the portal keeps a separate "Business Associates" sidebar entry or collapses it into Users. UX call; no backend impact.
- Whether rejection history is persisted (multiple reject cycles) or only the latest rejection is kept on the entry. V1: latest only. A history table can come later if real usage shows loops.
- Camera-capture UX on the contractor app — pick-from-gallery fallback or camera-only?
- Whether to enforce email uniqueness later (once password-reset-by-email lands). V1: not unique.

## Cross-repo contract reminders (from existing CLAUDE.md files)

- All IDs are UUID strings. Never `Number()` in portal or `Long` in app.
- Updates use `PATCH`, not `PUT`.
- Sync push `data` dict must never contain `"id"` key.
- Backend `server_time` is ISO string, app `lastSyncedAt` is epoch millis — converter already handles this.
- CREATEs via sync are backend-idempotent (content-hash unique index on entries).

## When to update this doc

- Anytime a decision changes or a new edge case gets answered.
- Anytime a phase completes — mark it complete and note the PR/commit.
- When the feature is fully shipped, move the "Contractor assignment is a write-only black hole" entry in `nosara-portal/problems.md` to a "Resolved" section (or delete it) and archive this doc under `docs/shipped/`.
