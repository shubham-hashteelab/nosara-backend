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

logger = logging.getLogger(__name__)

# Maps entity_type string to SQLAlchemy model
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
        """Process a list of sync push operations from a client."""
        accepted: list[str] = []
        rejected: list[SyncRejection] = []

        for op in operations:
            try:
                model = ENTITY_MODEL_MAP.get(op.entity_type)
                if model is None:
                    rejected.append(
                        SyncRejection(
                            id=op.entity_id,
                            reason=f"Unknown entity type: {op.entity_type}",
                        )
                    )
                    continue

                if op.operation == "CREATE":
                    data = dict(op.data)
                    data["id"] = op.entity_id
                    if op.entity_type == "inspection_entry":
                        data["inspector_id"] = inspector_id
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
                        if hasattr(obj, key):
                            setattr(obj, key, value)
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
                rejected.append(
                    SyncRejection(id=op.entity_id, reason=str(exc))
                )

        await db.commit()
        return accepted, rejected

    async def _resolve_accessible_flat_ids(
        self,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> set[uuid.UUID] | None:
        """
        Resolve the set of flat IDs a user can access based on granular assignments.
        Returns None if the user has project-level access (= all flats in those projects).
        Returns a set of specific flat IDs if building or flat level assignments exist.

        Priority: flat assignments > building assignments > project assignments.
        If ANY flat assignments exist, only those flats are visible.
        If ANY building assignments exist (but no flat assignments), those buildings' flats.
        Otherwise, project-level access returns None (handled by caller).
        """
        # Check flat-level assignments
        flat_assign_q = await db.execute(
            select(UserFlatAssignment.flat_id).where(
                UserFlatAssignment.user_id == user_id
            )
        )
        flat_ids = {row[0] for row in flat_assign_q.all()}
        if flat_ids:
            return flat_ids

        # Check building-level assignments
        bldg_assign_q = await db.execute(
            select(UserBuildingAssignment.building_id).where(
                UserBuildingAssignment.user_id == user_id
            )
        )
        building_ids = {row[0] for row in bldg_assign_q.all()}
        if building_ids:
            # Get all flats in those buildings
            flats_q = await db.execute(
                select(Flat.id)
                .join(Floor, Flat.floor_id == Floor.id)
                .where(Floor.building_id.in_(building_ids))
            )
            return {row[0] for row in flats_q.all()}

        # No granular assignments — return None to signal project-level access
        return None

    async def process_pull(
        self,
        last_synced_at: datetime,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pull all data updated since last_synced_at for the user's accessible scope."""
        # Get user's assigned project IDs (always needed as the base scope)
        assignment_result = await db.execute(
            select(UserProjectAssignment.project_id).where(
                UserProjectAssignment.user_id == user_id
            )
        )
        project_ids = [row[0] for row in assignment_result.all()]

        # Check for granular (building/flat) access
        specific_flat_ids = await self._resolve_accessible_flat_ids(user_id, db)

        # Projects — always filtered by project assignments
        projects_q = await db.execute(
            select(Project).where(
                Project.updated_at >= last_synced_at,
                Project.id.in_(project_ids) if project_ids else False,
            )
        )
        projects = projects_q.scalars().all()

        # Buildings — filter by project, then optionally by building assignments
        bldg_assign_q = await db.execute(
            select(UserBuildingAssignment.building_id).where(
                UserBuildingAssignment.user_id == user_id
            )
        )
        assigned_building_ids = {row[0] for row in bldg_assign_q.all()}

        buildings_q_stmt = select(Building).where(
            Building.updated_at >= last_synced_at,
            Building.project_id.in_(project_ids) if project_ids else False,
        )
        if assigned_building_ids:
            buildings_q_stmt = buildings_q_stmt.where(
                Building.id.in_(assigned_building_ids)
            )
        buildings_q = await db.execute(buildings_q_stmt)
        buildings = buildings_q.scalars().all()

        # Floors
        building_ids_in_scope = [b.id for b in buildings]
        if building_ids_in_scope:
            floors_q = await db.execute(
                select(Floor).where(
                    Floor.updated_at >= last_synced_at,
                    Floor.building_id.in_(building_ids_in_scope),
                )
            )
        else:
            floors_q = await db.execute(
                select(Floor).where(False)
            )
        floors = floors_q.scalars().all()

        # Flats — apply granular filter if exists
        floor_ids_in_scope = [f.id for f in floors]
        if specific_flat_ids is not None:
            # Granular: only specific flats
            flats_q = await db.execute(
                select(Flat).where(
                    Flat.updated_at >= last_synced_at,
                    Flat.id.in_(specific_flat_ids) if specific_flat_ids else False,
                )
            )
        elif floor_ids_in_scope:
            flats_q = await db.execute(
                select(Flat).where(
                    Flat.updated_at >= last_synced_at,
                    Flat.floor_id.in_(floor_ids_in_scope),
                )
            )
        else:
            flats_q = await db.execute(select(Flat).where(False))
        flats = flats_q.scalars().all()

        # Inspection entries — only for accessible flats
        flat_ids_in_scope = [f.id for f in flats]
        if flat_ids_in_scope:
            entries_q = await db.execute(
                select(InspectionEntry)
                .options(
                    selectinload(InspectionEntry.images),
                    selectinload(InspectionEntry.voice_notes),
                    selectinload(InspectionEntry.videos),
                )
                .where(
                    InspectionEntry.updated_at >= last_synced_at,
                    InspectionEntry.flat_id.in_(flat_ids_in_scope),
                )
            )
        else:
            entries_q = await db.execute(
                select(InspectionEntry).where(False)
            )
        entries = entries_q.scalars().all()

        # Global data (not scoped by assignments)
        contractors_q = await db.execute(
            select(Contractor).where(Contractor.updated_at >= last_synced_at)
        )
        contractors = contractors_q.scalars().all()

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
