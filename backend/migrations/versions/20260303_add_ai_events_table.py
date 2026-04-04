"""add ai events table

Revision ID: 20260303_add_ai_events_table
Revises: 20260219_fix_whatsapp_nullability
Create Date: 2026-03-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260303_add_ai_events_table"
down_revision: Union[str, Sequence[str], None] = "20260219_fix_whatsapp_nullability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {idx.get("name") for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "ai_events"):
        op.create_table(
            "ai_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("event_type", sa.String(length=50), nullable=False, server_default=sa.text("'inbound_message'")),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("wa_message_id", sa.String(length=191), nullable=False),
            sa.Column("wa_id", sa.String(length=80), nullable=False),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.UniqueConstraint("wa_message_id", name="uq_ai_events_wa_message_id"),
        )

    inspector = sa.inspect(bind)
    if _table_exists(inspector, "ai_events"):
        indexes = _index_names(inspector, "ai_events")
        if "ix_ai_events_thread_id" not in indexes:
            op.create_index("ix_ai_events_thread_id", "ai_events", ["thread_id"], unique=False)
        if "ix_ai_events_wa_message_id" not in indexes:
            op.create_index("ix_ai_events_wa_message_id", "ai_events", ["wa_message_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "ai_events"):
        indexes = _index_names(inspector, "ai_events")
        if "ix_ai_events_wa_message_id" in indexes:
            op.drop_index("ix_ai_events_wa_message_id", table_name="ai_events")
        if "ix_ai_events_thread_id" in indexes:
            op.drop_index("ix_ai_events_thread_id", table_name="ai_events")
        op.drop_table("ai_events")
