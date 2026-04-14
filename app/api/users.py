import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_manager
from app.models.user import User, UserProjectAssignment
from app.models.project import Project
from app.schemas.auth import UserCreate, UserResponse, UserUpdate
from app.services.auth_service import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserResponse]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    # Check for duplicate username
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
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    result = await db.execute(select(User).where(User.id == user_id))
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
    return UserResponse.model_validate(user)


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
    # Validate user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    if not user_result.scalars().first():
        raise HTTPException(status_code=404, detail="User not found")

    # Validate project exists
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    if not proj_result.scalars().first():
        raise HTTPException(status_code=404, detail="Project not found")

    # Check duplicate
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
