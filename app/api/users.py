import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_manager
from app.models.user import User, UserProjectAssignment, UserBuildingAssignment, UserFlatAssignment
from app.models.project import Project
from app.models.building import Building
from app.models.floor import Floor
from app.models.flat import Flat
from app.schemas.auth import (
    ScopedBuilding,
    ScopedFlat,
    ScopedProject,
    UserCreate,
    UserResponse,
    UserScopeDetails,
    UserUpdate,
)
from app.services.auth_service import hash_password
from app.services.event_service import event_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


async def _notify_assignment_changed(
    user_id: uuid.UUID, action: str, level: str, entity_id: uuid.UUID,
) -> None:
    """Best-effort SSE notification for assignment changes."""
    try:
        await event_service.notify({
            "event_type": "assignment_changed",
            "user_id": str(user_id),
            "action": action,
            "level": level,
            "entity_id": str(entity_id),
        })
    except Exception:
        logger.exception("Failed to notify assignment change")


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


@router.get("/{user_id}/scope-details", response_model=UserScopeDetails)
async def get_user_scope_details(
    user_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserScopeDetails:
    """Return the user's direct assignments with resolved names.

    "Direct" means each list shows only explicitly-created assignments at
    that level — a building under a project-level assignment won't show up
    in `buildings` unless it was also assigned individually. This lets the
    portal render a clean list of what the manager actually chose.
    """
    user_result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user_id)
    )
    user = user_result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    project_ids = [a.project_id for a in user.project_assignments]
    building_ids = [a.building_id for a in user.building_assignments]
    flat_ids = [a.flat_id for a in user.flat_assignments]

    projects: list[ScopedProject] = []
    if project_ids:
        proj_rows = (
            await db.execute(
                select(
                    Project.id,
                    Project.name,
                    Project.location,
                    func.count(distinct(Building.id)).label("total_buildings"),
                    func.count(distinct(Flat.id)).label("total_flats"),
                )
                .outerjoin(Building, Building.project_id == Project.id)
                .outerjoin(Floor, Floor.building_id == Building.id)
                .outerjoin(Flat, Flat.floor_id == Floor.id)
                .where(Project.id.in_(project_ids))
                .group_by(Project.id, Project.name, Project.location)
                .order_by(Project.name)
            )
        ).all()
        projects = [
            ScopedProject(
                project_id=r.id,
                project_name=r.name,
                location=r.location or "",
                total_buildings=r.total_buildings,
                total_flats=r.total_flats,
            )
            for r in proj_rows
        ]

    buildings: list[ScopedBuilding] = []
    if building_ids:
        bld_rows = (
            await db.execute(
                select(
                    Building.id,
                    Building.name,
                    Project.id.label("project_id"),
                    Project.name.label("project_name"),
                    func.count(distinct(Floor.id)).label("total_floors"),
                    func.count(distinct(Flat.id)).label("total_flats"),
                )
                .join(Project, Project.id == Building.project_id)
                .outerjoin(Floor, Floor.building_id == Building.id)
                .outerjoin(Flat, Flat.floor_id == Floor.id)
                .where(Building.id.in_(building_ids))
                .group_by(Building.id, Building.name, Project.id, Project.name)
                .order_by(Project.name, Building.name)
            )
        ).all()
        buildings = [
            ScopedBuilding(
                building_id=r.id,
                building_name=r.name,
                project_id=r.project_id,
                project_name=r.project_name,
                total_floors=r.total_floors,
                total_flats=r.total_flats,
            )
            for r in bld_rows
        ]

    flats: list[ScopedFlat] = []
    if flat_ids:
        flat_rows = (
            await db.execute(
                select(
                    Flat.id,
                    Flat.flat_number,
                    Flat.flat_type,
                    Floor.id.label("floor_id"),
                    Floor.floor_number,
                    Building.id.label("building_id"),
                    Building.name.label("building_name"),
                    Project.id.label("project_id"),
                    Project.name.label("project_name"),
                )
                .join(Floor, Floor.id == Flat.floor_id)
                .join(Building, Building.id == Floor.building_id)
                .join(Project, Project.id == Building.project_id)
                .where(Flat.id.in_(flat_ids))
                .order_by(
                    Project.name,
                    Building.name,
                    Floor.floor_number,
                    Flat.flat_number,
                )
            )
        ).all()
        flats = [
            ScopedFlat(
                flat_id=r.id,
                flat_number=r.flat_number,
                flat_type=r.flat_type,
                floor_id=r.floor_id,
                floor_number=r.floor_number,
                floor_label=f"Floor {r.floor_number}",
                building_id=r.building_id,
                building_name=r.building_name,
                project_id=r.project_id,
                project_name=r.project_name,
            )
            for r in flat_rows
        ]

    return UserScopeDetails(
        user_id=user.id,
        role=user.role,
        projects=projects,
        buildings=buildings,
        flats=flats,
    )


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
    await _notify_assignment_changed(user_id, "assigned", "project", project_id)
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
    await _notify_assignment_changed(user_id, "unassigned", "project", project_id)
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
    await _notify_assignment_changed(user_id, "assigned", "building", building_id)
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
    await _notify_assignment_changed(user_id, "unassigned", "building", building_id)
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
    await _notify_assignment_changed(user_id, "assigned", "flat", flat_id)
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
    await _notify_assignment_changed(user_id, "unassigned", "flat", flat_id)
    return {"detail": "Flat unassigned"}
