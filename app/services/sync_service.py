import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.building import Building
from app.models.checklist import ChecklistTemplate, FlatTypeRoom, FloorPlanLayout
from app.models.contractor import Contractor
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry
from app.models.project import Project
from app.models.user import UserProjectAssignment, UserBuildingAssignment, UserFlatAssignment
from app.schemas.sync import SyncOperation, SyncRejection
from app.services.event_service import event_service

logger = logging.getLogger(__name__)

ENTITY_MODEL_MAP: dict[str, Any] = {
    "project": Project,
    "building": Building,
    "floor": Floor,
    "flat": Flat,
    "inspection_entry": InspectionEntry,
    "contractor": Contractor,
    "checklist_template": ChecklistTemplate,
    "flat_type_room": FlatTypeRoom,
    "floor_plan_layout": FloorPlanLayout,
}


class SyncService:
    async def process_push(
        self,
        operations: list[SyncOperation],
        inspector_id: uuid.UUID,
        db: AsyncSession,
    ) -> tuple[list[str], list[SyncRejection]]:
        accepted: list[str] = []
        rejected: list[SyncRejection] = []

        for op in operations:
            try:
                model = ENTITY_MODEL_MAP.get(op.entity_type)
                if model is None:
                    rejected.append(
                        SyncRejection(id=op.entity_id, reason=f"Unknown entity type: {op.entity_type}")
                    )
                    continue

                if op.operation == "CREATE":
                    # Check if record already exists (idempotent — don't create duplicates)
                    existing = await db.execute(select(model).where(model.id == op.entity_id))
                    if existing.scalars().first() is not None:
                        accepted.append(str(op.entity_id))
                        continue

                    data = dict(op.data)
                    data["id"] = op.entity_id
                    if op.entity_type == "inspection_entry":
                        data["inspector_id"] = inspector_id
                    obj = model(**data)
                    db.add(obj)
                    await db.flush()
                    accepted.append(str(op.entity_id))

                elif op.operation == "UPDATE":
                    result = await db.execute(select(model).where(model.id == op.entity_id))
                    obj = result.scalars().first()
                    if obj is None:
                        rejected.append(SyncRejection(id=op.entity_id, reason=f"{op.entity_type} not found"))
                        continue
                    for key, value in op.data.items():
                        if key == "id":
                            continue  # Never overwrite primary key
                        if hasattr(obj, key):
                            setattr(obj, key, value)
                    await db.flush()
                    accepted.append(str(op.entity_id))

                elif op.operation == "DELETE":
                    result = await db.execute(select(model).where(model.id == op.entity_id))
                    obj = result.scalars().first()
                    if obj:
                        await db.delete(obj)
                        await db.flush()
                    accepted.append(str(op.entity_id))

                else:
                    rejected.append(SyncRejection(id=op.entity_id, reason=f"Unknown operation: {op.operation}"))

            except Exception as exc:
                logger.error("Sync push error for %s %s: %s", op.entity_type, op.entity_id, exc)
                rejected.append(SyncRejection(id=op.entity_id, reason=str(exc)))

        await db.commit()

        # Notify SSE clients about the sync push
        if accepted:
            entity_types = list({op.entity_type for op in operations})
            try:
                await event_service.notify({
                    "event_type": "sync_push_completed",
                    "inspector_id": str(inspector_id),
                    "entity_types": entity_types,
                    "accepted_count": len(accepted),
                })
            except Exception:
                logger.exception("Failed to notify after sync push")

        return accepted, rejected

    async def _resolve_scope(
        self,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, set[uuid.UUID]]:
        """
        Resolve the complete access scope for a user by combining all assignment levels.
        Returns dict with 'project_ids', 'building_ids', 'floor_ids', 'flat_ids'.

        Logic: union all levels. If you have project access, all buildings/floors/flats
        in that project are included. If you have building access, the parent project
        is also included (for navigation). Same for flat access.
        """
        # 1. Get direct assignments
        proj_q = await db.execute(
            select(UserProjectAssignment.project_id).where(UserProjectAssignment.user_id == user_id)
        )
        assigned_project_ids = {row[0] for row in proj_q.all()}

        bldg_q = await db.execute(
            select(UserBuildingAssignment.building_id).where(UserBuildingAssignment.user_id == user_id)
        )
        assigned_building_ids = {row[0] for row in bldg_q.all()}

        flat_q = await db.execute(
            select(UserFlatAssignment.flat_id).where(UserFlatAssignment.user_id == user_id)
        )
        assigned_flat_ids = {row[0] for row in flat_q.all()}

        # 2. Expand project assignments → all buildings, floors, flats
        all_building_ids: set[uuid.UUID] = set(assigned_building_ids)
        all_floor_ids: set[uuid.UUID] = set()
        all_flat_ids: set[uuid.UUID] = set(assigned_flat_ids)
        all_project_ids: set[uuid.UUID] = set(assigned_project_ids)

        if assigned_project_ids:
            # All buildings in assigned projects
            bq = await db.execute(
                select(Building.id).where(Building.project_id.in_(assigned_project_ids))
            )
            project_building_ids = {row[0] for row in bq.all()}
            all_building_ids |= project_building_ids

        # 3. From building-level assignments, derive parent projects (for navigation)
        if assigned_building_ids:
            parent_q = await db.execute(
                select(Building.project_id).where(Building.id.in_(assigned_building_ids))
            )
            all_project_ids |= {row[0] for row in parent_q.all()}

        # 4. From flat-level assignments, derive parent floors, buildings, projects
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

        # 5. Expand all buildings → their floors
        if all_building_ids:
            fq = await db.execute(
                select(Floor.id).where(Floor.building_id.in_(all_building_ids))
            )
            all_floor_ids |= {row[0] for row in fq.all()}

        # 6. Expand all floors → their flats (only for project/building level access)
        # For flat-level-only users, we already have the specific flat IDs
        if assigned_project_ids or assigned_building_ids:
            # Get flats from floors belonging to project/building assignments
            expandable_floor_ids = set()
            if assigned_project_ids:
                # All floors in project-level buildings
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

    async def process_pull(
        self,
        last_synced_at: datetime,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pull all data updated since last_synced_at for the user's accessible scope."""
        scope = await self._resolve_scope(user_id, db)

        project_ids = scope["project_ids"]
        building_ids = scope["building_ids"]
        floor_ids = scope["floor_ids"]
        flat_ids = scope["flat_ids"]

        # Projects
        if project_ids:
            projects_q = await db.execute(
                select(Project).where(
                    Project.updated_at >= last_synced_at,
                    Project.id.in_(project_ids),
                )
            )
        else:
            projects_q = await db.execute(select(Project).where(False))
        projects = projects_q.scalars().all()

        # Buildings
        if building_ids:
            buildings_q = await db.execute(
                select(Building).where(
                    Building.updated_at >= last_synced_at,
                    Building.id.in_(building_ids),
                )
            )
        else:
            buildings_q = await db.execute(select(Building).where(False))
        buildings = buildings_q.scalars().all()

        # Floors
        if floor_ids:
            floors_q = await db.execute(
                select(Floor).where(
                    Floor.updated_at >= last_synced_at,
                    Floor.id.in_(floor_ids),
                )
            )
        else:
            floors_q = await db.execute(select(Floor).where(False))
        floors = floors_q.scalars().all()

        # Flats
        if flat_ids:
            flats_q = await db.execute(
                select(Flat).where(
                    Flat.updated_at >= last_synced_at,
                    Flat.id.in_(flat_ids),
                )
            )
        else:
            flats_q = await db.execute(select(Flat).where(False))
        flats = flats_q.scalars().all()

        # Inspection entries for accessible flats
        if flat_ids:
            entries_q = await db.execute(
                select(InspectionEntry)
                .options(
                    selectinload(InspectionEntry.images),
                    selectinload(InspectionEntry.voice_notes),
                    selectinload(InspectionEntry.videos),
                )
                .where(
                    InspectionEntry.updated_at >= last_synced_at,
                    InspectionEntry.flat_id.in_(flat_ids),
                )
            )
        else:
            entries_q = await db.execute(select(InspectionEntry).where(False))
        entries = entries_q.scalars().all()

        # Global data
        contractors_q = await db.execute(
            select(Contractor).where(Contractor.updated_at >= last_synced_at)
        )
        contractors = contractors_q.scalars().all()

        templates_q = await db.execute(
            select(ChecklistTemplate).where(ChecklistTemplate.updated_at >= last_synced_at)
        )
        templates = templates_q.scalars().all()

        ftr_q = await db.execute(select(FlatTypeRoom))
        flat_type_rooms = ftr_q.scalars().all()

        fpl_q = await db.execute(select(FloorPlanLayout))
        floor_plan_layouts = fpl_q.scalars().all()

        server_time = datetime.now(timezone.utc).isoformat()

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
            "server_time": server_time,
        }


sync_service = SyncService()
