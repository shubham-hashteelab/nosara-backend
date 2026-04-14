import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.building import Building
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.user import User
from app.schemas.floor import FloorCreate, FloorResponse, FloorUpdate

router = APIRouter(tags=["floors"])


async def _enrich_floor(floor: Floor, db: AsyncSession) -> dict:
    """Add computed counts and label to a floor."""
    flat_count = await db.scalar(
        select(func.count()).where(Flat.floor_id == floor.id)
    )
    data = {c.key: getattr(floor, c.key) for c in floor.__table__.columns}
    data["total_flats"] = flat_count or 0
    data["label"] = f"Floor {floor.floor_number}"
    return data


@router.get(
    "/buildings/{building_id}/floors", response_model=list[FloorResponse]
)
async def list_floors(
    building_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FloorResponse]:
    result = await db.execute(
        select(Floor)
        .where(Floor.building_id == building_id)
        .order_by(Floor.floor_number)
    )
    floors = result.scalars().all()
    return [FloorResponse(**await _enrich_floor(f, db)) for f in floors]


@router.get("/floors/{floor_id}", response_model=FloorResponse)
async def get_floor(
    floor_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FloorResponse:
    result = await db.execute(select(Floor).where(Floor.id == floor_id))
    floor = result.scalars().first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    return FloorResponse(**await _enrich_floor(floor, db))


@router.post(
    "/buildings/{building_id}/floors",
    response_model=FloorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_floor(
    building_id: uuid.UUID,
    body: FloorCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FloorResponse:
    bld = await db.execute(select(Building).where(Building.id == building_id))
    if not bld.scalars().first():
        raise HTTPException(status_code=404, detail="Building not found")

    floor = Floor(building_id=building_id, floor_number=body.floor_number)
    db.add(floor)
    await db.commit()
    await db.refresh(floor)
    return FloorResponse(**await _enrich_floor(floor, db))


@router.patch("/floors/{floor_id}", response_model=FloorResponse)
async def update_floor(
    floor_id: uuid.UUID,
    body: FloorUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FloorResponse:
    result = await db.execute(select(Floor).where(Floor.id == floor_id))
    floor = result.scalars().first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    if body.floor_number is not None:
        floor.floor_number = body.floor_number

    await db.commit()
    await db.refresh(floor)
    return FloorResponse(**await _enrich_floor(floor, db))


@router.delete("/floors/{floor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_floor(
    floor_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(select(Floor).where(Floor.id == floor_id))
    floor = result.scalars().first()
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")

    await db.delete(floor)
    await db.commit()
