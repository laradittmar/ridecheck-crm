"""WILD-04R Phase 2: add unanswered_alert_sent_at to ai_events.

Moves per-turn alert tracking from whatsapp_thread_states to ai_events so
the SLA checker can operate on individual turn events rather than thread state.
The thread-level unanswered_alert_sent_at in whatsapp_thread_states is preserved
for the existing human-handoff alert path.

Revision ID: 20260824_wild04r_phase2_alert_ts
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_wild04r_phase2_alert_ts"
down_revision: str = "20260824_wild04r_ai_events_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_events",
        sa.Column("unanswered_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_events", "unanswered_alert_sent_at")
