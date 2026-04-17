import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: str  # MANAGER or INSPECTOR


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    assigned_project_ids: list[uuid.UUID] = []
    assigned_building_ids: list[uuid.UUID] = []
    assigned_flat_ids: list[uuid.UUID] = []

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---------------------------------------------------------------------------
# User scope details — assignments resolved to human-readable names for the
# portal's user detail panel. Each list holds only *direct* assignments at
# that level: a building listed here may still be covered by a project-level
# assignment, but the portal needs to know what was explicitly picked.
# ---------------------------------------------------------------------------


class ScopedProject(BaseModel):
    project_id: uuid.UUID
    project_name: str
    location: str
    total_buildings: int
    total_flats: int


class ScopedBuilding(BaseModel):
    building_id: uuid.UUID
    building_name: str
    project_id: uuid.UUID
    project_name: str
    total_floors: int
    total_flats: int


class ScopedFlat(BaseModel):
    flat_id: uuid.UUID
    flat_number: str
    flat_type: str
    floor_id: uuid.UUID
    floor_number: int
    floor_label: str
    building_id: uuid.UUID
    building_name: str
    project_id: uuid.UUID
    project_name: str


class UserScopeDetails(BaseModel):
    user_id: uuid.UUID
    role: str
    projects: list[ScopedProject]
    buildings: list[ScopedBuilding]
    flats: list[ScopedFlat]
