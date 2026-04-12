"""add system settings table

Revision ID: 20260412_add_system_settings_table
Revises: 20260407_add_current_revision_id_to_thread_state
Create Date: 2026-04-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "20260412_add_system_settings_table"
down_revision: str | None = "20260407_add_current_revision_id_to_thread_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
