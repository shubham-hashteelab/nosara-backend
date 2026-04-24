import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator


class ContractorAssignmentBrief(BaseModel):
    id: uuid.UUID
    inspection_entry_id: uuid.UUID
    contractor_id: uuid.UUID
    contractor_name: str
    contractor_trades: list[str] = []
    assigned_at: datetime
    due_date: Optional[date] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _from_orm(cls, data: Any) -> Any:
        # When constructed from an ORM SnagContractorAssignment (the cascade
        # path through SyncPullResponse / InspectionEntryResponse), the
        # contractor_name and contractor_trades fields live on the loaded
        # `.contractor` relationship, not as columns on the assignment.
        # Flatten them here. Dict / kwargs input passes through unchanged.
        if isinstance(data, dict):
            return data
        contractor = getattr(data, "contractor", None)
        return {
            "id": data.id,
            "inspection_entry_id": data.inspection_entry_id,
            "contractor_id": data.contractor_id,
            "contractor_name": contractor.full_name if contractor else "",
            "contractor_trades": (contractor.trades or []) if contractor else [],
            "assigned_at": data.assigned_at,
            "due_date": data.due_date,
            "notes": data.notes,
        }


class MarkFixedRequest(BaseModel):
    notes: Optional[str] = None


class VerifyRequest(BaseModel):
    verification_remark: str


class RejectRequest(BaseModel):
    rejection_remark: str


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
    contractor_assignments: list[ContractorAssignmentBrief] = []

    model_config = ConfigDict(from_attributes=True)
