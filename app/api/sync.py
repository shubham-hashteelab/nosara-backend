import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.inspection import SnagImage, VoiceNote, InspectionVideo
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
    raw = body.last_synced_at
    try:
        # Try ISO8601 string first
        last_synced = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        # Try numeric epoch (seconds or milliseconds)
        try:
            epoch = float(raw)
            if epoch > 1e12:  # milliseconds
                epoch /= 1000
            last_synced = datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            raise HTTPException(
                status_code=400,
                detail="Invalid timestamp for last_synced_at (expected ISO8601 or epoch)",
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
    type: Annotated[str, Form()],
    inspection_entry_id: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """
    Upload a media file during sync and create the corresponding DB record.
    Called by the Android app for images, voice notes, and videos that were
    captured offline and need to be pushed to the server.

    - type: "snag_image", "voice_note", or "inspection_video"
    - inspection_entry_id: UUID of the parent inspection entry
    - client_id: UUID generated client-side (used as the DB record ID)
    """
    entry_uuid = uuid.UUID(inspection_entry_id)
    record_id = uuid.UUID(client_id)

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    file_ext = ""
    if file.filename:
        file_ext = "." + file.filename.rsplit(".", 1)[-1] if "." in file.filename else ""

    # Generate MinIO key matching the online upload convention
    type_folder = {
        "snag_image": "images",
        "voice_note": "voices",
        "inspection_video": "videos",
    }.get(type, "files")
    minio_key = f"{type_folder}/{inspection_entry_id}/{uuid.uuid4()}{file_ext}"

    minio_service.upload_file(file_bytes, minio_key, content_type)

    # Create the DB record linking the file to the inspection entry
    if type == "snag_image":
        record = SnagImage(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            original_filename=file.filename,
            file_size_bytes=len(file_bytes),
        )
        db.add(record)
    elif type == "voice_note":
        record = VoiceNote(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=0,  # App should update this via sync push if needed
        )
        db.add(record)
    elif type == "inspection_video":
        record = InspectionVideo(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=0,
        )
        db.add(record)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown media type: {type}. Expected: snag_image, voice_note, inspection_video",
        )

    await db.commit()

    return {"minio_key": minio_key, "size": len(file_bytes)}
