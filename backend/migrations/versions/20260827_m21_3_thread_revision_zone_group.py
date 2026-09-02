"""M21.3 — add nullable zone_group to thread_revisions for travel-aware scheduling.

Additive migration: existing rows remain valid with NULL zone_group.
ScheduleService resolves NULL via linked WhatsAppThreadCandidate.zone_group.

Revision ID: 20260827_m21_3_thread_revision_zone_group
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_m21_3_thread_revision_zone_group"
down_revision: str = "20260824_wild04r_phase2_alert_ts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "thread_revisions",
        sa.Column("zone_group", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("thread_revisions", "zone_group")
