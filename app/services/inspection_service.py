import logging
import uuid
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import ChecklistTemplate, FlatTypeRoom
from app.models.flat import Flat
from app.models.inspection import InspectionEntry

logger = logging.getLogger(__name__)


async def recompute_flat_inspection_status(
    flat_id: uuid.UUID,
    db: AsyncSession,
) -> str:
    """
    Compute and persist the correct inspection_status for a flat based on its entries.

    Rules:
    - NOT_STARTED: no entries exist, or all entries have status = 'NA'
    - IN_PROGRESS: at least one entry has status != 'NA', but not all
    - COMPLETED: all entries have status != 'NA' (and at least one entry exists)

    Does NOT commit or flush — caller controls the transaction boundary.
    Returns the new status string.
    """
    total = await db.scalar(
        select(func.count(InspectionEntry.id)).where(InspectionEntry.flat_id == flat_id)
    )
    inspected = await db.scalar(
        select(func.count(InspectionEntry.id)).where(
            InspectionEntry.flat_id == flat_id,
            InspectionEntry.status != "NA",
        )
    )

    total = total or 0
    inspected = inspected or 0

    if total == 0 or inspected == 0:
        status = "NOT_STARTED"
    elif inspected >= total:
        status = "COMPLETED"
    else:
        status = "IN_PROGRESS"

    result = await db.execute(select(Flat).where(Flat.id == flat_id))
    flat = result.scalars().first()
    if flat:
        flat.inspection_status = status

    return status


async def initialize_flat_checklist(
    flat_id: uuid.UUID,
    db: AsyncSession,
    inspector_id: Optional[uuid.UUID] = None,
) -> list[InspectionEntry]:
    """
    Instantiate inspection entries for a flat from its flat_type's templates.

    Idempotent: returns [] without creating anything if the flat already has entries
    or if no rooms/templates exist for the flat's type. Safe to call repeatedly.

    Does NOT commit — caller controls the transaction.
    Caller is responsible for calling recompute_flat_inspection_status if desired.
    """
    existing = await db.scalar(
        select(func.count(InspectionEntry.id)).where(InspectionEntry.flat_id == flat_id)
    )
    if (existing or 0) > 0:
        return []

    flat = await db.scalar(select(Flat).where(Flat.id == flat_id))
    if not flat:
        return []

    rooms_result = await db.execute(
        select(FlatTypeRoom)
        .where(FlatTypeRoom.flat_type == flat.flat_type)
        .order_by(FlatTypeRoom.sort_order)
    )
    rooms = rooms_result.scalars().all()

    created: list[InspectionEntry] = []
    for room in rooms:
        templates_result = await db.execute(
            select(ChecklistTemplate)
            .where(
                ChecklistTemplate.room_type == room.room_type,
                ChecklistTemplate.is_active == True,  # noqa: E712
            )
            .order_by(ChecklistTemplate.sort_order)
        )
        templates = templates_result.scalars().all()

        for template in templates:
            entry = InspectionEntry(
                flat_id=flat.id,
                room_label=room.label,
                category=template.category,
                item_name=template.item_name,
                status="NA",
                snag_fix_status="OPEN",
                inspector_id=inspector_id,
            )
            db.add(entry)
            created.append(entry)

    return created


async def backfill_uninitialized_flats(db: AsyncSession) -> int:
    """
    Find every flat with zero inspection entries and initialize its checklist.

    Runs on startup to cover flats created before auto-init-on-create existed.
    Idempotent — safe to run every boot. Returns count of flats initialized.
    """
    subq = select(InspectionEntry.flat_id).distinct()
    result = await db.execute(select(Flat.id).where(Flat.id.notin_(subq)))
    flat_ids = [row[0] for row in result.all()]

    init_count = 0
    for flat_id in flat_ids:
        created = await initialize_flat_checklist(flat_id, db)
        if created:
            await recompute_flat_inspection_status(flat_id, db)
            init_count += 1

    if init_count > 0:
        await db.commit()
        logger.info("Backfill: initialized checklists for %d flats", init_count)

    return init_count
