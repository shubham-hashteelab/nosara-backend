"""Dedupe inspection_entries and add unique index on (flat_id, room_label, category, item_name)

Revision ID: 003
Revises: 002
Create Date: 2026-04-17

One-time cleanup: lifespan backfill raced across uvicorn workers and produced
duplicate entries for every flat. This migration keeps the row with the
richest data per logical item and drops the rest, then adds a unique index
so no future code path (auto-init, sync push, manual POST) can reintroduce
content-level duplicates.
"""
from alembic import op


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the best row per logical item. Preference order:
    #   1. any real inspection status (non-NA) over NA
    #   2. severity set
    #   3. notes present
    #   4. most recently updated
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY flat_id, room_label, category, item_name
                       ORDER BY (status <> 'NA') DESC,
                                (severity IS NOT NULL) DESC,
                                (notes IS NOT NULL AND notes <> '') DESC,
                                updated_at DESC
                   ) AS rn
            FROM inspection_entries
        )
        DELETE FROM inspection_entries
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1);
        """
    )

    op.create_index(
        "ix_inspection_entries_unique_item",
        "inspection_entries",
        ["flat_id", "room_label", "category", "item_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inspection_entries_unique_item",
        table_name="inspection_entries",
    )
