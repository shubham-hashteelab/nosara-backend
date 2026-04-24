import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_manager
from app.constants.trades import VALID_TRADES, is_valid_trade
from app.models.contractor import SnagContractorAssignment
from app.models.inspection import InspectionEntry
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
        email=user.email,
        phone=user.phone,
        company=user.company,
        trades=user.trades,
        assigned_project_ids=[a.project_id for a in user.project_assignments],
        assigned_building_ids=[a.building_id for a in user.building_assignments],
        assigned_flat_ids=[a.flat_id for a in user.flat_assignments],
    )


def _validate_trades(trades: list[str]) -> None:
    """Validate every trade value against the taxonomy. Raises 400 on mismatch."""
    for t in trades:
        if not is_valid_trade(t):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid trade: {t}. Valid trades: {sorted(VALID_TRADES)}",
            )


def _load_assignments():
    """Eager-load all assignment relationships."""
    return [
        selectinload(User.project_assignments),
        selectinload(User.building_assignments),
        selectinload(User.flat_assignments),
    ]


# ---------------------------------------------------------------------------
# Exclusive-assignment helpers
#
# Policy: a flat/building/project is owned by at most one inspector at the
# same assignment level. Higher-level grants still implicitly extend scope
# to lower levels (a tower assignment still covers its flats), but direct
# same-level co-ownership is disallowed unless the manager explicitly
# opts in via ?force=true, which strips conflicts atomically.
# ---------------------------------------------------------------------------

