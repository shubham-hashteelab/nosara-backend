import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class InspectionEntryCreate(BaseModel):
    room_label: str
    category: str
    item_name: str
    status: str = "NA"
    severity: Optional[str] = None
    notes: Optional[str] = None
    snag_fix_status: str = "OPEN"


class InspectionEntryUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    notes: Optional[str] = None
    snag_fix_status: Optional[str] = None
    room_label: Optional[str] = None
    category: Optional[str] = None
    item_name: Optional[str] = None


class SnagImageResponse(BaseModel):
    id: uuid.UUID
    inspection_entry_id: uuid.UUID
    minio_key: str
    original_filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    kind: str = "NC"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VoiceNoteResponse(BaseModel):
    id: uuid.UUID
    inspection_entry_id: uuid.UUID
    minio_key: str
    duration_ms: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InspectionVideoResponse(BaseModel):
    id: uuid.UUID
    inspection_entry_id: uuid.UUID
    minio_key: str
    duration_ms: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VideoFrameAnalysisResponse(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    timestamp_ms: int
    description: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InspectionEntryResponse(BaseModel):
    id: uuid.UUID
    flat_id: uuid.UUID
    room_label: str
    category: str
    item_name: str
    status: str
    severity: Optional[str] = None
    notes: Optional[str] = None
    snag_fix_status: str
    inspector_id: Optional[uuid.UUID] = None
    trade: str = "MISC"
    fixed_at: Optional[datetime] = None
    fixed_by_id: Optional[uuid.UUID] = None
    verified_at: Optional[datetime] = None
    verified_by_id: Optional[uuid.UUID] = None
    verification_remark: Optional[str] = None
    rejection_remark: Optional[str] = None
    rejected_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    images: list[SnagImageResponse] = []
    voice_notes: list[VoiceNoteResponse] = []
    videos: list[InspectionVideoResponse] = []

    model_config = ConfigDict(from_attributes=True)
