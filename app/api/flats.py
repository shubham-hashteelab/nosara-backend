import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.user import User
from app.schemas.flat import FlatCreate, FlatResponse, FlatUpdate

router = APIRouter(tags=["flats"])


@router.get("/floors/{floor_id}/flats", response_model=list[FlatResponse])
async def list_flats(
    floor_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FlatResponse]:
    result = await db.execute(
        select(Flat).where(Flat.floor_id == floor_id).order_by(Flat.flat_number)
    )
    flats = result.scalars().all()
    return [FlatResponse.model_validate(f) for f in flats]


@router.get("/flats/{flat_id}", response_model=FlatResponse)
async def get_flat(
    flat_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlatResponse:
    result = await db.execute(select(Flat).where(Flat.id == flat_id))
    flat = result.scalars().first()
    if not flat:
        raise HTTPException(status_code=404, detail="Flat not found")
    return FlatResponse.model_validate(flat)


@router.post(
    "/floors/{floor_id}/flats",
    response_model=FlatResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_flat(
    floor_id: uuid.UUID,
    body: FlatCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlatResponse:
    flr = await db.execute(select(Floor).where(Floor.id == floor_id))
    if not flr.scalars().first():
        raise HTTPException(status_code=404, detail="Floor not found")

    flat = Flat(
        floor_id=floor_id,
        flat_number=body.flat_number,
        flat_type=body.flat_type,
    )
    db.add(flat)
    await db.commit()
    await db.refresh(flat)
    return FlatResponse.model_validate(flat)


@router.patch("/flats/{flat_id}", response_model=FlatResponse)
async def update_flat(
    flat_id: uuid.UUID,
    body: FlatUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlatResponse:
    result = await db.execute(select(Flat).where(Flat.id == flat_id))
    flat = result.scalars().first()
    if not flat:
        raise HTTPException(status_code=404, detail="Flat not found")

    if body.flat_number is not None:
        flat.flat_number = body.flat_number
    if body.flat_type is not None:
        flat.flat_type = body.flat_type
    if body.inspection_status is not None:
        flat.inspection_status = body.inspection_status

    await db.commit()
    await db.refresh(flat)
    return FlatResponse.model_validate(flat)


@router.delete("/flats/{flat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flat(
    flat_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(select(Flat).where(Flat.id == flat_id))
    flat = result.scalars().first()
    if not flat:
        raise HTTPException(status_code=404, detail="Flat not found")

    await db.delete(flat)
    await db.commit()
