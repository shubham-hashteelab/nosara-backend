import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class FloorCreate(BaseModel):
    floor_number: int


class FloorUpdate(BaseModel):
    floor_number: Optional[int] = None


class FloorResponse(BaseModel):
    id: uuid.UUID
    building_id: uuid.UUID
    floor_number: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
