import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ChecklistTemplateCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    room_type: str
    category: str
    item_name: str
    trade: str
    sort_order: int = 0
    is_active: bool = True


class ChecklistTemplateUpdate(BaseModel):
    room_type: Optional[str] = None
    category: Optional[str] = None
    item_name: Optional[str] = None
    trade: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ChecklistTemplateResponse(BaseModel):
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    room_type: str
    category: str
    item_name: str
    trade: str = "MISC"
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FlatTypeRoomCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    flat_type: str
    room_type: str
    label: str
    sort_order: int = 0


class FlatTypeRoomUpdate(BaseModel):
    flat_type: Optional[str] = None
    room_type: Optional[str] = None
    label: Optional[str] = None
    sort_order: Optional[int] = None


class FlatTypeRoomResponse(BaseModel):
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    flat_type: str
    room_type: str
    label: str
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


class FloorPlanLayoutCreate(BaseModel):
    project_id: Optional[uuid.UUID] = None
    flat_type: str
    room_label: str
    x: float
    y: float
    width: float
    height: float


class FloorPlanLayoutUpdate(BaseModel):
    flat_type: Optional[str] = None
    room_label: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None


class FloorPlanLayoutResponse(BaseModel):
    id: uuid.UUID
    project_id: Optional[uuid.UUID] = None
    flat_type: str
    room_label: str
    x: float
    y: float
    width: float
    height: float

    model_config = ConfigDict(from_attributes=True)
