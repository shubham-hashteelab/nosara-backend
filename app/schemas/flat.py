import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class FlatCreate(BaseModel):
    flat_number: str
    flat_type: str


class FlatUpdate(BaseModel):
    flat_number: Optional[str] = None
    flat_type: Optional[str] = None
    inspection_status: Optional[str] = None


class FlatResponse(BaseModel):
    id: uuid.UUID
    floor_id: uuid.UUID
    flat_number: str
    flat_type: str
    inspection_status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
