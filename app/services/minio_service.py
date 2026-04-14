import logging
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger(__name__)


class MinioService:
    def __init__(self) -> None:
        protocol = "https" if settings.MINIO_USE_SSL else "http"
        endpoint_url = f"{protocol}://{settings.MINIO_ENDPOINT}"

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )
        self.bucket = settings.MINIO_BUCKET

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not already exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
            logger.info("Created MinIO bucket: %s", self.bucket)

    def upload_file(
        self, file_bytes: bytes, key: str, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload bytes to MinIO and return the object key."""
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )
        return key

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading an object."""
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def get_object(self, key: str) -> tuple[bytes, Optional[str]]:
        """Download an object and return (bytes, content_type)."""
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"].read()
        content_type = response.get("ContentType")
        return body, content_type

    def delete_file(self, key: str) -> None:
        """Delete an object from MinIO."""
        self.client.delete_object(Bucket=self.bucket, Key=key)


minio_service = MinioService()
