"""Add pending_turn_evidence_text to whatsapp_thread_states — M21.2.

Change:
  1. New nullable TEXT column ``pending_turn_evidence_text`` on
     ``whatsapp_thread_states``.  Server default NULL.  Stores the exact
     current_turn_text (joined debounced burst) that produced a pending fuzzy
     vehicle confirmation.  NULL means no pending evidence.

Lifecycle (managed entirely by conversation_engine.py):
  - Written: when fuzzy outcome = CONFIRM, alongside pending_fuzzy_catalog_key.
  - Consumed: on customer acceptance ("sí"), used as source_text for candidate
    creation (year extraction) and for replaying zone detection before the
    routing gate.  Cleared atomically with pending_fuzzy_catalog_key.
  - Cleared: on rejection, on exact-vehicle replacement, and on path clearing
    pending_fuzzy_catalog_key.
  - Not read by field_evidence.py resolver (pending = unconfirmed).

Revision ID: 20260813_pending_turn_evidence_text
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_pending_turn_evidence_text"
down_revision: str = "20260806_pending_fuzzy_catalog_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "pending_turn_evidence_text",
            sa.Text,
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "pending_turn_evidence_text")
