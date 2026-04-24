import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SnagContractorAssignment(Base):
    __tablename__ = "snag_contractor_assignments"
    __table_args__ = (
        UniqueConstraint(
            "inspection_entry_id", name="uq_snag_entry_one_contractor"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    inspection_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inspection_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    contractor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    due_date: Mapped[Optional[datetime]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    inspection_entry = relationship(
        "InspectionEntry", back_populates="contractor_assignments"
    )
    contractor = relationship("User")
