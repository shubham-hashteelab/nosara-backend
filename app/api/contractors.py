import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["contractors"])

_GONE_BODY = {
    "detail": (
        "Contractors are now users with role=CONTRACTOR. Manage them via "
        "/api/v1/users endpoints. Snag assignment lives at "
        "/api/v1/entries/{id}/assign-contractor/{contractor_id}."
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
