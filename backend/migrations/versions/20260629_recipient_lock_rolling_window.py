"""Recipient lock + rolling-window dedup — M19.R1.2.

Replaces fixed-bucket dedup with rolling-window + per-recipient lock table.

Changes:
  1. New table ``whatsapp_recipient_locks`` — one row per wa_id while a send
     is in-flight; the presence of a row is the advisory lock that serialises
     concurrent automated sends to the same contact.
  2. Add ``message_kind`` column to ``whatsapp_outbound_dedup`` so the rolling
     dedup SELECT can be scoped to a specific message kind (e.g. 'text',
     'template').
  3. Drop the fixed-bucket UNIQUE constraint
     ``uq_outbound_dedup_wa_fp_window`` (was the old 10-minute-window guard).
  4. Drop the ``window_start`` column — no longer needed with rolling windows.
  5. New composite index ``ix_dedup_wa_kind_fp_created`` covering the rolling
     dedup SELECT pattern:
       SELECT 1 FROM whatsapp_outbound_dedup
       WHERE wa_id = :wa_id
         AND message_kind = :kind
         AND content_fingerprint = :fp
         AND created_at > NOW() - INTERVAL '10 minutes'
       LIMIT 1

Revision ID: 20260629_recipient_lock_rolling_window
Revises: 20260625_outbound_safety_gate
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260629_recipient_lock_rolling_window"
down_revision: str = "20260625_outbound_safety_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New recipient-lock table.
    op.create_table(
        "whatsapp_recipient_locks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("wa_id", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_recipient_locks_wa_id",
        "whatsapp_recipient_locks",
        ["wa_id"],
        unique=True,
    )

    # 2. Add message_kind column to whatsapp_outbound_dedup.
    op.add_column(
        "whatsapp_outbound_dedup",
        sa.Column(
            "message_kind",
            sa.String(20),
            nullable=False,
            server_default="text",
        ),
    )

    # 3. Drop the fixed-bucket UNIQUE constraint.
    op.execute(
        "ALTER TABLE whatsapp_outbound_dedup "
        "DROP CONSTRAINT uq_outbound_dedup_wa_fp_window"
    )

    # 4. Drop window_start column (and its index first).
    op.drop_index("ix_outbound_dedup_window_start", table_name="whatsapp_outbound_dedup")
    op.drop_column("whatsapp_outbound_dedup", "window_start")

    # 5. New composite index for rolling dedup SELECT.
    op.create_index(
        "ix_dedup_wa_kind_fp_created",
        "whatsapp_outbound_dedup",
        ["wa_id", "message_kind", "content_fingerprint", "created_at"],
    )


def downgrade() -> None:
    # 5. Drop rolling-window index.
    op.drop_index("ix_dedup_wa_kind_fp_created", table_name="whatsapp_outbound_dedup")

    # 4. Re-add window_start column and its index.
    op.add_column(
        "whatsapp_outbound_dedup",
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_outbound_dedup_window_start",
        "whatsapp_outbound_dedup",
        ["window_start"],
    )

    # 3. Re-create the fixed-bucket UNIQUE constraint.
    op.execute(
        "ALTER TABLE whatsapp_outbound_dedup "
        "ADD CONSTRAINT uq_outbound_dedup_wa_fp_window "
        "UNIQUE (wa_id, content_fingerprint, window_start)"
    )

    # 2. Drop message_kind column.
    op.drop_column("whatsapp_outbound_dedup", "message_kind")

    # 1. Drop recipient-lock table (index is dropped automatically).
    op.drop_index("ix_recipient_locks_wa_id", table_name="whatsapp_recipient_locks")
    op.drop_table("whatsapp_recipient_locks")
