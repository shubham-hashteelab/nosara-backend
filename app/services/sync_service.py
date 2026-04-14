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
from app.models.user import UserProjectAssignment
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
                    # For inspection entries, attach the inspector
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

    async def process_pull(
        self,
        last_synced_at: datetime,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pull all data updated since last_synced_at for the user's assigned projects."""
        # Get user's assigned project IDs
        assignment_result = await db.execute(
            select(UserProjectAssignment.project_id).where(
                UserProjectAssignment.user_id == user_id
            )
        )
        project_ids = [row[0] for row in assignment_result.all()]

        # Projects
        projects_q = await db.execute(
            select(Project).where(
                Project.updated_at >= last_synced_at,
                Project.id.in_(project_ids) if project_ids else True,
            )
        )
        projects = projects_q.scalars().all()

        # Buildings
        buildings_q = await db.execute(
            select(Building).where(
                Building.updated_at >= last_synced_at,
                Building.project_id.in_(project_ids) if project_ids else True,
            )
        )
        buildings = buildings_q.scalars().all()

        # Floors via buildings
        building_ids = [b.id for b in buildings]
        if building_ids:
            floors_q = await db.execute(
                select(Floor).where(Floor.updated_at >= last_synced_at)
            )
        else:
            floors_q = await db.execute(
                select(Floor).where(Floor.updated_at >= last_synced_at)
            )
        floors = floors_q.scalars().all()

        # Flats
        flats_q = await db.execute(
            select(Flat).where(Flat.updated_at >= last_synced_at)
        )
        flats = flats_q.scalars().all()

        # Inspection entries
        entries_q = await db.execute(
            select(InspectionEntry)
            .options(
                selectinload(InspectionEntry.images),
                selectinload(InspectionEntry.voice_notes),
                selectinload(InspectionEntry.videos),
            )
            .where(InspectionEntry.updated_at >= last_synced_at)
        )
        entries = entries_q.scalars().all()

        # Contractors (global — not project-scoped)
        contractors_q = await db.execute(
            select(Contractor).where(Contractor.updated_at >= last_synced_at)
        )
        contractors = contractors_q.scalars().all()

        # Checklist templates (global + project-scoped)
        templates_q = await db.execute(
            select(ChecklistTemplate).where(
                ChecklistTemplate.updated_at >= last_synced_at
            )
        )
        templates = templates_q.scalars().all()

        # Flat type rooms
        ftr_q = await db.execute(select(FlatTypeRoom))
        flat_type_rooms = ftr_q.scalars().all()

        # Floor plan layouts
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
