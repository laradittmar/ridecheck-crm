"""add last_visible_slots to whatsapp_thread_states

Tracks the exact HH:MM slots displayed in the most recent bot offer message.
Used by ordinal slot selection ('el último', 'el primero', etc.) so the engine
selects from the visible list, not the full-day availability list.

Revision ID: 20260618_last_visible_slots
Revises: 20260618_scheduling_context
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_last_visible_slots"
down_revision: str | None = "20260618_scheduling_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("last_visible_slots", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "last_visible_slots")
