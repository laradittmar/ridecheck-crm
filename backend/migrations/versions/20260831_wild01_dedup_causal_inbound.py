"""WILD-01 FINDING-03 — causal inbound identity for outbound dedup.

Adds causal_inbound_wa_message_id to whatsapp_outbound_dedup so that the
rolling-window dedup key becomes (wa_id, message_kind, content_fingerprint,
causal_inbound_wa_message_id) when a causal ID is present.

This allows the gate to let through identical reply text triggered by
different inbound events (e.g. customer re-sends "Hola" after silence),
while still blocking true duplicate sends caused by the same inbound event.

Revision ID: 20260831_wild01_dedup_causal_inbound
Revises: 20260829_m21_4a_attribution
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_wild01_dedup_causal_inbound"
down_revision: str = "20260829_m21_4a_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_outbound_dedup",
        sa.Column("causal_inbound_wa_message_id", sa.String(191), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_outbound_dedup", "causal_inbound_wa_message_id")
