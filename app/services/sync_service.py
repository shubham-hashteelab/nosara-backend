import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.checklist import ChecklistTemplate, FlatTypeRoom, FloorPlanLayout
from app.models.contractor import SnagContractorAssignment
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry, SnagImage
from app.models.project import Project
from app.models.user import (
    User,
    UserBuildingAssignment,
    UserFlatAssignment,
    UserProjectAssignment,
)
from app.schemas.sync import SyncOperation, SyncRejection
from app.services.event_service import event_service
from app.services.inspection_service import recompute_flat_inspection_status

logger = logging.getLogger(__name__)

ENTITY_MODEL_MAP: dict[str, Any] = {
    "project": Project,
    "building": Building,
    "floor": Floor,
    "flat": Flat,
    "inspection_entry": InspectionEntry,
    "checklist_template": ChecklistTemplate,
    "flat_type_room": FlatTypeRoom,
    "floor_plan_layout": FloorPlanLayout,
}

# The only field a CONTRACTOR may write through /sync/push. Everything else
# (status, severity, notes, trade, timeline columns) is locked down for them.
_CONTRACTOR_WRITABLE_FIELDS: frozenset[str] = frozenset({"snag_fix_status"})


class SyncService:
    async def process_push(
        self,
        operations: list[SyncOperation],
        caller: User,
        db: AsyncSession,
    ) -> tuple[list[str], list[SyncRejection]]:
        accepted: list[str] = []
        rejected: list[SyncRejection] = []

        for op in operations:
            # SAVEPOINT per op so a single DB-level failure rolls back just
            # that op instead of cascading through the outer commit.
            try:
                async with db.begin_nested():
                    model = ENTITY_MODEL_MAP.get(op.entity_type)
                    if model is None:
                        rejected.append(
                            SyncRejection(
                                id=op.entity_id,
                                reason=f"Unknown entity type: {op.entity_type}",
                            )
                        )
                        continue

                    if caller.role == "CONTRACTOR":
                        await self._apply_contractor_op(
                            op=op,
                            caller=caller,
                            db=db,
                            accepted=accepted,
                            rejected=rejected,
                        )
                        continue

                    if op.operation == "CREATE":
                        existing = await db.execute(
                            select(model).where(model.id == op.entity_id)
                        )
                        if existing.scalars().first() is not None:
                            accepted.append(str(op.entity_id))
                            continue

                        data = dict(op.data)
                        data["id"] = op.entity_id
                        if op.entity_type == "inspection_entry":
                            data["inspector_id"] = caller.id
                            content_dup = await db.execute(
                                select(InspectionEntry).where(
                                    InspectionEntry.flat_id == data.get("flat_id"),
                                    InspectionEntry.room_label == data.get("room_label"),
                                    InspectionEntry.category == data.get("category"),
                                    InspectionEntry.item_name == data.get("item_name"),
                                )
                            )
                            if content_dup.scalars().first() is not None:
                                accepted.append(str(op.entity_id))
                                continue
                        obj = model(**data)
                        db.add(obj)
                        await db.flush()
                        accepted.append(str(op.entity_id))

                    elif op.operation == "UPDATE":
                        result = await db.execute(
                            select(model).where(model.id == op.entity_id)
                        )
                        obj = result.scalars().first()
                        if obj is None:
                            rejected.append(
                                SyncRejection(
                                    id=op.entity_id,
                                    reason=f"{op.entity_type} not found",
                                )
                            )
                            continue
                        for key, value in op.data.items():
                            if key == "id":
                                continue
                            if hasattr(obj, key):
                                setattr(obj, key, value)
                        await db.flush()

                        if op.entity_type == "inspection_entry" and "status" in op.data:
                            await recompute_flat_inspection_status(obj.flat_id, db)
                            await db.flush()

                        accepted.append(str(op.entity_id))

                    elif op.operation == "DELETE":
                        result = await db.execute(
                            select(model).where(model.id == op.entity_id)
                        )
                        obj = result.scalars().first()
                        if obj:
                            await db.delete(obj)
                            await db.flush()
                        accepted.append(str(op.entity_id))

                    else:
                        rejected.append(
                            SyncRejection(
                                id=op.entity_id,
                                reason=f"Unknown operation: {op.operation}",
                            )
                        )

            except Exception as exc:
                logger.error(
                    "Sync push error for %s %s: %s",
                    op.entity_type,
                    op.entity_id,
                    exc,
                )
                rejected.append(SyncRejection(id=op.entity_id, reason=str(exc)))

        await db.commit()

        if accepted:
            entity_types = list({op.entity_type for op in operations})
            try:
                await event_service.notify(
                    {
                        "event_type": "sync_push_completed",
                        "user_id": str(caller.id),
                        "role": caller.role,
                        "entity_types": entity_types,
                        "accepted_count": len(accepted),
                    }
                )
            except Exception:
                logger.exception("Failed to notify after sync push")

        return accepted, rejected

    async def _apply_contractor_op(
        self,
        op: SyncOperation,
        caller: User,
        db: AsyncSession,
        accepted: list[str],
        rejected: list[SyncRejection],
    ) -> None:
        """CONTRACTOR push path — locked down to the mark-fixed transition.

        Anything else (CREATEs, DELETEs, non-entry entity types, writes to
        other columns) is rejected. The same integrity rules as the
        /mark-fixed endpoint apply: assignment ownership, current status
        OPEN, and at least one CLOSURE image."""
        if op.entity_type != "inspection_entry":
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason="Contractors can only push inspection_entry UPDATEs",
                )
            )
            return
        if op.operation != "UPDATE":
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason="Contractors can only push UPDATE operations",
                )
            )
            return

        disallowed = {
            k for k in op.data.keys() if k != "id" and k not in _CONTRACTOR_WRITABLE_FIELDS
        }
        if disallowed:
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason=(
                        "Contractor cannot update fields: "
                        + ", ".join(sorted(disallowed))
                    ),
                )
            )
            return

        target_status = op.data.get("snag_fix_status")
        if target_status != "FIXED":
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason="Contractor can only push snag_fix_status=FIXED",
                )
            )
            return

        assignment = await db.scalar(
            select(SnagContractorAssignment).where(
                SnagContractorAssignment.inspection_entry_id == op.entity_id,
                SnagContractorAssignment.contractor_id == caller.id,
            )
        )
        if assignment is None:
            rejected.append(
                SyncRejection(
                    id=op.entity_id, reason="Not assigned to this entry"
                )
            )
            return

        entry = await db.scalar(
            select(InspectionEntry).where(InspectionEntry.id == op.entity_id)
        )
        if entry is None:
            rejected.append(
                SyncRejection(id=op.entity_id, reason="Entry not found")
            )
            return

        # An entry must actually be a snag (status=FAIL) before any fix-flow
        # transition is meaningful. Mirrors the /mark-fixed endpoint guard.
        if entry.status != "FAIL":
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason="Only FAIL entries can be marked fixed",
                )
            )
            return

        if entry.snag_fix_status == "FIXED":
            accepted.append(str(op.entity_id))  # idempotent
            return
        if entry.snag_fix_status != "OPEN":
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason=(
                        f"Cannot transition from {entry.snag_fix_status} to FIXED"
                    ),
                )
            )
            return

        closure_count = await db.scalar(
            select(func.count(SnagImage.id)).where(
                SnagImage.inspection_entry_id == op.entity_id,
                SnagImage.kind == "CLOSURE",
            )
        )
        if (closure_count or 0) == 0:
            rejected.append(
                SyncRejection(
                    id=op.entity_id,
                    reason="At least one CLOSURE image required before marking fixed",
                )
            )
            return

        entry.snag_fix_status = "FIXED"
        entry.fixed_at = datetime.now(timezone.utc)
        entry.fixed_by_id = caller.id
        entry.rejection_remark = None
        entry.rejected_at = None
        await db.flush()
        accepted.append(str(op.entity_id))

    async def _resolve_scope(
        self,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, set[uuid.UUID]]:
        """
        Resolve the complete access scope for a non-contractor user by
        combining all assignment levels.
        Returns dict with 'project_ids', 'building_ids', 'floor_ids', 'flat_ids'.
        """
        proj_q = await db.execute(
            select(UserProjectAssignment.project_id).where(
                UserProjectAssignment.user_id == user_id
            )
        )
        assigned_project_ids = {row[0] for row in proj_q.all()}

        bldg_q = await db.execute(
            select(UserBuildingAssignment.building_id).where(
                UserBuildingAssignment.user_id == user_id
            )
        )
        assigned_building_ids = {row[0] for row in bldg_q.all()}

        flat_q = await db.execute(
            select(UserFlatAssignment.flat_id).where(
                UserFlatAssignment.user_id == user_id
            )
        )
        assigned_flat_ids = {row[0] for row in flat_q.all()}

        all_building_ids: set[uuid.UUID] = set(assigned_building_ids)
        all_floor_ids: set[uuid.UUID] = set()
        all_flat_ids: set[uuid.UUID] = set(assigned_flat_ids)
        all_project_ids: set[uuid.UUID] = set(assigned_project_ids)

        if assigned_project_ids:
            bq = await db.execute(
                select(Building.id).where(Building.project_id.in_(assigned_project_ids))
            )
            project_building_ids = {row[0] for row in bq.all()}
            all_building_ids |= project_building_ids

        if assigned_building_ids:
            parent_q = await db.execute(
                select(Building.project_id).where(Building.id.in_(assigned_building_ids))
            )
            all_project_ids |= {row[0] for row in parent_q.all()}

        if assigned_flat_ids:
            flat_parents_q = await db.execute(
                select(Floor.id, Floor.building_id, Building.project_id)
                .join(Flat, Flat.floor_id == Floor.id)
                .join(Building, Floor.building_id == Building.id)
                .where(Flat.id.in_(assigned_flat_ids))
            )
            for floor_id, building_id, project_id in flat_parents_q.all():
                all_floor_ids.add(floor_id)
                all_building_ids.add(building_id)
                all_project_ids.add(project_id)

        if all_building_ids:
            fq = await db.execute(
                select(Floor.id).where(Floor.building_id.in_(all_building_ids))
            )
            all_floor_ids |= {row[0] for row in fq.all()}

        if assigned_project_ids or assigned_building_ids:
            expandable_floor_ids: set[uuid.UUID] = set()
            if assigned_project_ids:
                pf_q = await db.execute(
                    select(Floor.id)
                    .join(Building, Floor.building_id == Building.id)
                    .where(Building.project_id.in_(assigned_project_ids))
                )
                expandable_floor_ids |= {row[0] for row in pf_q.all()}
            if assigned_building_ids:
                bf_q = await db.execute(
                    select(Floor.id).where(Floor.building_id.in_(assigned_building_ids))
                )
                expandable_floor_ids |= {row[0] for row in bf_q.all()}

            if expandable_floor_ids:
                flat_exp_q = await db.execute(
                    select(Flat.id).where(Flat.floor_id.in_(expandable_floor_ids))
                )
                all_flat_ids |= {row[0] for row in flat_exp_q.all()}

        return {
            "project_ids": all_project_ids,
            "building_ids": all_building_ids,
            "floor_ids": all_floor_ids,
            "flat_ids": all_flat_ids,
        }

    async def _assignments_changed_since(
        self,
        user_id: uuid.UUID,
        last_synced_at: datetime,
        db: AsyncSession,
    ) -> bool:
        for model in (UserProjectAssignment, UserBuildingAssignment, UserFlatAssignment):
            latest = await db.scalar(
                select(func.max(model.assigned_at)).where(model.user_id == user_id)
            )
            if latest is not None and latest > last_synced_at:
                return True
        return False

    async def process_pull(
        self,
        last_synced_at: datetime,
        caller: User,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pull data accessible to the caller since `last_synced_at`.

        Branches on role: CONTRACTOR gets only their assigned entries +
        parent hierarchy for navigation, no templates/rooms/layouts."""
        if caller.role == "CONTRACTOR":
            return await self._process_pull_contractor(
                last_synced_at=last_synced_at, user_id=caller.id, db=db
            )

        scope = await self._resolve_scope(caller.id, db)

        project_ids = scope["project_ids"]
        building_ids = scope["building_ids"]
        floor_ids = scope["floor_ids"]
        flat_ids = scope["flat_ids"]

        include_full_hierarchy = await self._assignments_changed_since(
            caller.id, last_synced_at, db
        )

        if project_ids:
            proj_filters = [Project.id.in_(project_ids)]
            if not include_full_hierarchy:
                proj_filters.append(Project.updated_at >= last_synced_at)
            projects_q = await db.execute(select(Project).where(*proj_filters))
        else:
            projects_q = await db.execute(select(Project).where(False))
        projects = projects_q.scalars().all()

        if building_ids:
            bldg_filters = [Building.id.in_(building_ids)]
            if not include_full_hierarchy:
                bldg_filters.append(Building.updated_at >= last_synced_at)
            buildings_q = await db.execute(select(Building).where(*bldg_filters))
        else:
            buildings_q = await db.execute(select(Building).where(False))
        buildings = buildings_q.scalars().all()

        if floor_ids:
            floor_filters = [Floor.id.in_(floor_ids)]
            if not include_full_hierarchy:
                floor_filters.append(Floor.updated_at >= last_synced_at)
            floors_q = await db.execute(select(Floor).where(*floor_filters))
        else:
            floors_q = await db.execute(select(Floor).where(False))
        floors = floors_q.scalars().all()

        if flat_ids:
            flat_filters = [Flat.id.in_(flat_ids)]
            if not include_full_hierarchy:
                flat_filters.append(Flat.updated_at >= last_synced_at)
            flats_q = await db.execute(select(Flat).where(*flat_filters))
        else:
            flats_q = await db.execute(select(Flat).where(False))
        flats = flats_q.scalars().all()

        if flat_ids:
            entry_filters = [InspectionEntry.flat_id.in_(flat_ids)]
            if not include_full_hierarchy:
                entry_filters.append(InspectionEntry.updated_at >= last_synced_at)
            entries_q = await db.execute(
                select(InspectionEntry)
                .options(
                    selectinload(InspectionEntry.images),
                    selectinload(InspectionEntry.voice_notes),
                    selectinload(InspectionEntry.videos),
                    selectinload(InspectionEntry.contractor_assignments).selectinload(
                        SnagContractorAssignment.contractor
                    ),
                )
                .where(*entry_filters)
            )
        else:
            entries_q = await db.execute(select(InspectionEntry).where(False))
        entries = entries_q.scalars().all()

        # Contractors are delivered via their own pull path. Inspector/manager
        # response keeps the wire shape but always empty for now.
        contractors: list[Any] = []

        templates_q = await db.execute(
            select(ChecklistTemplate).where(
                ChecklistTemplate.updated_at >= last_synced_at
            )
        )
        templates = templates_q.scalars().all()

        ftr_q = await db.execute(select(FlatTypeRoom))
        flat_type_rooms = ftr_q.scalars().all()

        fpl_q = await db.execute(select(FloorPlanLayout))
        floor_plan_layouts = fpl_q.scalars().all()

        server_time = datetime.now(timezone.utc).isoformat()

        scope_snapshot = {
            "project_ids": [str(x) for x in project_ids],
            "building_ids": [str(x) for x in building_ids],
            "floor_ids": [str(x) for x in floor_ids],
            "flat_ids": [str(x) for x in flat_ids],
        }

        return {
            "projects": projects,
            "buildings": buildings,
            "floors": floors,
            "flats": flats,
            "inspection_entries": entries,
            "contractors": contractors,
            "checklist_templates": templates,
            "flat_type_rooms": flat_type_rooms,
            "floor_plan_layouts": floor_plan_layouts,
            "deleted_ids": [],
            "scope_snapshot": scope_snapshot,
            "server_time": server_time,
        }

    async def _process_pull_contractor(
        self,
        last_synced_at: datetime,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pull path for CONTRACTOR role: only assigned entries + the parent
        flat/floor/building/project hierarchy for navigation. No sibling
        entries, no templates, no room/layout definitions."""
        assignment_q = await db.execute(
            select(SnagContractorAssignment.inspection_entry_id).where(
                SnagContractorAssignment.contractor_id == user_id
            )
        )
        assigned_entry_ids = {row[0] for row in assignment_q.all()}

        # New assignments granted after last_synced_at mean parent rows with
        # old `updated_at` must still be delivered, otherwise scope_snapshot
        # lists flats whose rows never reach the client.
        latest_assignment_at = await db.scalar(
            select(func.max(SnagContractorAssignment.assigned_at)).where(
                SnagContractorAssignment.contractor_id == user_id
            )
        )
        include_full_hierarchy = (
            latest_assignment_at is not None and latest_assignment_at > last_synced_at
        )

        server_time = datetime.now(timezone.utc).isoformat()

        empty_scope = {
            "project_ids": [],
            "building_ids": [],
            "floor_ids": [],
            "flat_ids": [],
        }
        empty_response: dict[str, Any] = {
            "projects": [],
            "buildings": [],
            "floors": [],
            "flats": [],
            "inspection_entries": [],
            "contractors": [],
            "checklist_templates": [],
            "flat_type_rooms": [],
            "floor_plan_layouts": [],
            "deleted_ids": [],
            "scope_snapshot": empty_scope,
            "server_time": server_time,
        }
        if not assigned_entry_ids:
            return empty_response

        flat_q = await db.execute(
            select(InspectionEntry.flat_id).where(
                InspectionEntry.id.in_(assigned_entry_ids)
            )
        )
        flat_ids = {row[0] for row in flat_q.all()}
        if not flat_ids:
            return empty_response

        hierarchy_q = await db.execute(
            select(Flat.id, Flat.floor_id, Floor.building_id, Building.project_id)
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
            .where(Flat.id.in_(flat_ids))
        )
        floor_ids: set[uuid.UUID] = set()
        building_ids: set[uuid.UUID] = set()
        project_ids: set[uuid.UUID] = set()
        for _, floor_id, building_id, project_id in hierarchy_q.all():
            floor_ids.add(floor_id)
            building_ids.add(building_id)
            project_ids.add(project_id)

        async def _fetch(model, ids, updated_col):
            if not ids:
                return []
            filters = [model.id.in_(ids)]
            if not include_full_hierarchy:
                filters.append(updated_col >= last_synced_at)
            result = await db.execute(select(model).where(*filters))
            return result.scalars().all()

        projects = await _fetch(Project, project_ids, Project.updated_at)
        buildings = await _fetch(Building, building_ids, Building.updated_at)
        floors = await _fetch(Floor, floor_ids, Floor.updated_at)
        flats = await _fetch(Flat, flat_ids, Flat.updated_at)

        entry_filters = [InspectionEntry.id.in_(assigned_entry_ids)]
        if not include_full_hierarchy:
            entry_filters.append(InspectionEntry.updated_at >= last_synced_at)
        entries_q = await db.execute(
            select(InspectionEntry)
            .options(
                selectinload(InspectionEntry.images),
                selectinload(InspectionEntry.voice_notes),
                selectinload(InspectionEntry.videos),
                selectinload(InspectionEntry.contractor_assignments).selectinload(
                    SnagContractorAssignment.contractor
                ),
            )
            .where(*entry_filters)
        )
        entries = entries_q.scalars().all()

        scope_snapshot = {
            "project_ids": [str(x) for x in project_ids],
            "building_ids": [str(x) for x in building_ids],
            "floor_ids": [str(x) for x in floor_ids],
            "flat_ids": [str(x) for x in flat_ids],
        }

        return {
            "projects": projects,
            "buildings": buildings,
            "floors": floors,
            "flats": flats,
            "inspection_entries": entries,
            "contractors": [],
            "checklist_templates": [],
            "flat_type_rooms": [],
            "floor_plan_layouts": [],
            "deleted_ids": [],
            "scope_snapshot": scope_snapshot,
            "server_time": server_time,
        }


sync_service = SyncService()
