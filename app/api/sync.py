import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.sync import (
    SyncPullRequest,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from app.services.minio_service import minio_service
from app.services.sync_service import sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=SyncPushResponse)
async def sync_push(
    body: SyncPushRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncPushResponse:
    accepted, rejected = await sync_service.process_push(
        operations=body.operations,
        inspector_id=current_user.id,
        db=db,
    )
    return SyncPushResponse(accepted=accepted, rejected=rejected)


@router.post("/pull", response_model=SyncPullResponse)
async def sync_pull(
    body: SyncPullRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncPullResponse:
    try:
        last_synced = datetime.fromisoformat(body.last_synced_at)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid ISO8601 timestamp for last_synced_at",
        )

    # If naive, assume UTC
    if last_synced.tzinfo is None:
        last_synced = last_synced.replace(tzinfo=timezone.utc)

    data = await sync_service.process_pull(
        last_synced_at=last_synced,
        user_id=current_user.id,
        db=db,
    )
    return SyncPullResponse(**data)


@router.post("/upload-file", status_code=status.HTTP_201_CREATED)
async def sync_upload_file(
    file: Annotated[UploadFile, File()],
    minio_key: Annotated[str, Form()],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """
    Upload a file during sync. The client specifies the desired minio_key.
    This is used for offline-created files that need to be pushed to storage.
    """
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    minio_service.upload_file(file_bytes, minio_key, content_type)
    return {"minio_key": minio_key, "size": len(file_bytes)}
