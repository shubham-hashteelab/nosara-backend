import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_manager
from app.models.user import User, UserProjectAssignment, UserBuildingAssignment, UserFlatAssignment
from app.models.project import Project
from app.models.building import Building
from app.models.flat import Flat
from app.schemas.auth import UserCreate, UserResponse, UserUpdate
from app.services.auth_service import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _user_to_response(user: User) -> UserResponse:
    """Convert User ORM object to response with assignment IDs."""
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        assigned_project_ids=[a.project_id for a in user.project_assignments],
        assigned_building_ids=[a.building_id for a in user.building_assignments],
        assigned_flat_ids=[a.flat_id for a in user.flat_assignments],
    )


def _load_assignments():
    """Eager-load all assignment relationships."""
    return [
        selectinload(User.project_assignments),
        selectinload(User.building_assignments),
        selectinload(User.flat_assignments),
    ]


@router.get("", response_model=list[UserResponse])
async def list_users(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserResponse]:
    result = await db.execute(
        select(User).options(*_load_assignments()).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    return [_user_to_response(u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    existing = await db.execute(
        select(User).where(User.username == body.username)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        )

    if body.role not in ("MANAGER", "INSPECTOR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be MANAGER or INSPECTOR",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    await db.commit()

    result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user.id)
    )
    user = result.scalars().first()
    return _user_to_response(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user_id)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user_id)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.commit()
    await db.refresh(user)
    result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user.id)
    )
    user = result.scalars().first()
    return _user_to_response(user)


# ---------------------------------------------------------------------------
# Project assignments
# ---------------------------------------------------------------------------

@router.post(
    "/{user_id}/assign-project/{project_id}",
    status_code=status.HTTP_201_CREATED,
)
async def assign_project(
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")

    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    if not proj_result.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    existing = await db.execute(
        select(UserProjectAssignment).where(
            UserProjectAssignment.user_id == user_id,
            UserProjectAssignment.project_id == project_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment already exists",
        )

    assignment = UserProjectAssignment(user_id=user_id, project_id=project_id)
    db.add(assignment)
    await db.commit()
    return {"detail": "Project assigned"}


@router.delete("/{user_id}/assign-project/{project_id}")
async def unassign_project(
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(UserProjectAssignment).where(
            UserProjectAssignment.user_id == user_id,
            UserProjectAssignment.project_id == project_id,
        )
    )
    assignment = result.scalars().first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    await db.commit()
    return {"detail": "Project unassigned"}


# ---------------------------------------------------------------------------
# Building (tower) assignments
# ---------------------------------------------------------------------------

@router.post(
    "/{user_id}/assign-building/{building_id}",
    status_code=status.HTTP_201_CREATED,
)
async def assign_building(
    user_id: uuid.UUID,
    building_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")

    bldg_result = await db.execute(select(Building).where(Building.id == building_id))
    if not bldg_result.scalars().first():
        raise HTTPException(status_code=404, detail="Building not found")

    existing = await db.execute(
        select(UserBuildingAssignment).where(
            UserBuildingAssignment.user_id == user_id,
            UserBuildingAssignment.building_id == building_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment already exists",
        )

    assignment = UserBuildingAssignment(user_id=user_id, building_id=building_id)
    db.add(assignment)
    await db.commit()
    return {"detail": "Building assigned"}


@router.delete("/{user_id}/assign-building/{building_id}")
async def unassign_building(
    user_id: uuid.UUID,
    building_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(UserBuildingAssignment).where(
            UserBuildingAssignment.user_id == user_id,
            UserBuildingAssignment.building_id == building_id,
        )
    )
    assignment = result.scalars().first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    await db.commit()
    return {"detail": "Building unassigned"}


# ---------------------------------------------------------------------------
# Flat assignments
# ---------------------------------------------------------------------------

@router.post(
    "/{user_id}/assign-flat/{flat_id}",
    status_code=status.HTTP_201_CREATED,
)
async def assign_flat(
    user_id: uuid.UUID,
    flat_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")

    flat_result = await db.execute(select(Flat).where(Flat.id == flat_id))
    if not flat_result.scalars().first():
        raise HTTPException(status_code=404, detail="Flat not found")

    existing = await db.execute(
        select(UserFlatAssignment).where(
            UserFlatAssignment.user_id == user_id,
            UserFlatAssignment.flat_id == flat_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assignment already exists",
        )

    assignment = UserFlatAssignment(user_id=user_id, flat_id=flat_id)
    db.add(assignment)
    await db.commit()
    return {"detail": "Flat assigned"}


@router.delete("/{user_id}/assign-flat/{flat_id}")
async def unassign_flat(
    user_id: uuid.UUID,
    flat_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(UserFlatAssignment).where(
            UserFlatAssignment.user_id == user_id,
            UserFlatAssignment.flat_id == flat_id,
        )
    )
    assignment = result.scalars().first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    await db.commit()
    return {"detail": "Flat unassigned"}
