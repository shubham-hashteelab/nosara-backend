import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.building import Building
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.project import Project
from app.models.user import User
from app.schemas.building import BuildingCreate, BuildingResponse, BuildingUpdate

router = APIRouter(tags=["buildings"])


async def _enrich_building(building: Building, db: AsyncSession) -> dict:
    """Add computed counts to a building."""
    floor_count = await db.scalar(
        select(func.count()).where(Floor.building_id == building.id)
    )
    flat_count = await db.scalar(
        select(func.count())
        .select_from(Flat)
        .join(Floor, Flat.floor_id == Floor.id)
        .where(Floor.building_id == building.id)
    )
    data = {c.key: getattr(building, c.key) for c in building.__table__.columns}
    data["total_floors"] = floor_count or 0
    data["total_flats"] = flat_count or 0
    return data


@router.get(
    "/projects/{project_id}/buildings", response_model=list[BuildingResponse]
)
async def list_buildings(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[BuildingResponse]:
    result = await db.execute(
        select(Building)
        .where(Building.project_id == project_id)
        .order_by(Building.name)
    )
    buildings = result.scalars().all()
    return [BuildingResponse(**await _enrich_building(b, db)) for b in buildings]


@router.get("/buildings/{building_id}", response_model=BuildingResponse)
async def get_building(
    building_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BuildingResponse:
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalars().first()
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")
    return BuildingResponse(**await _enrich_building(building, db))


@router.post(
    "/projects/{project_id}/buildings",
    response_model=BuildingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_building(
    project_id: uuid.UUID,
    body: BuildingCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BuildingResponse:
    proj = await db.execute(select(Project).where(Project.id == project_id))
    if not proj.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    building = Building(project_id=project_id, name=body.name)
    db.add(building)
    await db.commit()
    await db.refresh(building)
    return BuildingResponse(**await _enrich_building(building, db))


@router.patch("/buildings/{building_id}", response_model=BuildingResponse)
async def update_building(
    building_id: uuid.UUID,
    body: BuildingUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BuildingResponse:
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalars().first()
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    if body.name is not None:
        building.name = body.name

    await db.commit()
    await db.refresh(building)
    return BuildingResponse(**await _enrich_building(building, db))


@router.delete("/buildings/{building_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_building(
    building_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(select(Building).where(Building.id == building_id))
    building = result.scalars().first()
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    await db.delete(building)
    await db.commit()
