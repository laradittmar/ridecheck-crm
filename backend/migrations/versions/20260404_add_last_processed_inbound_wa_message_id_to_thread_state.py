"""add last_processed_inbound_wa_message_id to whatsapp_thread_states

Revision ID: 20260404_add_last_processed_inbound_wa_message_id_to_thread_state
Revises: 20260330_add_whatsapp_thread_memory_tables
Create Date: 2026-04-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260404_add_last_processed_inbound_wa_message_id_to_thread_state"
down_revision: Union[str, Sequence[str], None] = "20260330_add_whatsapp_thread_memory_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_exists(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(col.get("name") == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_thread_states") and not _column_exists(
        inspector,
        "whatsapp_thread_states",
        "last_processed_inbound_wa_message_id",
    ):
        op.add_column(
            "whatsapp_thread_states",
            sa.Column("last_processed_inbound_wa_message_id", sa.String(length=191), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "whatsapp_thread_states") and _column_exists(
        inspector,
        "whatsapp_thread_states",
        "last_processed_inbound_wa_message_id",
    ):
        op.drop_column("whatsapp_thread_states", "last_processed_inbound_wa_message_id")
