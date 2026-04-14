import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BuildingCreate(BaseModel):
    name: str


class BuildingUpdate(BaseModel):
    name: Optional[str] = None


class BuildingResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    total_floors: int = 0
    total_flats: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
