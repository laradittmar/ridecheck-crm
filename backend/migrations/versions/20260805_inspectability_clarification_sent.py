"""Add inspectability_clarification_sent to whatsapp_thread_states — M21.1.2-R1.

Change:
  1. New boolean column ``inspectability_clarification_sent`` on
     ``whatsapp_thread_states``.  Default false.  Tracks whether a non-running
     clarification has been sent for the current conversation turn sequence,
     so the engine can escalate to a human agent on the second unresolved turn
     instead of repeating the same clarification.

Revision ID: 20260805_inspectability_clarification_sent
Revises: 20260629_recipient_lock_rolling_window
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_inspectability_clarification_sent"
down_revision: str = "20260629_recipient_lock_rolling_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "inspectability_clarification_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "inspectability_clarification_sent")
