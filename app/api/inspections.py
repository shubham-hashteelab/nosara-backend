import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.models.building import Building
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry
from app.models.user import User
from app.schemas.inspection import (
    InspectionEntryCreate,
    InspectionEntryResponse,
    InspectionEntryUpdate,
)
from app.services.inspection_service import (
    initialize_flat_checklist,
    recompute_flat_inspection_status,
)

router = APIRouter(tags=["inspections"])


@router.get("/entries/snags", response_model=list[InspectionEntryResponse])
async def list_snag_entries(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    snag_fix_status: Annotated[str | None, Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[InspectionEntryResponse]:
    """
    Cross-project list of snag entries (status == 'FAIL') with optional filters.
    Powers the portal's Inspections page.
    """
    stmt = (
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.status == "FAIL")
    )

    # Filtering by project requires joining through flat → floor → building.
    if project_id is not None:
        stmt = (
            stmt.join(Flat, Flat.id == InspectionEntry.flat_id)
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
            .where(Building.project_id == project_id)
        )

    if severity:
        stmt = stmt.where(InspectionEntry.severity == severity)
    if category:
        stmt = stmt.where(InspectionEntry.category == category)
    if snag_fix_status:
        stmt = stmt.where(InspectionEntry.snag_fix_status == snag_fix_status)

    stmt = stmt.order_by(InspectionEntry.updated_at.desc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [InspectionEntryResponse.model_validate(e) for e in entries]


@router.get(
    "/flats/{flat_id}/entries", response_model=list[InspectionEntryResponse]
)
async def list_entries(
    flat_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[InspectionEntryResponse]:
    result = await db.execute(
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.flat_id == flat_id)
        .order_by(InspectionEntry.room_label, InspectionEntry.category)
    )
    entries = result.scalars().all()
    return [InspectionEntryResponse.model_validate(e) for e in entries]


@router.post(
    "/flats/{flat_id}/entries",
    response_model=InspectionEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    flat_id: uuid.UUID,
    body: InspectionEntryCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    flat_result = await db.execute(select(Flat).where(Flat.id == flat_id))
    if not flat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Flat not found")

    entry = InspectionEntry(
        flat_id=flat_id,
        room_label=body.room_label,
        category=body.category,
        item_name=body.item_name,
        status=body.status,
        severity=body.severity,
        notes=body.notes,
        snag_fix_status=body.snag_fix_status,
        inspector_id=current_user.id,
    )
    db.add(entry)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.id == entry.id)
    )
    entry = result.scalars().first()
    return InspectionEntryResponse.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=InspectionEntryResponse)
async def get_entry(
    entry_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    result = await db.execute(
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.id == entry_id)
    )
    entry = result.scalars().first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return InspectionEntryResponse.model_validate(entry)


@router.patch("/entries/{entry_id}", response_model=InspectionEntryResponse)
async def update_entry(
    entry_id: uuid.UUID,
    body: InspectionEntryUpdate,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    result = await db.execute(
        select(InspectionEntry).where(InspectionEntry.id == entry_id)
    )
    entry = result.scalars().first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    for field in ("status", "severity", "notes", "snag_fix_status", "room_label", "category", "item_name"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(entry, field, value)

    await db.commit()

    # Recompute flat inspection status after entry update
    await recompute_flat_inspection_status(entry.flat_id, db)
    await db.commit()

    # Reload with relationships
    result = await db.execute(
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.id == entry.id)
    )
    entry = result.scalars().first()
    return InspectionEntryResponse.model_validate(entry)


@router.post(
    "/entries/{entry_id}/initialize-checklist",
    response_model=list[InspectionEntryResponse],
    status_code=status.HTTP_201_CREATED,
)
async def initialize_checklist(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[InspectionEntryResponse]:
    """
    Legacy endpoint — flats now auto-initialize on creation. Retained as an
    idempotent fallback: instantiates entries from the flat's templates if
    the flat somehow has none, otherwise returns the existing entries.

    The path param is historically named entry_id but is actually the flat_id.
    """
    flat_id = entry_id
    flat = await db.scalar(select(Flat).where(Flat.id == flat_id))
    if not flat:
        raise HTTPException(status_code=404, detail="Flat not found")

    created = await initialize_flat_checklist(
        flat_id, db, inspector_id=current_user.id
    )
    if created:
        await recompute_flat_inspection_status(flat_id, db)
    await db.commit()

    result = await db.execute(
        select(InspectionEntry)
        .options(
            selectinload(InspectionEntry.images),
            selectinload(InspectionEntry.voice_notes),
            selectinload(InspectionEntry.videos),
        )
        .where(InspectionEntry.flat_id == flat_id)
        .order_by(InspectionEntry.room_label, InspectionEntry.category)
    )
    entries = list(result.scalars().all())
    return [InspectionEntryResponse.model_validate(e) for e in entries]
