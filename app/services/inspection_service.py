import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flat import Flat
from app.models.inspection import InspectionEntry


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
