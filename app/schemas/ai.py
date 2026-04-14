import uuid
from typing import Optional

from pydantic import BaseModel


class DescribeSnagRequest(BaseModel):
    image_minio_key: Optional[str] = None
    image_base64: Optional[str] = None
    item_name: str
    category: str
    room_label: str


class DescribeSnagResponse(BaseModel):
    description: str


class AnalyzeVideoFrameRequest(BaseModel):
    video_id: uuid.UUID
    frame_base64: str
    timestamp_ms: int


class AnalyzeVideoFrameResponse(BaseModel):
    description: str
    frame_analysis_id: uuid.UUID