async def _find_same_level_conflicts(
    db: AsyncSession,
    level: str,
    entity_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[tuple[uuid.UUID, str]]:
    """Return (user_id, full_name) pairs of OTHER users already assigned at
    the same level for this entity."""
    if level == "project":
        q = (
            select(UserProjectAssignment.user_id, User.full_name)
            .join(User, User.id == UserProjectAssignment.user_id)
            .where(
                UserProjectAssignment.project_id == entity_id,
                UserProjectAssignment.user_id != user_id,
            )
        )
    elif level == "building":
        q = (
            select(UserBuildingAssignment.user_id, User.full_name)
            .join(User, User.id == UserBuildingAssignment.user_id)
            .where(
                UserBuildingAssignment.building_id == entity_id,
                UserBuildingAssignment.user_id != user_id,
            )
        )
    elif level == "flat":
        q = (
            select(UserFlatAssignment.user_id, User.full_name)
            .join(User, User.id == UserFlatAssignment.user_id)
            .where(
                UserFlatAssignment.flat_id == entity_id,
                UserFlatAssignment.user_id != user_id,
            )
        )
    else:
        raise ValueError(f"Unknown level: {level}")

    rows = (await db.execute(q)).all()
    return [(r.user_id, r.full_name) for r in rows]


async def _cascade_strip_other_users(
    db: AsyncSession,
    level: str,
    entity_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[dict]:
    """Strip same-level other-user conflicts AND cascade-strip lower-level
    direct assignments from other users inside this scope.

    Project-level strip removes other users' direct building and flat
    assignments inside the project. Building-level strip removes other
    users' direct flat assignments inside the building. Flat-level strip
    only removes same-level conflicts (leaf).

    Adds objects to the session for deletion; caller must commit. Returns a
    list of removed-assignment dicts for SSE notifications and the
    client-visible summary."""
    removed: list[dict] = []

    async def _drop_project_assignments_in(project_id: uuid.UUID) -> None:
        rows = (
            await db.execute(
                select(UserProjectAssignment, User.full_name)
                .join(User, User.id == UserProjectAssignment.user_id)
                .where(
                    UserProjectAssignment.project_id == project_id,
                    UserProjectAssignment.user_id != user_id,
                )
            )
        ).all()
        for assignment, name in rows:
            removed.append({
                "user_id": assignment.user_id,
                "user_name": name,
                "level": "project",
                "entity_id": assignment.project_id,
            })
            await db.delete(assignment)

    async def _drop_direct_buildings_in(project_id: uuid.UUID | None,
                                         building_id: uuid.UUID | None) -> None:
        q = (
            select(UserBuildingAssignment, User.full_name)
            .join(User, User.id == UserBuildingAssignment.user_id)
            .join(Building, Building.id == UserBuildingAssignment.building_id)
            .where(UserBuildingAssignment.user_id != user_id)
        )
        if project_id is not None:
            q = q.where(Building.project_id == project_id)
        if building_id is not None:
            q = q.where(Building.id == building_id)
        rows = (await db.execute(q)).all()
        for assignment, name in rows:
            removed.append({
                "user_id": assignment.user_id,
                "user_name": name,
                "level": "building",
                "entity_id": assignment.building_id,
            })
            await db.delete(assignment)

    async def _drop_direct_flats_in(project_id: uuid.UUID | None,
                                     building_id: uuid.UUID | None,
                                     flat_id: uuid.UUID | None) -> None:
        q = (
            select(UserFlatAssignment, User.full_name)
            .join(User, User.id == UserFlatAssignment.user_id)
            .join(Flat, Flat.id == UserFlatAssignment.flat_id)
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
            .where(UserFlatAssignment.user_id != user_id)
        )
        if project_id is not None:
            q = q.where(Building.project_id == project_id)
        if building_id is not None:
            q = q.where(Floor.building_id == building_id)
        if flat_id is not None:
            q = q.where(Flat.id == flat_id)
        rows = (await db.execute(q)).all()
        for assignment, name in rows:
            removed.append({
                "user_id": assignment.user_id,
                "user_name": name,
                "level": "flat",
                "entity_id": assignment.flat_id,
            })
            await db.delete(assignment)

    if level == "project":
        await _drop_project_assignments_in(entity_id)
        await _drop_direct_buildings_in(project_id=entity_id, building_id=None)
        await _drop_direct_flats_in(project_id=entity_id, building_id=None, flat_id=None)
    elif level == "building":
        # Same-level: other users' building assignments on this building
        await _drop_direct_buildings_in(project_id=None, building_id=entity_id)
        # Cascade: other users' direct flat assignments inside this building
        await _drop_direct_flats_in(project_id=None, building_id=entity_id, flat_id=None)
    elif level == "flat":
        await _drop_direct_flats_in(project_id=None, building_id=None, flat_id=entity_id)
    else:
        raise ValueError(f"Unknown level: {level}")

    return removed


def _conflicts_to_http_detail(
    level: str, conflicts: list[tuple[uuid.UUID, str]]
) -> dict:
    """Structured 409 body the portal can read to show a reassign prompt."""
    return {
        "code": "EXCLUSIVE_CONFLICT",
        "level": level,
        "message": (
            f"Another inspector is already assigned at the {level} level. "
            "Retry with ?force=true to reassign."
        ),
        "conflicts": [
            {"user_id": str(uid), "full_name": name} for uid, name in conflicts
        ],
    }


def _removed_to_response(removed: list[dict]) -> list[dict]:
    return [
        {
            "user_id": str(r["user_id"]),
            "user_name": r["user_name"],
            "level": r["level"],
            "entity_id": str(r["entity_id"]),
        }
        for r in removed
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

    if body.role not in ("MANAGER", "INSPECTOR", "CONTRACTOR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be MANAGER, INSPECTOR, or CONTRACTOR",
        )

    if body.role == "CONTRACTOR":
        if not body.trades:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trades is required and must be non-empty for CONTRACTOR role",
            )
        _validate_trades(body.trades)
    else:
        if body.trades is not None or body.company is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trades and company are only valid for CONTRACTOR role",
            )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        email=body.email,
        phone=body.phone,
        company=body.company if body.role == "CONTRACTOR" else None,
        trades=body.trades if body.role == "CONTRACTOR" else None,
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
    force: bool = False,
) -> UserResponse:
    result = await db.execute(
        select(User).options(*_load_assignments()).where(User.id == user_id)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Contractor-only fields: validate role compatibility before applying any
    # mutation so we don't half-apply on reject.
    if body.company is not None and user.role != "CONTRACTOR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="company can only be set on CONTRACTOR users",
        )
    if body.trades is not None:
        if user.role != "CONTRACTOR":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trades can only be set on CONTRACTOR users",
            )
        if len(body.trades) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trades cannot be empty for a CONTRACTOR",
            )
        _validate_trades(body.trades)

    # Guard contractor deactivation against silently orphaning open snags.
    # "Open" = anything not yet VERIFIED by a manager (OPEN or FIXED).
    if (
        body.is_active is False
        and user.is_active is True
        and user.role == "CONTRACTOR"
        and not force
    ):
        open_q = await db.execute(
            select(
                SnagContractorAssignment.inspection_entry_id,
                InspectionEntry.item_name,
                InspectionEntry.snag_fix_status,
            )
            .join(
                InspectionEntry,
                InspectionEntry.id == SnagContractorAssignment.inspection_entry_id,
            )
            .where(
                SnagContractorAssignment.contractor_id == user_id,
                InspectionEntry.snag_fix_status != "VERIFIED",
            )
        )
        rows = open_q.all()
        if rows:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OPEN_ASSIGNMENTS",
                    "message": (
                        "Contractor has open assignments. Reassign them first "
                        "or retry with ?force=true."
                    ),
                    "entries": [
                        {
                            "entry_id": str(r[0]),
                            "item_name": r[1],
                            "snag_fix_status": r[2],
                        }
                        for r in rows
                    ],
                },
            )

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.email is not None:
        user.email = body.email
    if body.phone is not None:
        user.phone = body.phone
    if body.company is not None:
        user.company = body.company
    if body.trades is not None:
        user.trades = body.trades

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
    force: bool = False,
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

    if not force:
        conflicts = await _find_same_level_conflicts(
            db, "project", project_id, user_id
        )
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_conflicts_to_http_detail("project", conflicts),
            )

    removed: list[dict] = []
    if force:
        removed = await _cascade_strip_other_users(
            db, "project", project_id, user_id
        )

    db.add(UserProjectAssignment(user_id=user_id, project_id=project_id))
    await db.commit()

    for r in removed:
        await _notify_assignment_changed(
            r["user_id"], "unassigned", r["level"], r["entity_id"]
        )
    await _notify_assignment_changed(user_id, "assigned", "project", project_id)

    return {
        "detail": "Project assigned",
        "unassigned": _removed_to_response(removed),
    }


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
    force: bool = False,
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

    if not force:
        conflicts = await _find_same_level_conflicts(
            db, "building", building_id, user_id
        )
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_conflicts_to_http_detail("building", conflicts),
            )

    removed: list[dict] = []
    if force:
        removed = await _cascade_strip_other_users(
            db, "building", building_id, user_id
        )

    db.add(UserBuildingAssignment(user_id=user_id, building_id=building_id))
    await db.commit()

    for r in removed:
        await _notify_assignment_changed(
            r["user_id"], "unassigned", r["level"], r["entity_id"]
        )
    await _notify_assignment_changed(user_id, "assigned", "building", building_id)

    return {
        "detail": "Building assigned",
        "unassigned": _removed_to_response(removed),
    }


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
    force: bool = False,
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

    if not force:
        conflicts = await _find_same_level_conflicts(
            db, "flat", flat_id, user_id
        )
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_conflicts_to_http_detail("flat", conflicts),
            )

    removed: list[dict] = []
    if force:
        removed = await _cascade_strip_other_users(
            db, "flat", flat_id, user_id
        )

    db.add(UserFlatAssignment(user_id=user_id, flat_id=flat_id))
    await db.commit()

    for r in removed:
        await _notify_assignment_changed(
            r["user_id"], "unassigned", r["level"], r["entity_id"]
        )
    await _notify_assignment_changed(user_id, "assigned", "flat", flat_id)

    return {
        "detail": "Flat assigned",
        "unassigned": _removed_to_response(removed),
    }


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
