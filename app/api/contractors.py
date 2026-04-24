import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["contractors"])

_GONE_BODY = {
    "detail": (
        "This endpoint is retired. Contractor management is being moved onto the "
        "User model (role=CONTRACTOR) as part of the contractor role rollout. "
        "New endpoints ship in Phase 2."
    )
}


def _gone() -> JSONResponse:
    return JSONResponse(status_code=410, content=_GONE_BODY)


@router.get("/contractors")
async def list_contractors_gone() -> JSONResponse:
    return _gone()


@router.post("/contractors")
async def create_contractor_gone() -> JSONResponse:
    return _gone()


@router.get("/contractors/{contractor_id}")
async def get_contractor_gone(contractor_id: uuid.UUID) -> JSONResponse:
    return _gone()


@router.patch("/contractors/{contractor_id}")
async def update_contractor_gone(contractor_id: uuid.UUID) -> JSONResponse:
    return _gone()


@router.delete("/contractors/{contractor_id}")
async def delete_contractor_gone(contractor_id: uuid.UUID) -> JSONResponse:
    return _gone()


@router.post("/entries/{entry_id}/assign-contractor/{contractor_id}")
async def assign_contractor_gone(
    entry_id: uuid.UUID, contractor_id: uuid.UUID
) -> JSONResponse:
    return _gone()


@router.delete("/entries/{entry_id}/assign-contractor/{contractor_id}")
async def unassign_contractor_gone(
    entry_id: uuid.UUID, contractor_id: uuid.UUID
) -> JSONResponse:
    return _gone()
