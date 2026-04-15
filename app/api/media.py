import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.inspection import InspectionEntry, InspectionVideo, SnagImage, VoiceNote
from app.models.user import User
from app.schemas.media import FileDeleteResponse, FileUploadResponse
from app.services.minio_service import minio_service

router = APIRouter(prefix="/files", tags=["media"])


@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: Annotated[UploadFile, File()],
    type: Annotated[str, Form()],  # "image", "voice", "video"
    inspection_entry_id: Annotated[str, Form()],
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileUploadResponse:
    entry_uuid = uuid.UUID(inspection_entry_id)

    # Validate entry exists
    result = await db.execute(
        select(InspectionEntry).where(InspectionEntry.id == entry_uuid)
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Inspection entry not found")

    # Read file bytes
    file_bytes = await file.read()
    file_size = len(file_bytes)

    # Generate MinIO key
    file_ext = ""
    if file.filename and "." in file.filename:
        file_ext = "." + file.filename.rsplit(".", 1)[1]
    minio_key = f"{type}s/{entry_uuid}/{uuid.uuid4()}{file_ext}"

    # Upload to MinIO
    content_type = file.content_type or "application/octet-stream"
    minio_service.upload_file(file_bytes, minio_key, content_type)

    # Create DB record
    record_id: uuid.UUID
    if type == "image":
        record = SnagImage(
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            original_filename=file.filename,
            file_size_bytes=file_size,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id
    elif type == "voice":
        record = VoiceNote(
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=0,  # Client should update this
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id
    elif type == "video":
        record = InspectionVideo(
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=0,  # Client should update this
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id
    else:
        raise HTTPException(
            status_code=400, detail="type must be 'image', 'voice', or 'video'"
        )

    return FileUploadResponse(
        id=record_id,
        minio_key=minio_key,
        original_filename=file.filename,
    )


@router.get("/{minio_key:path}")
async def get_file(
    minio_key: str,
    token: str | None = None,
) -> Response:
    """
    Proxy file download from MinIO.
    Accepts ?token= query param for auth (needed for <img src> and <audio src>).
    """
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required (pass ?token=)")

    from app.services.auth_service import decode_token
    from jose import JWTError
    try:
        decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    try:
        file_bytes, content_type = minio_service.get_object(minio_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    return Response(
        content=file_bytes,
        media_type=content_type or "application/octet-stream",
    )


@router.delete("/{file_id}", response_model=FileDeleteResponse)
async def delete_file(
    file_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileDeleteResponse:
    """Delete a media file (image, voice note, or video) by its ID."""
    # Try each media type
    for model_cls in (SnagImage, VoiceNote, InspectionVideo):
        result = await db.execute(
            select(model_cls).where(model_cls.id == file_id)
        )
        record = result.scalars().first()
        if record:
            try:
                minio_service.delete_file(record.minio_key)
            except Exception:
                pass  # File may already be gone from storage
            await db.delete(record)
            await db.commit()
            return FileDeleteResponse(detail="File deleted")

    raise HTTPException(status_code=404, detail="File not found")
