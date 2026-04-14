import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.models.checklist import ChecklistTemplate, FlatTypeRoom
from app.models.flat import Flat
from app.models.inspection import InspectionEntry
from app.models.user import User
from app.schemas.inspection import (
    InspectionEntryCreate,
    InspectionEntryResponse,
    InspectionEntryUpdate,
)

router = APIRouter(tags=["inspections"])


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
    Create inspection entries from checklist templates for the flat's type.
    entry_id here is actually the flat_id — the endpoint creates entries for the flat.
    We use the flat to determine flat_type, look up rooms for that type, then
    create entries from matching checklist templates.
    """
    flat_id = entry_id  # Reinterpret: the path param is the flat_id
    flat_result = await db.execute(select(Flat).where(Flat.id == flat_id))
    flat = flat_result.scalars().first()
    if not flat:
        raise HTTPException(status_code=404, detail="Flat not found")

    # Get rooms for this flat type
    rooms_result = await db.execute(
        select(FlatTypeRoom)
        .where(FlatTypeRoom.flat_type == flat.flat_type)
        .order_by(FlatTypeRoom.sort_order)
    )
    rooms = rooms_result.scalars().all()

    if not rooms:
        raise HTTPException(
            status_code=404,
            detail=f"No room definitions found for flat type {flat.flat_type}",
        )

    created_entries: list[InspectionEntry] = []

    for room in rooms:
        # Get checklist templates for this room type
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
                inspector_id=current_user.id,
            )
            db.add(entry)
            created_entries.append(entry)

    await db.commit()

    # Reload all with relationships
    entry_ids = [e.id for e in created_entries]
    if entry_ids:
        result = await db.execute(
            select(InspectionEntry)
            .options(
                selectinload(InspectionEntry.images),
                selectinload(InspectionEntry.voice_notes),
                selectinload(InspectionEntry.videos),
            )
            .where(InspectionEntry.id.in_(entry_ids))
        )
        created_entries = list(result.scalars().all())

    # Update flat status
    flat.inspection_status = "IN_PROGRESS"
    await db.commit()

    return [InspectionEntryResponse.model_validate(e) for e in created_entries]
