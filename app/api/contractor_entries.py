import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, require_contractor, require_manager
from app.api.entry_helpers import entry_to_response
from app.models.building import Building
from app.models.contractor import SnagContractorAssignment
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry, SnagImage
from app.models.user import User
from app.schemas.contractor import (
    OrphanedAssignmentResponse,
    SnagContractorAssignmentCreate,
    SnagContractorAssignmentResponse,
)
from app.schemas.inspection import (
    InspectionEntryResponse,
    MarkFixedRequest,
    RejectRequest,
    VerifyRequest,
)

router = APIRouter(tags=["contractor-entries"])


def _entry_load_options():
    return (
        selectinload(InspectionEntry.images),
        selectinload(InspectionEntry.voice_notes),
        selectinload(InspectionEntry.videos),
        selectinload(InspectionEntry.contractor_assignments).selectinload(
            SnagContractorAssignment.contractor
        ),
    )


async def _load_entry(entry_id: uuid.UUID, db: AsyncSession) -> InspectionEntry:
    result = await db.execute(
        select(InspectionEntry)
        .options(*_entry_load_options())
        .where(InspectionEntry.id == entry_id)
    )
    entry = result.scalars().first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


def _build_assignment_response(
    assignment: SnagContractorAssignment,
) -> SnagContractorAssignmentResponse:
    return SnagContractorAssignmentResponse(
        id=assignment.id,
        inspection_entry_id=assignment.inspection_entry_id,
        contractor_id=assignment.contractor_id,
        contractor_name=assignment.contractor.full_name,
        contractor_trades=assignment.contractor.trades or [],
        assigned_at=assignment.assigned_at,
        due_date=assignment.due_date,
        notes=assignment.notes,
    )


# ---------------------------------------------------------------------------
# Contractor-facing endpoints
# ---------------------------------------------------------------------------


@router.get("/entries/my-assigned", response_model=list[InspectionEntryResponse])
async def list_my_assigned(
    current_user: Annotated[User, Depends(require_contractor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    snag_fix_status: Annotated[str | None, Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[InspectionEntryResponse]:
    """Snag entries currently assigned to the calling contractor."""
    stmt = (
        select(InspectionEntry)
        .options(*_entry_load_options())
        .join(
            SnagContractorAssignment,
            SnagContractorAssignment.inspection_entry_id == InspectionEntry.id,
        )
        .where(SnagContractorAssignment.contractor_id == current_user.id)
    )
    if snag_fix_status:
        stmt = stmt.where(InspectionEntry.snag_fix_status == snag_fix_status)
    stmt = stmt.order_by(InspectionEntry.updated_at.desc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [entry_to_response(e) for e in entries]


@router.post(
    "/entries/{entry_id}/mark-fixed",
    response_model=InspectionEntryResponse,
)
async def mark_fixed(
    entry_id: uuid.UUID,
    body: MarkFixedRequest,
    current_user: Annotated[User, Depends(require_contractor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    entry = await _load_entry(entry_id, db)

    # Assigned-to-caller check. Unique constraint guarantees at most one
    # assignment per entry, so "the" assignment is the first (and only) one.
    assignment = next(iter(entry.contractor_assignments), None)
    if assignment is None or assignment.contractor_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned contractor for this entry",
        )

    if entry.status != "FAIL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "NOT_A_SNAG",
                "message": "Only FAIL entries can be marked fixed",
            },
        )

    if entry.snag_fix_status == "FIXED":
        return entry_to_response(entry)  # idempotent
    if entry.snag_fix_status == "VERIFIED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_VERIFIED"},
        )
    if entry.snag_fix_status != "OPEN":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE",
                "message": (
                    f"Cannot transition from {entry.snag_fix_status} to FIXED"
                ),
            },
        )

    closure_count = await db.scalar(
        select(func.count(SnagImage.id)).where(
            SnagImage.inspection_entry_id == entry.id,
            SnagImage.kind == "CLOSURE",
        )
    )
    if (closure_count or 0) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "NO_CLOSURE_IMAGE",
                "message": "At least one CLOSURE image is required before marking fixed",
            },
        )

    entry.snag_fix_status = "FIXED"
    entry.fixed_at = datetime.now(timezone.utc)
    entry.fixed_by_id = current_user.id
    entry.rejection_remark = None
    entry.rejected_at = None
    if body.notes:
        entry.notes = body.notes

    await db.commit()

    entry = await _load_entry(entry_id, db)
    return entry_to_response(entry)


