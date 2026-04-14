"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    user_role = sa.Enum("MANAGER", "INSPECTOR", name="user_role")
    user_role.create(op.get_bind(), checkfirst=True)

    inspection_status = sa.Enum("NOT_STARTED", "IN_PROGRESS", "COMPLETED", name="inspection_status")
    inspection_status.create(op.get_bind(), checkfirst=True)

    entry_status = sa.Enum("OK", "SNAG", "NA", name="entry_status")
    entry_status.create(op.get_bind(), checkfirst=True)

    severity_level = sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="severity_level")
    severity_level.create(op.get_bind(), checkfirst=True)

    snag_fix_status = sa.Enum("OPEN", "IN_PROGRESS", "FIXED", "VERIFIED", name="snag_fix_status")
    snag_fix_status.create(op.get_bind(), checkfirst=True)

    notification_status = sa.Enum("PENDING", "SENT", "FAILED", name="notification_status")
    notification_status.create(op.get_bind(), checkfirst=True)

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(150), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- user_project_assignments ---
    op.create_table(
        "user_project_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )

    # --- buildings ---
    op.create_table(
        "buildings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- floors ---
    op.create_table(
        "floors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("building_id", UUID(as_uuid=True), sa.ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("floor_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- flats ---
    op.create_table(
        "flats",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("floor_id", UUID(as_uuid=True), sa.ForeignKey("floors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flat_number", sa.String(50), nullable=False),
        sa.Column("flat_type", sa.String(50), nullable=False),
        sa.Column("inspection_status", inspection_status, default="NOT_STARTED", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- inspection_entries ---
    op.create_table(
        "inspection_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("flat_id", UUID(as_uuid=True), sa.ForeignKey("flats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_label", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("item_name", sa.String(255), nullable=False),
        sa.Column("status", entry_status, default="NA", nullable=False),
        sa.Column("severity", severity_level, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("snag_fix_status", snag_fix_status, default="OPEN", nullable=False),
        sa.Column("inspector_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- snag_images ---
    op.create_table(
        "snag_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("inspection_entry_id", UUID(as_uuid=True), sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- voice_notes ---
    op.create_table(
        "voice_notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("inspection_entry_id", UUID(as_uuid=True), sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- inspection_videos ---
    op.create_table(
        "inspection_videos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("inspection_entry_id", UUID(as_uuid=True), sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- video_frame_analyses ---
    op.create_table(
        "video_frame_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("inspection_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- contractors ---
    op.create_table(
        "contractors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("specialty", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- snag_contractor_assignments ---
    op.create_table(
        "snag_contractor_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("inspection_entry_id", UUID(as_uuid=True), sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contractor_id", UUID(as_uuid=True), sa.ForeignKey("contractors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("inspection_entry_id", "contractor_id", name="uq_snag_contractor"),
    )

    # --- checklist_templates ---
    op.create_table(
        "checklist_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("room_type", sa.String(100), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("item_name", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), default=0, nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- flat_type_rooms ---
    op.create_table(
        "flat_type_rooms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("flat_type", sa.String(50), nullable=False),
        sa.Column("room_type", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), default=0, nullable=False),
    )

    # --- floor_plan_layouts ---
    op.create_table(
        "floor_plan_layouts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("flat_type", sa.String(50), nullable=False),
        sa.Column("room_label", sa.String(255), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
    )

    # --- notification_logs ---
    op.create_table(
        "notification_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("recipient_type", sa.String(50), nullable=False),
        sa.Column("recipient_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", notification_status, default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("notification_logs")
    op.drop_table("floor_plan_layouts")
    op.drop_table("flat_type_rooms")
    op.drop_table("checklist_templates")
    op.drop_table("snag_contractor_assignments")
    op.drop_table("contractors")
    op.drop_table("video_frame_analyses")
    op.drop_table("inspection_videos")
    op.drop_table("voice_notes")
    op.drop_table("snag_images")
    op.drop_table("inspection_entries")
    op.drop_table("flats")
    op.drop_table("floors")
    op.drop_table("buildings")
    op.drop_table("user_project_assignments")
    op.drop_table("projects")
    op.drop_table("users")

    # Drop enum types
    sa.Enum(name="notification_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="snag_fix_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="severity_level").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="entry_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="inspection_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
