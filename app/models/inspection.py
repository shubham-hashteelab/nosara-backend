import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InspectionEntry(Base):
    __tablename__ = "inspection_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    flat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flats.id", ondelete="CASCADE"),
        nullable=False,
    )
    room_label: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), default="NA", nullable=False
    )  # PASS, FAIL, NA
    severity: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # CRITICAL, MAJOR, MINOR
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snag_fix_status: Mapped[str] = mapped_column(
        String(20), default="OPEN", nullable=False
    )  # OPEN, FIXED, VERIFIED
    inspector_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    flat = relationship("Flat", back_populates="inspection_entries")
    inspector = relationship("User")
    images: Mapped[list["SnagImage"]] = relationship(
        back_populates="inspection_entry",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    voice_notes: Mapped[list["VoiceNote"]] = relationship(
        back_populates="inspection_entry",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    videos: Mapped[list["InspectionVideo"]] = relationship(
        back_populates="inspection_entry",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    contractor_assignments: Mapped[list["SnagContractorAssignment"]] = relationship(  # noqa: F821
        back_populates="inspection_entry",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SnagImage(Base):
    __tablename__ = "snag_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inspection_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inspection_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    minio_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    inspection_entry = relationship("InspectionEntry", back_populates="images")


class VoiceNote(Base):
    __tablename__ = "voice_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inspection_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inspection_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    minio_key: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    inspection_entry = relationship("InspectionEntry", back_populates="voice_notes")


class InspectionVideo(Base):
    __tablename__ = "inspection_videos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inspection_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inspection_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    minio_key: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    inspection_entry = relationship("InspectionEntry", back_populates="videos")
    frame_analyses: Mapped[list["VideoFrameAnalysis"]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class VideoFrameAnalysis(Base):
    __tablename__ = "video_frame_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inspection_videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    video = relationship("InspectionVideo", back_populates="frame_analyses")