# ---------------------------------------------------------------------------
# Manager-facing verification + assignment endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/entries/verification-queue", response_model=list[InspectionEntryResponse]
)
async def verification_queue(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[InspectionEntryResponse]:
    """FIXED entries awaiting manager verification. Oldest first (FIFO)."""
    stmt = (
        select(InspectionEntry)
        .options(*_entry_load_options())
        .where(InspectionEntry.snag_fix_status == "FIXED")
    )
    if project_id is not None:
        stmt = (
            stmt.join(Flat, Flat.id == InspectionEntry.flat_id)
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
            .where(Building.project_id == project_id)
        )
    stmt = stmt.order_by(InspectionEntry.updated_at.asc()).offset(skip).limit(limit)

    result = await db.execute(stmt)
    entries = result.scalars().all()
    return [entry_to_response(e) for e in entries]


@router.get(
    "/entries/orphaned-assignments",
    response_model=list[OrphanedAssignmentResponse],
)
async def orphaned_assignments(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[OrphanedAssignmentResponse]:
    """Assignments whose contractor user is deactivated or no longer a
    CONTRACTOR role. The portal uses this to drive a reassignment queue."""
    result = await db.execute(
        select(SnagContractorAssignment, User)
        .join(User, User.id == SnagContractorAssignment.contractor_id)
        .where((User.is_active.is_(False)) | (User.role != "CONTRACTOR"))
    )
    rows = result.all()
    return [
        OrphanedAssignmentResponse(
            assignment_id=a.id,
            inspection_entry_id=a.inspection_entry_id,
            contractor_id=a.contractor_id,
            contractor_name=u.full_name,
            contractor_role=u.role,
            contractor_is_active=u.is_active,
            assigned_at=a.assigned_at,
        )
        for (a, u) in rows
    ]


@router.post(
    "/entries/{entry_id}/verify",
    response_model=InspectionEntryResponse,
)
async def verify_entry(
    entry_id: uuid.UUID,
    body: VerifyRequest,
    current_user: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    entry = await _load_entry(entry_id, db)

    if entry.snag_fix_status == "VERIFIED":
        return entry_to_response(entry)  # idempotent — preserve original remark
    if entry.snag_fix_status != "FIXED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_FIXED",
                "message": "Entry must be FIXED before it can be verified",
            },
        )

    entry.snag_fix_status = "VERIFIED"
    entry.verified_at = datetime.now(timezone.utc)
    entry.verified_by_id = current_user.id
    entry.verification_remark = body.verification_remark

    await db.commit()

    entry = await _load_entry(entry_id, db)
    return entry_to_response(entry)


@router.post(
    "/entries/{entry_id}/reject",
    response_model=InspectionEntryResponse,
)
async def reject_entry(
    entry_id: uuid.UUID,
    body: RejectRequest,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InspectionEntryResponse:
    entry = await _load_entry(entry_id, db)

    if entry.snag_fix_status == "VERIFIED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_VERIFIED"},
        )
    if entry.snag_fix_status != "FIXED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "NOT_FIXED",
                "message": "Entry must be FIXED before it can be rejected",
            },
        )

    entry.snag_fix_status = "OPEN"
    entry.rejection_remark = body.rejection_remark
    entry.rejected_at = datetime.now(timezone.utc)
    entry.fixed_at = None
    entry.fixed_by_id = None

    await db.commit()

    entry = await _load_entry(entry_id, db)
    return entry_to_response(entry)


@router.post(
    "/entries/{entry_id}/assign-contractor/{contractor_id}",
    response_model=SnagContractorAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_contractor(
    entry_id: uuid.UUID,
    contractor_id: uuid.UUID,
    body: SnagContractorAssignmentCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> SnagContractorAssignmentResponse:
    entry_row = await db.scalar(
        select(InspectionEntry).where(InspectionEntry.id == entry_id)
    )
    if entry_row is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    contractor = await db.scalar(
        select(User).where(User.id == contractor_id)
    )
    if contractor is None:
        raise HTTPException(status_code=404, detail="Contractor not found")
    if contractor.role != "CONTRACTOR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target user is not a contractor",
        )
    if not contractor.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contractor is deactivated",
        )

    contractor_trades = contractor.trades or []
    if entry_row.trade not in contractor_trades:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TRADE_MISMATCH",
                "message": (
                    f"Entry requires {entry_row.trade}; contractor handles "
                    f"{contractor_trades}"
                ),
            },
        )

    existing = await db.scalar(
        select(SnagContractorAssignment).where(
            SnagContractorAssignment.inspection_entry_id == entry_id
        )
    )
    if existing is not None:
        if existing.contractor_id == contractor_id:
            # Already assigned to the same contractor — idempotent.
            await db.refresh(existing, attribute_names=["contractor"])
            return _build_assignment_response(existing)
        if not force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "EXCLUSIVE_CONFLICT",
                    "existing_contractor_id": str(existing.contractor_id),
                    "message": (
                        "Entry already has a contractor assigned. Retry with "
                        "?force=true to replace."
                    ),
                },
            )
        # force: atomically strip the existing assignment first.
        await db.delete(existing)
        await db.flush()

    assignment = SnagContractorAssignment(
        inspection_entry_id=entry_id,
        contractor_id=contractor_id,
        due_date=body.due_date,
        notes=body.notes,
    )
    db.add(assignment)
    await db.commit()

    # Reload with contractor relationship for the response.
    result = await db.execute(
        select(SnagContractorAssignment)
        .options(selectinload(SnagContractorAssignment.contractor))
        .where(SnagContractorAssignment.id == assignment.id)
    )
    assignment = result.scalars().first()
    return _build_assignment_response(assignment)


@router.delete(
    "/entries/{entry_id}/assign-contractor/{contractor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unassign_contractor(
    entry_id: uuid.UUID,
    contractor_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    assignment = await db.scalar(
        select(SnagContractorAssignment).where(
            SnagContractorAssignment.inspection_entry_id == entry_id,
            SnagContractorAssignment.contractor_id == contractor_id,
        )
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await db.delete(assignment)
    await db.commit()
