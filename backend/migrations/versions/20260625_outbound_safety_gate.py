"""Outbound safety gate — pre-send audit records, duplicate guard, flood gate.

Adds three columns to whatsapp_messages and a new dedup table so that the
OutboundSafetyGate service can:
  1. Write a durable 'pending' record BEFORE every automated Meta API call.
  2. Enforce a 10-minute window dedup per (thread, normalized-text fingerprint).
  3. Detect and block floods of ≥ 3 automated messages within 60 seconds.
  4. Persist 'blocked' records for suppressed attempts (audit trail).

The existing status check constraint is widened to add 'pending' and 'blocked'.
'pending' was already used by send_thread_text but silently rejected by the DB
constraint (IntegrityError caught → fell back to 'sent' before the Meta call);
the widening makes that path work as intended.

Revision ID: 20260625_outbound_safety_gate
Revises: 20260624_group_default_viaticos
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260625_outbound_safety_gate"
down_revision: str | None = "20260624_group_default_viaticos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Widen the status check constraint on whatsapp_messages.
    op.drop_constraint("ck_whatsapp_messages_status", "whatsapp_messages", type_="check")
    op.create_check_constraint(
        "ck_whatsapp_messages_status",
        "whatsapp_messages",
        "status IN ('received', 'pending', 'sent', 'delivered', 'read', 'failed', 'blocked')",
    )

    # 2. New columns on whatsapp_messages.
    op.add_column(
        "whatsapp_messages",
        sa.Column("automated", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("content_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "whatsapp_messages",
        sa.Column("blocked_reason", sa.Text(), nullable=True),
    )

    # 3. Dedup table — one row per (recipient wa_id, fingerprint, 10-min window).
    #    The UNIQUE(wa_id, content_fingerprint, window_start) constraint is the
    #    atomic guard against concurrent duplicates, keyed by recipient phone ID
    #    (not thread_id) so it works across multiple CRM threads for the same contact.
    op.create_table(
        "whatsapp_outbound_dedup",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("wa_id", sa.String(80), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "window_start",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "wa_id",
            "content_fingerprint",
            "window_start",
            name="uq_outbound_dedup_wa_fp_window",
        ),
    )
    # Index for periodic cleanup (DELETE WHERE window_start < NOW() - 1 day).
    op.create_index(
        "ix_outbound_dedup_window_start",
        "whatsapp_outbound_dedup",
        ["window_start"],
    )
    # Index for the flood gate query: count recent dedup rows per recipient.
    op.create_index(
        "ix_outbound_dedup_wa_id_created",
        "whatsapp_outbound_dedup",
        ["wa_id", "created_at"],
    )

    # 4. Index for whatsapp_messages to support automated-message queries.
    op.create_index(
        "ix_wm_automated_thread_created",
        "whatsapp_messages",
        ["thread_id", "automated", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wm_automated_thread_created", table_name="whatsapp_messages")
    op.drop_index("ix_outbound_dedup_wa_id_created", table_name="whatsapp_outbound_dedup")
    op.drop_index("ix_outbound_dedup_window_start", table_name="whatsapp_outbound_dedup")
    op.drop_table("whatsapp_outbound_dedup")
    op.drop_column("whatsapp_messages", "blocked_reason")
    op.drop_column("whatsapp_messages", "content_fingerprint")
    op.drop_column("whatsapp_messages", "automated")
    op.drop_constraint("ck_whatsapp_messages_status", "whatsapp_messages", type_="check")
    op.create_check_constraint(
        "ck_whatsapp_messages_status",
        "whatsapp_messages",
        "status IN ('received', 'sent', 'delivered', 'read', 'failed')",
    )
