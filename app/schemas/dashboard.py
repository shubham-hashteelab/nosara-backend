import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProjectStats(BaseModel):
    project_id: uuid.UUID
    project_name: str
    total_buildings: int
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    total_snags: int
    open_snags: int
    fixed_snags: int
    verified_snags: int
    critical_snags: int
    major_snags: int
    minor_snags: int
    snags_by_category: dict[str, int]


class BuildingStats(BaseModel):
    building_id: uuid.UUID
    building_name: str
    total_floors: int
    total_flats: int
    total_entries: int
    snag_count: int
    ok_count: int
    na_count: int


class OverdueSnag(BaseModel):
    entry_id: uuid.UUID
    flat_id: uuid.UUID
    room_label: str
    item_name: str
    severity: Optional[str] = None
    contractor_name: Optional[str] = None
    due_date: Optional[datetime] = None
    days_overdue: int


class InspectorActivity(BaseModel):
    inspector_id: uuid.UUID
    inspector_name: str
    total_entries: int
    snags_found: int
    last_activity: Optional[datetime] = None
