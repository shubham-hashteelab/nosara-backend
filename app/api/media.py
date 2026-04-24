import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_allow_all, get_db
from app.constants.trades import is_valid_snag_image_kind
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
    current_user: Annotated[User, Depends(get_current_user_allow_all)],
    db: Annotated[AsyncSession, Depends(get_db)],
    duration_ms: Annotated[str | None, Form()] = None,
    kind: Annotated[str | None, Form()] = None,
) -> FileUploadResponse:
    entry_uuid = uuid.UUID(inspection_entry_id)
    duration_int = 0
    if duration_ms is not None and duration_ms.strip():
        try:
            duration_int = int(float(duration_ms))
        except ValueError:
            duration_int = 0

    # Contractors can only upload closure photos. They have no voice/video
    # authoring surface at all.
    if current_user.role == "CONTRACTOR" and type != "image":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contractors can only upload images (CLOSURE kind)",
        )

    # Validate entry exists
    result = await db.execute(
        select(InspectionEntry).where(InspectionEntry.id == entry_uuid)
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Inspection entry not found")

    # Role-based kind gating for images.
    if type == "image":
        if kind is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="kind is required for image uploads (NC or CLOSURE)",
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
        # Decision #6 in the rollout doc: only contractors upload closure
        # photos. Managers can still upload NC images (e.g. during triage
        # or QA) but never a CLOSURE that would satisfy a contractor's
        # mark-fixed precondition.
        if current_user.role == "MANAGER" and kind != "NC":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only contractors can upload CLOSURE images",
            )

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
            kind=kind,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id
    elif type == "voice":
        record = VoiceNote(
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=duration_int,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        record_id = record.id
    elif type == "video":
        record = InspectionVideo(
            inspection_entry_id=entry_uuid,
            minio_key=minio_key,
            duration_ms=duration_int,
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
