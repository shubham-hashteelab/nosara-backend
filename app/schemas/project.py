import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    location: str


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    location: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
