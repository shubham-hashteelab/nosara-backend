import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.contractor import Contractor, SnagContractorAssignment
from app.models.inspection import InspectionEntry
from app.models.user import User
from app.schemas.contractor import (
    ContractorCreate,
    ContractorResponse,
    ContractorUpdate,
    SnagContractorAssignmentCreate,
    SnagContractorAssignmentResponse,
)

router = APIRouter(tags=["contractors"])


@router.get("/contractors", response_model=list[ContractorResponse])
async def list_contractors(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ContractorResponse]:
    result = await db.execute(
        select(Contractor).order_by(Contractor.name)
    )
    contractors = result.scalars().all()
    return [ContractorResponse.model_validate(c) for c in contractors]


@router.post(
    "/contractors",
    response_model=ContractorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contractor(
    body: ContractorCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContractorResponse:
    contractor = Contractor(
        name=body.name,
        company=body.company,
        phone=body.phone,
        email=body.email,
        specialty=body.specialty,
    )
    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)
    return ContractorResponse.model_validate(contractor)


@router.get("/contractors/{contractor_id}", response_model=ContractorResponse)
async def get_contractor(
    contractor_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContractorResponse:
    result = await db.execute(
        select(Contractor).where(Contractor.id == contractor_id)
    )
    contractor = result.scalars().first()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")
    return ContractorResponse.model_validate(contractor)


@router.patch("/contractors/{contractor_id}", response_model=ContractorResponse)
async def update_contractor(
    contractor_id: uuid.UUID,
    body: ContractorUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ContractorResponse:
    result = await db.execute(
        select(Contractor).where(Contractor.id == contractor_id)
    )
    contractor = result.scalars().first()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    for field in ("name", "company", "phone", "email", "specialty", "is_active"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(contractor, field, value)

    await db.commit()
    await db.refresh(contractor)
    return ContractorResponse.model_validate(contractor)


@router.delete(
    "/contractors/{contractor_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_contractor(
    contractor_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(Contractor).where(Contractor.id == contractor_id)
    )
    contractor = result.scalars().first()
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    await db.delete(contractor)
    await db.commit()


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
) -> SnagContractorAssignmentResponse:
    # Validate entry
    entry_result = await db.execute(
        select(InspectionEntry).where(InspectionEntry.id == entry_id)
    )
    if not entry_result.scalars().first():
        raise HTTPException(status_code=404, detail="Inspection entry not found")

    # Validate contractor
    con_result = await db.execute(
        select(Contractor).where(Contractor.id == contractor_id)
    )
    if not con_result.scalars().first():
        raise HTTPException(status_code=404, detail="Contractor not found")

    # Check duplicate
    existing = await db.execute(
        select(SnagContractorAssignment).where(
            SnagContractorAssignment.inspection_entry_id == entry_id,
            SnagContractorAssignment.contractor_id == contractor_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contractor already assigned to this entry",
        )

    assignment = SnagContractorAssignment(
        inspection_entry_id=entry_id,
        contractor_id=contractor_id,
        due_date=body.due_date,
        notes=body.notes,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return SnagContractorAssignmentResponse.model_validate(assignment)


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
    result = await db.execute(
        select(SnagContractorAssignment).where(
            SnagContractorAssignment.inspection_entry_id == entry_id,
            SnagContractorAssignment.contractor_id == contractor_id,
        )
    )
    assignment = result.scalars().first()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    await db.commit()
