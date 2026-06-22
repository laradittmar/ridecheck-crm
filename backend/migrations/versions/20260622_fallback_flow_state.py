"""add fallback Flow state columns to whatsapp_thread_states

Tracks whether a chat clarification has been sent and whether a fallback
qualification Flow has been dispatched, to prevent loops and enable
deterministic routing after Flow submit.

Revision ID: 20260622_fallback_flow_state
Revises: 20260622_dock_sud_zone
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_fallback_flow_state"
down_revision: str | None = "20260622_dock_sud_zone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("vehicle_clarification_sent", sa.Boolean(), nullable=False,
                  server_default="false"),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("location_clarification_sent", sa.Boolean(), nullable=False,
                  server_default="false"),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("vehicle_fallback_flow_sent", sa.Boolean(), nullable=False,
                  server_default="false"),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("location_fallback_flow_sent", sa.Boolean(), nullable=False,
                  server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "location_fallback_flow_sent")
    op.drop_column("whatsapp_thread_states", "vehicle_fallback_flow_sent")
    op.drop_column("whatsapp_thread_states", "location_clarification_sent")
    op.drop_column("whatsapp_thread_states", "vehicle_clarification_sent")
