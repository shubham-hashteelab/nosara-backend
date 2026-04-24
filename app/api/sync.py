import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_allow_all, get_db
from app.constants.trades import is_valid_snag_image_kind
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
    current_user: Annotated[User, Depends(get_current_user_allow_all)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncPushResponse:
    accepted, rejected = await sync_service.process_push(
        operations=body.operations,
        caller=current_user,
        db=db,
    )
    return SyncPushResponse(accepted=accepted, rejected=rejected)


@router.post("/pull", response_model=SyncPullResponse)
async def sync_pull(
    body: SyncPullRequest,
    current_user: Annotated[User, Depends(get_current_user_allow_all)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncPullResponse:
    raw = body.last_synced_at
    try:
        last_synced = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
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

    if last_synced.tzinfo is None:
        last_synced = last_synced.replace(tzinfo=timezone.utc)

    data = await sync_service.process_pull(
        last_synced_at=last_synced,
        caller=current_user,
        db=db,
    )
    return SyncPullResponse(**data)


@router.post("/upload-file", status_code=status.HTTP_201_CREATED)
async def sync_upload_file(
    file: Annotated[UploadFile, File()],
    type: Annotated[str, Form()],
    inspection_entry_id: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    current_user: Annotated[User, Depends(get_current_user_allow_all)],
    db: Annotated[AsyncSession, Depends(get_db)],
    duration_ms: Annotated[str | None, Form()] = None,
    kind: Annotated[str | None, Form()] = None,
) -> dict:
    """
    Upload a media file during sync and create the corresponding DB record.
    Called by Android clients for images, voice notes, and videos captured
    offline.

    - type: "snag_image", "voice_note", or "inspection_video"
    - inspection_entry_id: UUID of the parent inspection entry
    - client_id: UUID generated client-side (used as the DB record ID)
    - duration_ms: optional, required for voice_note and inspection_video
    - kind: required for snag_image ("NC" or "CLOSURE"), ignored otherwise
    """
    # Contractors can only push CLOSURE image uploads, nothing else.
    if current_user.role == "CONTRACTOR" and type != "snag_image":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contractors can only upload snag_image (CLOSURE kind)",
        )

    if type == "snag_image":
        if kind is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="kind is required for snag_image uploads (NC or CLOSURE)",
            )
        if not is_valid_snag_image_kind(kind):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid kind: {kind}. Must be NC or CLOSURE",
            )
        if current_user.role == "INSPECTOR" and kind != "NC":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inspectors can only upload NC images",
            )
        if current_user.role == "CONTRACTOR" and kind != "CLOSURE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Contractors can only upload CLOSURE images",
            )
        if current_user.role == "MANAGER" and kind != "NC":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only contractors can upload CLOSURE images",
            )

    entry_uuid = uuid.UUID(inspection_entry_id)
    record_id = uuid.UUID(client_id)
    duration_int = 0
    if duration_ms is not None and duration_ms.strip():
        try:
            duration_int = int(float(duration_ms))
        except ValueError:
            duration_int = 0

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    file_ext = ""
    if file.filename:
        file_ext = "." + file.filename.rsplit(".", 1)[-1] if "." in file.filename else ""

    type_folder = {
        "snag_image": "images",
        "voice_note": "voices",
        "inspection_video": "videos",
    }.get(type, "files")
    minio_key = f"{type_folder}/{inspection_entry_id}/{uuid.uuid4()}{file_ext}"

    minio_service.upload_file(file_bytes, minio_key, content_type)

    if type == "snag_image":
        record = SnagImage(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            original_filename=file.filename,
            file_size_bytes=len(file_bytes),
            kind=kind,
        )
        db.add(record)
    elif type == "voice_note":
        record = VoiceNote(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=duration_int,
        )
        db.add(record)
    elif type == "inspection_video":
        record = InspectionVideo(
            id=record_id,
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=duration_int,
        )
        db.add(record)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown media type: {type}. Expected: snag_image, voice_note, inspection_video",
        )

    await db.commit()

    return {"minio_key": minio_key, "size": len(file_bytes)}
