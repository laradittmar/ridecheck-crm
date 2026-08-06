"""Add pending_fuzzy_catalog_key to whatsapp_thread_states — M21.1.4.

Change:
  1. New nullable column ``pending_fuzzy_catalog_key`` VARCHAR(120) on
     ``whatsapp_thread_states``.  Server default NULL.  Stores the catalog
     identity of a fuzzy vehicle proposal awaiting customer confirmation:
     value format is "{marca}||{modelo}" (e.g. "Ford||Ka").
     NULL means no pending proposal.

Revision ID: 20260806_pending_fuzzy_catalog_key
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_pending_fuzzy_catalog_key"
down_revision: str = "20260805_inspectability_clarification_sent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "pending_fuzzy_catalog_key",
            sa.String(120),
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "pending_fuzzy_catalog_key")
