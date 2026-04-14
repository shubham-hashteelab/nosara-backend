import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Flat(Base):
    __tablename__ = "flats"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    floor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("floors.id", ondelete="CASCADE"),
        nullable=False,
    )
    flat_number: Mapped[str] = mapped_column(String(50), nullable=False)
    flat_type: Mapped[str] = mapped_column(String(50), nullable=False)
    inspection_status: Mapped[str] = mapped_column(
        Enum("NOT_STARTED", "IN_PROGRESS", "COMPLETED", name="inspection_status"),
        default="NOT_STARTED",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    floor = relationship("Floor", back_populates="flats")
    inspection_entries: Mapped[list["InspectionEntry"]] = relationship(  # noqa: F821
        back_populates="flat", cascade="all, delete-orphan"
    )
