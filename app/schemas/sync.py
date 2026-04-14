import uuid
from typing import Any, Optional

from pydantic import BaseModel

from app.schemas.building import BuildingResponse
from app.schemas.checklist import (
    ChecklistTemplateResponse,
    FlatTypeRoomResponse,
    FloorPlanLayoutResponse,
)
from app.schemas.contractor import ContractorResponse
from app.schemas.flat import FlatResponse
from app.schemas.floor import FloorResponse
from app.schemas.inspection import InspectionEntryResponse
from app.schemas.project import ProjectResponse


class SyncOperation(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    operation: str  # CREATE, UPDATE, DELETE
    data: dict[str, Any]
    timestamp: str  # ISO8601


class SyncPushRequest(BaseModel):
    operations: list[SyncOperation]


class SyncRejection(BaseModel):
    id: uuid.UUID
    reason: str


class SyncPushResponse(BaseModel):
    accepted: list[str]
    rejected: list[SyncRejection]


class SyncPullRequest(BaseModel):
    last_synced_at: Any  # ISO8601 string or epoch number


class SyncPullResponse(BaseModel):
    projects: list[ProjectResponse] = []
    buildings: list[BuildingResponse] = []
    floors: list[FloorResponse] = []
    flats: list[FlatResponse] = []
    inspection_entries: list[InspectionEntryResponse] = []
    contractors: list[ContractorResponse] = []
    checklist_templates: list[ChecklistTemplateResponse] = []
    flat_type_rooms: list[FlatTypeRoomResponse] = []
    floor_plan_layouts: list[FloorPlanLayoutResponse] = []
    deleted_ids: list[uuid.UUID] = []
    server_time: str  # ISO8601
