"""scheduling context columns + provisional revision status

Adds to whatsapp_thread_states:
  - active_requested_date  VARCHAR(20)  — the date the user is currently targeting
                                          (survives time-slot rejection so "por la tarde"
                                           can filter alternatives for the same date)
  - last_requested_time    VARCHAR(10)  — last time the user asked for; used to detect
                                          re-insistence on a rejected slot
  - last_offered_slots     TEXT         — JSON list of alternative time strings offered
                                          after a rejection

Adds 'provisional' to thread_revisions.status constraint so the engine can
create a provisional (pre-Flow) revision when a customer is escalated to human.

Revision ID: 20260618_scheduling_context
Revises: 20260617_add_caba_zone
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_scheduling_context"
down_revision: str | None = "20260617_add_caba_zone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("active_requested_date", sa.String(20), nullable=True),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("last_requested_time", sa.String(10), nullable=True),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column("last_offered_slots", sa.Text, nullable=True),
    )

    # Extend thread_revisions.status to allow 'provisional'
    op.execute(
        sa.text(
            "ALTER TABLE thread_revisions DROP CONSTRAINT IF EXISTS ck_thread_revisions_status"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE thread_revisions ADD CONSTRAINT ck_thread_revisions_status "
            "CHECK (status IN ('draft', 'collecting_data', 'booked', 'completed', 'provisional'))"
        )
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "last_offered_slots")
    op.drop_column("whatsapp_thread_states", "last_requested_time")
    op.drop_column("whatsapp_thread_states", "active_requested_date")

    op.execute(
        sa.text(
            "ALTER TABLE thread_revisions DROP CONSTRAINT IF EXISTS ck_thread_revisions_status"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE thread_revisions ADD CONSTRAINT ck_thread_revisions_status "
            "CHECK (status IN ('draft', 'collecting_data', 'booked', 'completed'))"
        )
    )
