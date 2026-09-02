"""M21.4A — Acquisition attribution foundation.

Adds canonical channel + source + CTWA referral capture fields.

Changes:
  WhatsAppThread (whatsapp_threads):
    inbound_channel   VARCHAR(20)  — technical transport channel (WHATSAPP, INSTAGRAM_DM, …)
    ctwa_source_url   VARCHAR(500) — Meta Click-to-WA source URL (first message referral)
    ctwa_source_id    VARCHAR(100) — Meta CTWA source_id
    ctwa_source_type  VARCHAR(40)  — Meta CTWA source_type (ad, post, biz_store, …)

  Lead (leads):
    acq_source        VARCHAR(30)  — canonical acquisition source (INSTAGRAM, GOOGLE, …)
    inbound_channel   VARCHAR(20)  — display copy propagated from Thread by CE (first-write)

Backfill:
  whatsapp_threads.inbound_channel = 'WHATSAPP' for all existing rows.
  No backfill of leads.acq_source (historical canal reliability insufficient per M21.4A-AUDIT).
  No backfill of leads.inbound_channel (CE will populate on first processing).

Revision ID: 20260829_m21_4a_attribution
Revises: 20260828_m2_authorized_path_monitoring
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_m21_4a_attribution"
down_revision: str = "20260828_m2_authorized_path_monitoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── WhatsAppThread additions ─────────────────────────────────────────────
    op.add_column(
        "whatsapp_threads",
        sa.Column("inbound_channel", sa.String(20), nullable=True, server_default=None),
    )
    op.add_column(
        "whatsapp_threads",
        sa.Column("ctwa_source_url", sa.String(500), nullable=True, server_default=None),
    )
    op.add_column(
        "whatsapp_threads",
        sa.Column("ctwa_source_id", sa.String(100), nullable=True, server_default=None),
    )
    op.add_column(
        "whatsapp_threads",
        sa.Column("ctwa_source_type", sa.String(40), nullable=True, server_default=None),
    )

    # ── Lead additions ───────────────────────────────────────────────────────
    op.add_column(
        "leads",
        sa.Column("acq_source", sa.String(30), nullable=True, server_default=None),
    )
    op.add_column(
        "leads",
        sa.Column("inbound_channel", sa.String(20), nullable=True, server_default=None),
    )

    # ── Safe backfill — all current threads are WhatsApp ────────────────────
    op.execute(
        "UPDATE whatsapp_threads SET inbound_channel = 'WHATSAPP' WHERE inbound_channel IS NULL"
    )


def downgrade() -> None:
    op.drop_column("leads", "inbound_channel")
    op.drop_column("leads", "acq_source")
    op.drop_column("whatsapp_threads", "ctwa_source_type")
    op.drop_column("whatsapp_threads", "ctwa_source_id")
    op.drop_column("whatsapp_threads", "ctwa_source_url")
    op.drop_column("whatsapp_threads", "inbound_channel")
