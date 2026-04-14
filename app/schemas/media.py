import uuid
from typing import Optional

from pydantic import BaseModel


class FileUploadResponse(BaseModel):
    id: uuid.UUID
    minio_key: str
    original_filename: Optional[str] = None


class FileDeleteResponse(BaseModel):
    detail: str = "File deleted"
