"""WILD-04R cycle boundary fields on whatsapp_thread_states.

Changes:
  1. cycle_reset_pending BOOLEAN NOT NULL DEFAULT FALSE
     One-shot signal set by set_lead_estado() when a human transitions a Lead
     back to CONSULTA_NUEVA from any other estado. CE consumes this exactly once
     at the start of the next inbound turn to execute a cycle reset.

  2. current_cycle_start_message_db_id INTEGER NULL
     DB primary-key id of the earliest inbound WhatsAppMessage in the current
     cycle. Set during cycle reset; used to filter messages to active cycle only.

  3. current_cycle_started_at TIMESTAMPTZ NULL
     DB server-clock created_at of the earliest burst message that opened the
     current cycle. Set during cycle reset; used to filter candidates to
     active cycle only.

Revision ID: 20260824_wild04r_cycle_boundary
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_wild04r_cycle_boundary"
down_revision: str = "20260824_lead_attribution_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "cycle_reset_pending",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "current_cycle_start_message_db_id",
            sa.Integer,
            nullable=True,
        ),
    )
    op.add_column(
        "whatsapp_thread_states",
        sa.Column(
            "current_cycle_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("whatsapp_thread_states", "current_cycle_started_at")
    op.drop_column("whatsapp_thread_states", "current_cycle_start_message_db_id")
    op.drop_column("whatsapp_thread_states", "cycle_reset_pending")
