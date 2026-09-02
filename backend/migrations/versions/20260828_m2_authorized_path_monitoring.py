"""M2 — Authorized outbound path monitoring.

Adds:
  1. `security_events` table — persists unauthorized path detections with
     HIGH/BLOCKER severity and triggers email alerts.
  2. Three nullable columns on `whatsapp_messages`:
       path_id        — authorized path ID that created this message (e.g. CE_TEXT)
       deployment_id  — git SHA at time of send (for correlation)
       correlation_id — UUID linking the gate attempt to the message

Revision ID: 20260828_m2_authorized_path_monitoring
Revises: 20260827_m21_3_thread_revision_zone_group
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_m2_authorized_path_monitoring"
down_revision: str | None = "20260827_m21_3_thread_revision_zone_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. security_events table
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("path_id", sa.String(80), nullable=True),
        sa.Column("source_component", sa.String(200), nullable=True),
        sa.Column("wamid", sa.String(191), nullable=True),
        sa.Column("wa_id_hash", sa.String(64), nullable=True),
        sa.Column("deployment_id", sa.String(80), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("alert_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_security_events_detected_at", "security_events", ["detected_at"])
    op.create_index("ix_security_events_event_type", "security_events", ["event_type"])
    op.create_index("ix_security_events_wamid", "security_events", ["wamid"])

    # 2. Path tracking columns on whatsapp_messages
    op.add_column(
        "whatsapp_messages",
        sa.Column("path_id", sa.String(80), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("deployment_id", sa.String(80), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("correlation_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_messages", "correlation_id")
    op.drop_column("whatsapp_messages", "deployment_id")
    op.drop_column("whatsapp_messages", "path_id")
    op.drop_index("ix_security_events_wamid", table_name="security_events")
    op.drop_index("ix_security_events_event_type", table_name="security_events")
    op.drop_index("ix_security_events_detected_at", table_name="security_events")
    op.drop_table("security_events")
