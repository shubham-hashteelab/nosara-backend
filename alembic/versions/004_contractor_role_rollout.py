"""Contractor role rollout — Phase 1 schema changes

Revision ID: 004
Revises: 003
Create Date: 2026-04-24

Destructive schema migration (pod data is wiped and re-seeded daily, so no
data preservation is attempted):

- Drops contractors table entirely; contractors will be modelled as users
  with role='CONTRACTOR' starting in Phase 2.
- Drops + recreates snag_contractor_assignments with FK -> users.id and
  unique constraint on inspection_entry_id alone (one active contractor
  per snag).
- Adds email, phone, company, trades columns to users.
- Adds trade + fix/verify timeline columns to inspection_entries.
- Adds trade to checklist_templates.
- Adds kind (NC/CLOSURE) to snag_images.

NOT NULL columns (trade on entries/templates, kind on images) use a
server_default so the migration works cleanly against both empty and
populated DBs without a separate UPDATE backfill.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop snag_contractor_assignments first (FK child of contractors).
    op.drop_table("snag_contractor_assignments")

    # Drop the standalone contractors table.
    op.drop_table("contractors")

    # Recreate snag_contractor_assignments: FK -> users.id, unique on
    # inspection_entry_id alone (Phase 1 decision #8: one active contractor
    # per snag).
    op.create_table(
        "snag_contractor_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inspection_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contractor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "inspection_entry_id", name="uq_snag_entry_one_contractor"
        ),
    )

    # users: add contact / contractor-profile columns.
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("company", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("trades", ARRAY(sa.Text()), nullable=True),
    )

    # inspection_entries: trade + fix/verify/reject timeline columns.
    op.add_column(
        "inspection_entries",
        sa.Column(
            "trade",
            sa.String(50),
            nullable=False,
            server_default="MISC",
        ),
    )
    op.add_column(
        "inspection_entries",
        sa.Column("fixed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inspection_entries",
        sa.Column(
            "fixed_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "inspection_entries",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "inspection_entries",
        sa.Column(
            "verified_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "inspection_entries",
        sa.Column("verification_remark", sa.Text(), nullable=True),
    )
    op.add_column(
        "inspection_entries",
        sa.Column("rejection_remark", sa.Text(), nullable=True),
    )
    op.add_column(
        "inspection_entries",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )

    # checklist_templates: trade for routing to the right contractor.
    op.add_column(
        "checklist_templates",
        sa.Column(
            "trade",
            sa.String(50),
            nullable=False,
            server_default="MISC",
        ),
    )

    # snag_images: distinguish original NC photos from contractor CLOSURE
    # photos uploaded at fix time.
    op.add_column(
        "snag_images",
        sa.Column(
            "kind",
            sa.String(20),
            nullable=False,
            server_default="NC",
        ),
    )


def downgrade() -> None:
    # Best-effort structural rollback. Pod data is wiped daily so there is
    # no contractor data to restore into the recreated tables.
    op.drop_column("snag_images", "kind")

    op.drop_column("checklist_templates", "trade")

    op.drop_column("inspection_entries", "rejected_at")
    op.drop_column("inspection_entries", "rejection_remark")
    op.drop_column("inspection_entries", "verification_remark")
    op.drop_column("inspection_entries", "verified_by_id")
    op.drop_column("inspection_entries", "verified_at")
    op.drop_column("inspection_entries", "fixed_by_id")
    op.drop_column("inspection_entries", "fixed_at")
    op.drop_column("inspection_entries", "trade")

    op.drop_column("users", "trades")
    op.drop_column("users", "company")
    op.drop_column("users", "phone")
    op.drop_column("users", "email")

    op.drop_table("snag_contractor_assignments")

    op.create_table(
        "contractors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("specialty", sa.String(255), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "snag_contractor_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inspection_entry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inspection_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contractor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contractors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "inspection_entry_id", "contractor_id", name="uq_snag_contractor"
        ),
    )
