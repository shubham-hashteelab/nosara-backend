import uuid
from datetime import date

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
    """Legacy per-building entry-count stats served by /dashboard/buildings/{id}/stats."""

    building_id: uuid.UUID
    building_name: str
    total_floors: int
    total_flats: int
    total_entries: int
    snag_count: int
    ok_count: int
    na_count: int


class ProjectBuildingStats(BaseModel):
    """Per-building flat/snag rollup used by the project dashboard's building table."""

    building_id: uuid.UUID
    building_name: str
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    total_snags: int
    open_snags: int


class InspectorActivity(BaseModel):
    inspector_id: uuid.UUID
    inspector_name: str
    date: date
    entries_checked: int
    snags_found: int


class FloorProgress(BaseModel):
    floor_id: uuid.UUID
    floor_number: int
    label: str
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    completion_pct: float
    open_snags: int


class TowerProgress(BaseModel):
    """Per-tower (building) flat/snag rollup with nested per-floor breakdown."""

    building_id: uuid.UUID
    building_name: str
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    completion_pct: float
    total_snags: int
    open_snags: int
    critical_snags: int
    major_snags: int
    minor_snags: int
    floors: list[FloorProgress]


class TowerStatsResponse(BaseModel):
    project_id: uuid.UUID
    project_name: str
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    completion_pct: float
    towers: list[TowerProgress]


class TowerMini(BaseModel):
    """Lightweight per-tower summary for the projects-overview card strip."""

    building_id: uuid.UUID
    building_name: str
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    completion_pct: float


class ProjectOverview(BaseModel):
    project_id: uuid.UUID
    project_name: str
    location: str
    total_buildings: int
    total_flats: int
    inspected_flats: int
    in_progress_flats: int
    not_started_flats: int
    completion_pct: float
    towers: list[TowerMini]


class ProjectsOverviewResponse(BaseModel):
    projects: list[ProjectOverview]
